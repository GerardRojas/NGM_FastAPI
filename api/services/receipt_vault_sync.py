# api/services/receipt_vault_sync.py
# ============================================================================
# Bill receipt -> Vault "Receipts" folder sync
# ----------------------------------------------------------------------------
# A bill's receipt image (bills.receipt_url) is shared by all its line-item
# expenses. This copies it ONCE into the bill's project Vault "Receipts" folder
# and tags the vault_files row with source_bill_id, so there is exactly one
# vault file per bill (enforced by uq_vault_files_project_bill from
# sql/receipts_to_vault_phase0.sql). Idempotent and safe to re-run.
# ============================================================================

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from api.supabase_client import supabase
from api.services import vault_service

logger = logging.getLogger(__name__)

# Buckets a receipt_url may live in (same set the expenses router validates against).
_KNOWN_BUCKETS = ("expenses-receipts", "pending-expenses", "vault")

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _parse_storage_url(url: str):
    """Return (bucket, object_path) for a Supabase public storage URL, else None.
    Mirrors expenses._validate_storage_url's marker parsing; path is URL-decoded."""
    if not url or not isinstance(url, str):
        return None
    clean = url.split("#")[0]
    for bucket in _KNOWN_BUCKETS:
        marker = f"/object/public/{bucket}/"
        if marker in clean:
            path = clean.split(marker, 1)[1].split("?")[0]
            return bucket, unquote(path)
    return None


def _mime_from_path(path: str) -> str:
    lower = path.lower()
    for ext, mime in _MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


def _ext_from_path(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def _projects_for_bill(bill_id: str) -> Dict[str, int]:
    """Distinct projects (with line-item counts) a bill's expenses belong to.
    Bills have no project_id of their own, so this is derived from the expenses."""
    rows = (
        supabase.table("expenses_manual_COGS")
        .select("project")
        .eq("bill_id", bill_id)
        .execute()
        .data
    ) or []
    counts: Dict[str, int] = {}
    for r in rows:
        pid = r.get("project")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _target_projects_for_bill(bill_id: str, status: Optional[str]) -> List[str]:
    """Which project Receipts folders the receipt should land in. Split bills fan
    out to every project their expenses touch; others use the dominant project."""
    counts = _projects_for_bill(bill_id)
    if not counts:
        return []
    if (status or "").strip().lower() == "split":
        return list(counts.keys())
    return [max(counts, key=counts.get)]


def _sync_one_project(project_id: str, bill_id: str, data: bytes,
                      file_hash: str, mime: str, filename: str) -> Dict[str, Any]:
    """Ensure exactly one vault file for (project, bill). create / update / exists."""
    existing = (
        supabase.table("vault_files")
        .select("id, file_hash")
        .eq("project_id", project_id)
        .eq("source_bill_id", bill_id)
        .eq("is_deleted", False)
        .limit(1)
        .execute()
        .data
    ) or []

    if existing:
        vf = existing[0]
        if vf.get("file_hash") == file_hash:
            return {"project_id": project_id, "status": "exists", "vault_file_id": vf["id"]}
        # Receipt image changed -> add a new version on the same vault file.
        vault_service.create_version(
            file_id=vf["id"], file_content=data, filename=filename,
            content_type=mime, user_id=None, comment="Updated from bill receipt",
        )
        return {"project_id": project_id, "status": "updated", "vault_file_id": vf["id"]}

    rec = vault_service.save_to_project_folder(
        project_id=project_id, folder_name="Receipts",
        file_content=data, filename=filename, content_type=mime,
    )
    if not rec or not rec.get("id"):
        return {"project_id": project_id, "status": "error", "reason": "vault upload failed"}
    supabase.table("vault_files").update(
        {"source_bill_id": bill_id}
    ).eq("id", rec["id"]).execute()
    return {"project_id": project_id, "status": "created", "vault_file_id": rec["id"]}


def sync_bill_receipt_to_vault(bill_id: str) -> Dict[str, Any]:
    """Ensure the bill's receipt exists once per target-project Vault Receipts
    folder. Split bills fan out to every project they touch. Never raises.
    Returns {status: created|updated|exists|skipped|error, projects: [...]}."""
    try:
        bill_rows = (
            supabase.table("bills")
            .select("bill_id, receipt_url, status")
            .eq("bill_id", bill_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not bill_rows:
            return {"status": "skipped", "bill_id": bill_id, "reason": "bill not found"}

        receipt_url = (bill_rows[0].get("receipt_url") or "").strip()
        if not receipt_url:
            return {"status": "skipped", "bill_id": bill_id, "reason": "no receipt_url"}

        targets = _target_projects_for_bill(bill_id, bill_rows[0].get("status"))
        if not targets:
            return {"status": "skipped", "bill_id": bill_id, "reason": "no project (no expenses)"}

        parsed = _parse_storage_url(receipt_url)
        if not parsed:
            return {"status": "error", "bill_id": bill_id, "reason": "unrecognized receipt_url"}
        bucket, object_path = parsed

        try:
            data = supabase.storage.from_(bucket).download(object_path)
        except Exception as e:
            return {"status": "error", "bill_id": bill_id, "reason": f"download failed: {e}"}
        if not data:
            return {"status": "error", "bill_id": bill_id, "reason": "empty file"}

        file_hash = vault_service._compute_hash(data)
        mime = _mime_from_path(object_path)
        ext = _ext_from_path(object_path)
        filename = f"Bill {bill_id}{ext}".strip()

        results = [
            _sync_one_project(pid, bill_id, data, file_hash, mime, filename)
            for pid in targets
        ]
        statuses = [r["status"] for r in results]
        # Roll up to one status for the caller (worst-/most-notable-first).
        if any(s == "error" for s in statuses):
            overall = "error"
        elif any(s == "created" for s in statuses):
            overall = "created"
        elif any(s == "updated" for s in statuses):
            overall = "updated"
        else:
            overall = "exists"
        return {"status": overall, "bill_id": bill_id, "projects": results}
    except Exception as e:
        logger.warning("[receipt-vault-sync] %s failed: %s", bill_id, e)
        return {"status": "error", "bill_id": bill_id, "reason": str(e)}


def backfill_bill_receipts_to_vault(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Sync every bill that has a receipt_url. Optionally scope to one project
    (matched by the bill's expenses). Returns per-status counts + details."""
    bills = (
        supabase.table("bills")
        .select("bill_id, receipt_url")
        .not_.is_("receipt_url", "null")
        .execute()
        .data
    ) or []

    allowed_bill_ids: Optional[set] = None
    if project_id:
        exp = (
            supabase.table("expenses_manual_COGS")
            .select("bill_id")
            .eq("project", project_id)
            .not_.is_("bill_id", "null")
            .execute()
            .data
        ) or []
        allowed_bill_ids = {(e.get("bill_id") or "").strip() for e in exp if e.get("bill_id")}

    summary = {"created": 0, "updated": 0, "exists": 0, "skipped": 0, "error": 0}
    errors: List[Dict[str, Any]] = []
    for b in bills:
        bid = (b.get("bill_id") or "").strip()
        if not bid:
            continue
        if allowed_bill_ids is not None and bid not in allowed_bill_ids:
            continue
        res = sync_bill_receipt_to_vault(bid)
        status = res.get("status", "error")
        summary[status] = summary.get(status, 0) + 1
        if status == "error":
            errors.append(res)

    return {"summary": summary, "errors": errors[:50]}
