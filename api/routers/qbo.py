from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List
from datetime import datetime

# Import QBO service functions
from services.qbo_service import (
    get_authorization_url,
    exchange_code_for_tokens,
    get_connection_status,
    disconnect,
    fetch_all_purchases,
    fetch_all_bills,
    fetch_all_vendor_credits,
    fetch_all_journal_entries,
    fetch_project_catalog,
    fetch_accounts_metadata,
    fetch_budgets
)

router = APIRouter(prefix="/qbo", tags=["QBO Integration"])


# ====== OAUTH ENDPOINTS ======

@router.get("/auth")
def qbo_auth(state: Optional[str] = None):
    """
    Returns the QuickBooks OAuth2 authorization URL.
    Redirect the user to this URL to initiate the OAuth flow.
    """
    try:
        auth_url = get_authorization_url(state)
        return {
            "authorization_url": auth_url,
            "message": "Redirect user to authorization_url to connect QuickBooks"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/callback")
async def qbo_callback(code: str, realmId: str, state: Optional[str] = None):
    """
    OAuth2 callback endpoint.
    QuickBooks redirects here after user authorizes the app.
    Exchanges the authorization code for access tokens.
    """
    try:
        result = await exchange_code_for_tokens(code, realmId)

        # Redirect to frontend success page or return JSON
        # For now, return a success page HTML
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>QuickBooks Connected</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .success {{ color: #28a745; }}
                .info {{ color: #666; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <h1 class="success">QuickBooks Connected Successfully!</h1>
            <p>Company: <strong>{result.get('company_name', 'Unknown')}</strong></p>
            <p>Realm ID: <strong>{result.get('realm_id')}</strong></p>
            <p class="info">You can close this window and return to NGM HUB.</p>
            <script>
                // Notify parent window if opened as popup
                if (window.opener) {{
                    window.opener.postMessage({{ type: 'QBO_CONNECTED', data: {result} }}, '*');
                    setTimeout(() => window.close(), 3000);
                }}
            </script>
        </body>
        </html>
        """
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content)

    except Exception as e:
        # Return error page
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Connection Failed</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error {{ color: #dc3545; }}
            </style>
        </head>
        <body>
            <h1 class="error">Connection Failed</h1>
            <p>Error: {str(e)}</p>
            <p><a href="javascript:window.close()">Close Window</a></p>
        </body>
        </html>
        """
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content, status_code=400)


@router.get("/status")
def qbo_status():
    """
    Returns the current QuickBooks connection status.
    Shows all connected companies and token validity.
    """
    try:
        status = get_connection_status()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect/{realm_id}")
def qbo_disconnect(realm_id: str):
    """
    Disconnects a QuickBooks company by removing its tokens.
    """
    try:
        disconnect(realm_id)
        return {"message": f"Disconnected realm_id: {realm_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync/{realm_id}")
async def qbo_sync_expenses(
    realm_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    replace_all: Optional[bool] = False
):
    """
    Syncs expenses from QuickBooks to the local database.

    Fetches all expense-related transactions (Bills, Purchases, VendorCredits, JournalEntries)
    and imports them to qbo_expenses table.

    Parameters:
    - realm_id: The QuickBooks company realm ID
    - start_date: Filter transactions from this date (YYYY-MM-DD)
    - end_date: Filter transactions until this date (YYYY-MM-DD)
    - replace_all: If true, deletes all existing expenses before importing
    """
    try:
        # Fetch project catalog for bucketing
        project_catalog = await fetch_project_catalog(realm_id)
        project_ids = set(project_catalog.keys())

        # Fetch account metadata for enrichment
        accounts_meta = await fetch_accounts_metadata(realm_id)

        # Fetch all transaction types
        all_lines = []

        # Bills
        bills = await fetch_all_bills(realm_id, start_date, end_date)
        all_lines.extend(extract_expense_lines(bills, "Bill", project_ids, project_catalog, accounts_meta))

        # Purchases
        purchases = await fetch_all_purchases(realm_id, start_date, end_date)
        all_lines.extend(extract_expense_lines(purchases, "Purchase", project_ids, project_catalog, accounts_meta))

        # Vendor Credits
        vendor_credits = await fetch_all_vendor_credits(realm_id, start_date, end_date)
        all_lines.extend(extract_expense_lines(vendor_credits, "VendorCredit", project_ids, project_catalog, accounts_meta))

        # Journal Entries
        journal_entries = await fetch_all_journal_entries(realm_id, start_date, end_date)
        all_lines.extend(extract_expense_lines(journal_entries, "JournalEntry", project_ids, project_catalog, accounts_meta))

        # Deduplicate by GlobalLineUID
        seen = set()
        deduped = []
        for line in all_lines:
            uid = line.get("global_line_uid")
            if uid and uid not in seen:
                seen.add(uid)
                deduped.append(line)

        # Import to database
        if replace_all:
            supabase.table("qbo_expenses").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

        # Upsert in batches
        now = datetime.utcnow().isoformat()
        batch_size = 500
        imported_count = 0

        for i in range(0, len(deduped), batch_size):
            batch = deduped[i:i + batch_size]
            for row in batch:
                row["imported_at"] = now

            supabase.table("qbo_expenses").upsert(
                batch,
                on_conflict="global_line_uid"
            ).execute()
            imported_count += len(batch)

        # Create mappings for new projects
        unique_customers = {}
        for line in deduped:
            if line.get("bucket") == "PROJECT" and line.get("qbo_customer_id"):
                cid = line["qbo_customer_id"]
                if cid not in unique_customers:
                    unique_customers[cid] = line.get("qbo_customer_name", "")

        new_mappings = 0
        for qbo_id, qbo_name in unique_customers.items():
            existing = supabase.table("qbo_project_mapping").select("id").eq("qbo_customer_id", qbo_id).execute()
            if not existing.data:
                supabase.table("qbo_project_mapping").insert({
                    "qbo_customer_id": qbo_id,
                    "qbo_customer_name": qbo_name,
                    "auto_matched": False
                }).execute()
                new_mappings += 1

        return {
            "message": "Sync completed",
            "realm_id": realm_id,
            "total_raw_lines": len(all_lines),
            "total_imported": len(deduped),
            "transactions": {
                "bills": len(bills),
                "purchases": len(purchases),
                "vendor_credits": len(vendor_credits),
                "journal_entries": len(journal_entries)
            },
            "new_project_mappings": new_mappings
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def extract_expense_lines(txns, txn_type, project_ids, project_catalog, accounts_meta):
    """
    Extract expense lines from QBO transactions.
    Similar logic to the AppScript qboExtractAllLinesWithBuckets_.
    """
    lines = []
    if not txns:
        return lines

    for txn in txns:
        txn_id = str(txn.get("Id", ""))
        if not txn_id:
            continue

        txn_date = str(txn.get("TxnDate", "") or "")

        # Vendor name
        vendor_name = ""
        if txn.get("EntityRef", {}).get("name"):
            vendor_name = str(txn["EntityRef"]["name"])
        elif txn.get("VendorRef", {}).get("name"):
            vendor_name = str(txn["VendorRef"]["name"])

        payment_type = str(txn.get("PaymentType", "") or "")

        # Header customer (fallback for lines without customer)
        header_customer_id = ""
        header_customer_name = ""
        if txn.get("CustomerRef"):
            header_customer_id = str(txn["CustomerRef"].get("value", "") or "")
            header_customer_name = str(txn["CustomerRef"].get("name", "") or "")

        txn_lines = txn.get("Line", [])
        if not isinstance(txn_lines, list):
            txn_lines = []

        for idx, line in enumerate(txn_lines):
            amount = float(line.get("Amount", 0) or 0)
            description = str(line.get("Description", "") or "")

            detail_type = str(line.get("DetailType", "") or "")
            detail = None

            if detail_type == "AccountBasedExpenseLineDetail":
                detail = line.get("AccountBasedExpenseLineDetail", {})
            elif detail_type == "ItemBasedExpenseLineDetail":
                detail = line.get("ItemBasedExpenseLineDetail", {})
            elif detail_type == "JournalEntryLineDetail":
                detail = line.get("JournalEntryLineDetail", {})

            if not detail:
                continue

            # Effective customer (from line or header)
            line_customer_id = ""
            line_customer_name = ""
            if detail.get("CustomerRef"):
                line_customer_id = str(detail["CustomerRef"].get("value", "") or "")
                line_customer_name = str(detail["CustomerRef"].get("name", "") or "")

            effective_customer_id = line_customer_id or header_customer_id
            effective_customer_name = line_customer_name or header_customer_name

            # Bucket assignment
            bucket = ""
            project_id = ""
            project_name = ""

            if not effective_customer_id:
                bucket = "UNASSIGNED"
            elif effective_customer_id in project_ids:
                bucket = "PROJECT"
                project_id = effective_customer_id
                project_name = project_catalog.get(effective_customer_id, f"Project_{effective_customer_id}")
            else:
                bucket = "NOT_A_PROJECT"

            # Account info
            account_id = ""
            account_name = ""
            if detail.get("AccountRef"):
                account_id = str(detail["AccountRef"].get("value", "") or "")
                account_name = str(detail["AccountRef"].get("name", "") or "")

            # Enrich with account metadata
            account_type = ""
            account_sub_type = ""
            is_cogs = False
            if account_id and account_id in accounts_meta:
                meta = accounts_meta[account_id]
                account_name = account_name or meta.get("Name", "")
                account_type = meta.get("AccountType", "")
                account_sub_type = meta.get("AccountSubType", "")
                is_cogs = (account_type == "Cost of Goods Sold")

            line_id = str(line.get("Id", idx + 1))

            # Sign handling
            sign = 1
            posting_type = ""
            sign_source = "EntityDefault"

            if txn_type == "VendorCredit":
                sign = -1
                sign_source = "EntityDefault"
            elif txn_type == "JournalEntry":
                posting_type = detail.get("PostingType", "")
                if posting_type == "Credit":
                    sign = -1
                sign_source = "PostingType"

            signed_amount = amount * sign

            global_line_uid = f"{txn_type}:{txn_id}:{line_id}"

            lines.append({
                "global_line_uid": global_line_uid,
                "qbo_customer_id": effective_customer_id or None,
                "qbo_customer_name": effective_customer_name or None,
                "bucket": bucket,
                "txn_type": txn_type,
                "txn_id": txn_id,
                "line_id": line_id,
                "txn_date": txn_date or None,
                "vendor_name": vendor_name or None,
                "payment_type": payment_type or None,
                "account_id": account_id or None,
                "account_name": account_name or None,
                "account_type": account_type or None,
                "account_sub_type": account_sub_type or None,
                "is_cogs": is_cogs,
                "amount": amount,
                "sign": sign,
                "signed_amount": signed_amount,
                "line_description": description or detail_type or None
            })

    return lines


# ====== MODELOS ======

class QBOExpenseItem(BaseModel):
    """Un gasto individual de QBO"""
    global_line_uid: str
    qbo_customer_id: Optional[str] = None
    qbo_customer_name: Optional[str] = None
    bucket: Optional[str] = None  # PROJECT, UNASSIGNED, NOT_A_PROJECT
    txn_type: Optional[str] = None
    txn_id: Optional[str] = None
    line_id: Optional[str] = None
    txn_date: Optional[str] = None
    vendor_name: Optional[str] = None
    payment_type: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    account_type: Optional[str] = None
    account_sub_type: Optional[str] = None
    is_cogs: Optional[bool] = False
    amount: Optional[float] = None
    sign: Optional[int] = 1
    signed_amount: Optional[float] = None
    line_description: Optional[str] = None


class QBOImportPayload(BaseModel):
    """Payload para importar gastos de QBO"""
    expenses: List[QBOExpenseItem]
    replace_all: Optional[bool] = False  # Si true, elimina todos y reimporta


class ProjectMappingCreate(BaseModel):
    """Crear un mapeo QBO customer -> NGM project"""
    qbo_customer_id: str
    qbo_customer_name: Optional[str] = None
    ngm_project_id: str


class ProjectMappingUpdate(BaseModel):
    """Actualizar un mapeo"""
    ngm_project_id: Optional[str] = None
    qbo_customer_name: Optional[str] = None


# ====== ENDPOINTS: QBO EXPENSES ======

@router.get("/expenses")
def get_qbo_expenses(
    project: Optional[str] = None,
    qbo_customer_id: Optional[str] = None,
    is_cogs: Optional[bool] = None,
    bucket: Optional[str] = None,
    limit: Optional[int] = None
):
    """
    Obtiene gastos de QBO.

    Filtros:
    - project: UUID del proyecto NGM (usa mapeo para encontrar qbo_customer_id)
    - qbo_customer_id: ID directo del customer/job de QBO
    - is_cogs: true para solo COGS, false para no-COGS
    - bucket: PROJECT, UNASSIGNED, NOT_A_PROJECT
    - limit: límite de resultados
    """
    try:
        query = supabase.table("qbo_expenses").select("*")

        # Si se filtra por proyecto NGM, buscar el mapeo
        if project:
            mapping_resp = supabase.table("qbo_project_mapping") \
                .select("qbo_customer_id") \
                .eq("ngm_project_id", project) \
                .execute()

            if mapping_resp.data:
                qbo_ids = [m["qbo_customer_id"] for m in mapping_resp.data]
                query = query.in_("qbo_customer_id", qbo_ids)
            else:
                # No hay mapeo, retornar vacío
                return {"data": [], "count": 0}

        # Filtro directo por qbo_customer_id
        if qbo_customer_id:
            query = query.eq("qbo_customer_id", qbo_customer_id)

        # Filtro por COGS
        if is_cogs is not None:
            query = query.eq("is_cogs", is_cogs)

        # Filtro por bucket
        if bucket:
            query = query.eq("bucket", bucket)

        # Ordenar por fecha
        query = query.order("txn_date", desc=True)

        # Límite
        if limit:
            query = query.limit(limit)

        resp = query.execute()

        return {
            "data": resp.data or [],
            "count": len(resp.data or [])
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/expenses/import", status_code=201)
def import_qbo_expenses(payload: QBOImportPayload):
    """
    Importa gastos de QBO a la base de datos.

    - Usa global_line_uid como clave única para evitar duplicados
    - Si replace_all=true, elimina todos los gastos existentes primero
    - Usa upsert para actualizar existentes o crear nuevos
    """
    try:
        expenses = payload.expenses

        if not expenses:
            raise HTTPException(status_code=400, detail="No expenses to import")

        # Si replace_all, eliminar todo primero
        if payload.replace_all:
            supabase.table("qbo_expenses").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

        # Preparar datos para inserción
        rows_to_upsert = []
        now = datetime.utcnow().isoformat()

        for exp in expenses:
            row = {
                "global_line_uid": exp.global_line_uid,
                "qbo_customer_id": exp.qbo_customer_id,
                "qbo_customer_name": exp.qbo_customer_name,
                "bucket": exp.bucket,
                "txn_type": exp.txn_type,
                "txn_id": exp.txn_id,
                "line_id": exp.line_id,
                "txn_date": exp.txn_date,
                "vendor_name": exp.vendor_name,
                "payment_type": exp.payment_type,
                "account_id": exp.account_id,
                "account_name": exp.account_name,
                "account_type": exp.account_type,
                "account_sub_type": exp.account_sub_type,
                "is_cogs": exp.is_cogs or (exp.account_type == "Cost of Goods Sold"),
                "amount": exp.amount,
                "sign": exp.sign or 1,
                "signed_amount": exp.signed_amount or (exp.amount * (exp.sign or 1)),
                "line_description": exp.line_description,
                "imported_at": now
            }
            rows_to_upsert.append(row)

        # Upsert en lotes de 500
        batch_size = 500
        inserted_count = 0
        updated_count = 0

        for i in range(0, len(rows_to_upsert), batch_size):
            batch = rows_to_upsert[i:i + batch_size]

            # Upsert usando global_line_uid como clave
            res = supabase.table("qbo_expenses") \
                .upsert(batch, on_conflict="global_line_uid") \
                .execute()

            inserted_count += len(res.data) if res.data else 0

        # Extraer proyectos únicos para auto-crear mapeos
        unique_customers = {}
        for exp in expenses:
            if exp.qbo_customer_id and exp.bucket == "PROJECT":
                if exp.qbo_customer_id not in unique_customers:
                    unique_customers[exp.qbo_customer_id] = exp.qbo_customer_name

        # Crear mapeos para customers que no existen
        new_mappings = 0
        for qbo_id, qbo_name in unique_customers.items():
            existing = supabase.table("qbo_project_mapping") \
                .select("id") \
                .eq("qbo_customer_id", qbo_id) \
                .execute()

            if not existing.data:
                supabase.table("qbo_project_mapping").insert({
                    "qbo_customer_id": qbo_id,
                    "qbo_customer_name": qbo_name,
                    "auto_matched": False
                }).execute()
                new_mappings += 1

        return {
            "message": f"Import completed",
            "total_processed": len(rows_to_upsert),
            "new_mappings_created": new_mappings,
            "unique_projects": len(unique_customers)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/expenses/clear")
def clear_qbo_expenses(qbo_customer_id: Optional[str] = None):
    """
    Elimina gastos de QBO.
    - Sin parámetros: elimina TODOS
    - Con qbo_customer_id: elimina solo los de ese customer
    """
    try:
        query = supabase.table("qbo_expenses").delete()

        if qbo_customer_id:
            query = query.eq("qbo_customer_id", qbo_customer_id)
        else:
            # Eliminar todos (necesita una condición)
            query = query.neq("id", "00000000-0000-0000-0000-000000000000")

        query.execute()

        return {"message": "QBO expenses cleared"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/expenses/stats")
def get_qbo_stats():
    """
    Obtiene estadísticas de los gastos QBO importados.
    """
    try:
        # Total de gastos
        total_resp = supabase.table("qbo_expenses").select("id", count="exact").execute()
        total = total_resp.count or 0

        # Por bucket
        buckets_resp = supabase.table("qbo_expenses") \
            .select("bucket") \
            .execute()

        bucket_counts = {"PROJECT": 0, "UNASSIGNED": 0, "NOT_A_PROJECT": 0}
        for row in (buckets_resp.data or []):
            b = row.get("bucket")
            if b in bucket_counts:
                bucket_counts[b] += 1

        # COGS vs non-COGS
        cogs_resp = supabase.table("qbo_expenses") \
            .select("is_cogs") \
            .execute()

        cogs_count = sum(1 for r in (cogs_resp.data or []) if r.get("is_cogs"))
        non_cogs_count = total - cogs_count

        # Proyectos únicos
        projects_resp = supabase.table("qbo_expenses") \
            .select("qbo_customer_id") \
            .eq("bucket", "PROJECT") \
            .execute()

        unique_projects = len(set(r.get("qbo_customer_id") for r in (projects_resp.data or []) if r.get("qbo_customer_id")))

        return {
            "total_expenses": total,
            "by_bucket": bucket_counts,
            "cogs_expenses": cogs_count,
            "non_cogs_expenses": non_cogs_count,
            "unique_qbo_projects": unique_projects
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====== ENDPOINTS: PROJECT MAPPING ======

@router.get("/mapping")
def get_project_mappings(unmapped_only: Optional[bool] = False):
    """
    Obtiene todos los mapeos QBO customer -> NGM project.

    - unmapped_only=true: solo retorna los que no tienen ngm_project_id
    """
    try:
        query = supabase.table("qbo_project_mapping").select("*")

        if unmapped_only:
            query = query.is_("ngm_project_id", "null")

        query = query.order("qbo_customer_name")

        resp = query.execute()
        mappings = resp.data or []

        # Enriquecer con nombre del proyecto NGM
        if mappings:
            ngm_ids = [m.get("ngm_project_id") for m in mappings if m.get("ngm_project_id")]
            if ngm_ids:
                projects_resp = supabase.table("projects") \
                    .select("project_id, project_name") \
                    .in_("project_id", ngm_ids) \
                    .execute()

                projects_map = {p["project_id"]: p["project_name"] for p in (projects_resp.data or [])}

                for m in mappings:
                    if m.get("ngm_project_id"):
                        m["ngm_project_name"] = projects_map.get(m["ngm_project_id"])

        return {
            "data": mappings,
            "count": len(mappings)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mapping", status_code=201)
def create_project_mapping(payload: ProjectMappingCreate):
    """
    Crea un nuevo mapeo QBO customer -> NGM project.
    """
    try:
        # Verificar que el proyecto NGM existe
        project_check = supabase.table("projects") \
            .select("project_id") \
            .eq("project_id", payload.ngm_project_id) \
            .single() \
            .execute()

        if not project_check.data:
            raise HTTPException(status_code=400, detail="NGM project not found")

        # Verificar si ya existe un mapeo para este qbo_customer_id
        existing = supabase.table("qbo_project_mapping") \
            .select("id") \
            .eq("qbo_customer_id", payload.qbo_customer_id) \
            .execute()

        if existing.data:
            # Actualizar el existente
            res = supabase.table("qbo_project_mapping") \
                .update({
                    "ngm_project_id": payload.ngm_project_id,
                    "qbo_customer_name": payload.qbo_customer_name,
                    "auto_matched": False
                }) \
                .eq("qbo_customer_id", payload.qbo_customer_id) \
                .execute()

            return {
                "message": "Mapping updated",
                "data": res.data[0] if res.data else None
            }

        # Crear nuevo
        res = supabase.table("qbo_project_mapping").insert({
            "qbo_customer_id": payload.qbo_customer_id,
            "qbo_customer_name": payload.qbo_customer_name,
            "ngm_project_id": payload.ngm_project_id,
            "auto_matched": False
        }).execute()

        return {
            "message": "Mapping created",
            "data": res.data[0] if res.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/mapping/{qbo_customer_id}")
def update_project_mapping(qbo_customer_id: str, payload: ProjectMappingUpdate):
    """
    Actualiza un mapeo existente.
    """
    try:
        # Verificar que existe
        existing = supabase.table("qbo_project_mapping") \
            .select("id") \
            .eq("qbo_customer_id", qbo_customer_id) \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Mapping not found")

        # Preparar datos para actualizar
        data = {}
        if payload.ngm_project_id is not None:
            # Verificar que el proyecto existe
            if payload.ngm_project_id:
                project_check = supabase.table("projects") \
                    .select("project_id") \
                    .eq("project_id", payload.ngm_project_id) \
                    .single() \
                    .execute()

                if not project_check.data:
                    raise HTTPException(status_code=400, detail="NGM project not found")

            data["ngm_project_id"] = payload.ngm_project_id

        if payload.qbo_customer_name is not None:
            data["qbo_customer_name"] = payload.qbo_customer_name

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        data["auto_matched"] = False  # Manual update

        res = supabase.table("qbo_project_mapping") \
            .update(data) \
            .eq("qbo_customer_id", qbo_customer_id) \
            .execute()

        return {
            "message": "Mapping updated",
            "data": res.data[0] if res.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/mapping/{qbo_customer_id}")
def delete_project_mapping(qbo_customer_id: str):
    """
    Elimina un mapeo.
    """
    try:
        supabase.table("qbo_project_mapping") \
            .delete() \
            .eq("qbo_customer_id", qbo_customer_id) \
            .execute()

        return {"message": "Mapping deleted"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mapping/auto-match")
def auto_match_projects():
    """
    Intenta hacer match automático entre QBO customers y NGM projects
    basándose en nombres similares.
    """
    try:
        # Obtener mapeos sin NGM project
        unmapped_resp = supabase.table("qbo_project_mapping") \
            .select("*") \
            .is_("ngm_project_id", "null") \
            .execute()

        unmapped = unmapped_resp.data or []

        if not unmapped:
            return {"message": "No unmapped customers", "matched": 0}

        # Obtener todos los proyectos NGM
        projects_resp = supabase.table("projects") \
            .select("project_id, project_name") \
            .execute()

        projects = projects_resp.data or []

        if not projects:
            return {"message": "No NGM projects found", "matched": 0}

        # Crear mapa normalizado de proyectos
        def normalize(s):
            if not s:
                return ""
            return s.lower().strip().replace("-", " ").replace("_", " ")

        projects_normalized = {
            normalize(p["project_name"]): p for p in projects
        }

        matched_count = 0

        for mapping in unmapped:
            qbo_name = mapping.get("qbo_customer_name", "")
            qbo_normalized = normalize(qbo_name)

            # Buscar coincidencia exacta primero
            if qbo_normalized in projects_normalized:
                project = projects_normalized[qbo_normalized]
                supabase.table("qbo_project_mapping") \
                    .update({
                        "ngm_project_id": project["project_id"],
                        "auto_matched": True
                    }) \
                    .eq("qbo_customer_id", mapping["qbo_customer_id"]) \
                    .execute()
                matched_count += 1
                continue

            # Buscar coincidencia parcial (contiene)
            for norm_name, project in projects_normalized.items():
                if norm_name in qbo_normalized or qbo_normalized in norm_name:
                    supabase.table("qbo_project_mapping") \
                        .update({
                            "ngm_project_id": project["project_id"],
                            "auto_matched": True
                        }) \
                        .eq("qbo_customer_id", mapping["qbo_customer_id"]) \
                        .execute()
                    matched_count += 1
                    break

        return {
            "message": f"Auto-match completed",
            "total_unmapped": len(unmapped),
            "matched": matched_count,
            "remaining_unmapped": len(unmapped) - matched_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====== BUDGET SYNC ENDPOINT ======

@router.post("/budgets/sync/{realm_id}")
async def qbo_sync_budgets(
    realm_id: str,
    project_id: str,
    fiscal_year: Optional[str] = None
):
    """
    Syncs budgets from QuickBooks to the local database.

    QuickBooks budgets are divided into 12 monthly amounts per account.
    This endpoint sums all 12 months for each account line.

    Parameters:
    - realm_id: The QuickBooks company realm ID
    - project_id: The NGM project ID to associate the budget with
    - fiscal_year: Optional fiscal year filter (e.g., "2024")
    """
    try:
        # Fetch budgets from QBO
        qbo_budgets = await fetch_budgets(realm_id, fiscal_year)

        if not qbo_budgets:
            return {
                "message": "No budgets found in QuickBooks",
                "realm_id": realm_id,
                "total_imported": 0
            }

        # Fetch accounts metadata for enrichment
        accounts_meta = await fetch_accounts_metadata(realm_id)

        # Process each budget
        imported_records = []
        now = datetime.utcnow().isoformat()

        for budget in qbo_budgets:
            budget_id = str(budget.get("Id", ""))
            budget_name = budget.get("Name", "Unnamed Budget")
            start_date = budget.get("StartDate")
            end_date = budget.get("EndDate")
            is_active = budget.get("Active", True)

            # Extract fiscal year from StartDate if not provided
            budget_year = fiscal_year
            if not budget_year and start_date:
                try:
                    budget_year = start_date.split("-")[0]
                except:
                    budget_year = str(datetime.utcnow().year)

            # Get budget lines - each line has monthly amounts
            budget_details = budget.get("BudgetDetail", [])

            if not budget_details:
                continue

            # Group by account and sum all monthly amounts
            # Key: account_id -> {account_name, total_amount, months_data}
            account_totals = {}

            for detail in budget_details:
                account_ref = detail.get("AccountRef", {})
                account_id = str(account_ref.get("value", ""))
                account_name = account_ref.get("name", "")

                if not account_id:
                    continue

                # Enrich with account metadata
                if account_id in accounts_meta:
                    meta = accounts_meta[account_id]
                    account_name = account_name or meta.get("Name", "")

                # Get the amount for this period
                amount = float(detail.get("Amount", 0) or 0)

                if account_id not in account_totals:
                    account_totals[account_id] = {
                        "account_name": account_name,
                        "total_amount": 0,
                        "line_count": 0
                    }

                account_totals[account_id]["total_amount"] += amount
                account_totals[account_id]["line_count"] += 1

            # Create budget records for each account
            for account_id, data in account_totals.items():
                record = {
                    "ngm_project_id": project_id,
                    "budget_id_qbo": budget_id,
                    "budget_name": budget_name,
                    "year": int(budget_year) if budget_year else None,
                    "account_id": account_id,
                    "account_name": data["account_name"],
                    "amount_sum": data["total_amount"],
                    "lines_count": data["line_count"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "active": is_active,
                    "imported_at": now,
                    "import_source": "qbo_api"
                }
                imported_records.append(record)

        # Delete existing QBO API budgets for this project before importing
        supabase.table("budgets_qbo") \
            .delete() \
            .eq("ngm_project_id", project_id) \
            .eq("import_source", "qbo_api") \
            .execute()

        # Insert new records in batches
        batch_size = 100
        total_inserted = 0

        for i in range(0, len(imported_records), batch_size):
            batch = imported_records[i:i + batch_size]
            supabase.table("budgets_qbo").insert(batch).execute()
            total_inserted += len(batch)

        return {
            "message": "Budget sync completed",
            "realm_id": realm_id,
            "project_id": project_id,
            "qbo_budgets_found": len(qbo_budgets),
            "total_imported": total_inserted,
            "accounts_processed": len(set(r["account_id"] for r in imported_records))
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====== BUDGET MAPPING ENDPOINTS ======

class BudgetMappingCreate(BaseModel):
    """Create a QBO budget -> NGM project mapping"""
    qbo_budget_id: str
    qbo_budget_name: Optional[str] = None
    qbo_fiscal_year: Optional[int] = None
    ngm_project_id: str


class BudgetMappingUpdate(BaseModel):
    """Update a budget mapping"""
    ngm_project_id: Optional[str] = None
    qbo_budget_name: Optional[str] = None


@router.get("/budgets/preview/{realm_id}")
async def preview_qbo_budgets(realm_id: str):
    """
    Preview available budgets from QuickBooks without importing.
    Shows which budgets are mapped and which are unmapped.
    """
    try:
        # Fetch budgets from QBO
        qbo_budgets = await fetch_budgets(realm_id)

        if not qbo_budgets:
            return {
                "data": [],
                "count": 0,
                "message": "No budgets found in QuickBooks"
            }

        # Get existing mappings
        mappings_resp = supabase.table("qbo_budget_mapping").select("*").execute()
        mappings = {m["qbo_budget_id"]: m for m in (mappings_resp.data or [])}

        # Build preview data
        preview = []
        for budget in qbo_budgets:
            budget_id = str(budget.get("Id", ""))
            budget_name = budget.get("Name", "Unnamed Budget")
            start_date = budget.get("StartDate", "")

            # Extract fiscal year
            fiscal_year = None
            if start_date:
                try:
                    fiscal_year = int(start_date.split("-")[0])
                except:
                    pass

            # Check if mapped
            mapping = mappings.get(budget_id)

            preview_item = {
                "qbo_budget_id": budget_id,
                "qbo_budget_name": budget_name,
                "qbo_fiscal_year": fiscal_year,
                "start_date": start_date,
                "end_date": budget.get("EndDate"),
                "active": budget.get("Active", True),
                "is_mapped": mapping is not None,
                "ngm_project_id": mapping.get("ngm_project_id") if mapping else None,
                "auto_matched": mapping.get("auto_matched") if mapping else False
            }
            preview.append(preview_item)

        return {
            "data": preview,
            "count": len(preview),
            "mapped_count": sum(1 for p in preview if p["is_mapped"]),
            "unmapped_count": sum(1 for p in preview if not p["is_mapped"])
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/budgets/mapping")
def get_budget_mappings(unmapped_only: Optional[bool] = False):
    """
    Get all QBO budget -> NGM project mappings.

    - unmapped_only=true: only return unmapped budgets
    """
    try:
        query = supabase.table("qbo_budget_mapping").select("*")

        if unmapped_only:
            query = query.is_("ngm_project_id", "null")

        query = query.order("qbo_budget_name")

        resp = query.execute()
        mappings = resp.data or []

        # Enrich with NGM project names
        if mappings:
            ngm_ids = [m.get("ngm_project_id") for m in mappings if m.get("ngm_project_id")]
            if ngm_ids:
                projects_resp = supabase.table("projects") \
                    .select("project_id, project_name") \
                    .in_("project_id", ngm_ids) \
                    .execute()

                projects_map = {p["project_id"]: p["project_name"] for p in (projects_resp.data or [])}

                for m in mappings:
                    if m.get("ngm_project_id"):
                        m["ngm_project_name"] = projects_map.get(m["ngm_project_id"])

        return {
            "data": mappings,
            "count": len(mappings)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/budgets/mapping", status_code=201)
def create_budget_mapping(payload: BudgetMappingCreate):
    """
    Create a new QBO budget -> NGM project mapping.
    """
    try:
        # Verify NGM project exists
        project_check = supabase.table("projects") \
            .select("project_id") \
            .eq("project_id", payload.ngm_project_id) \
            .single() \
            .execute()

        if not project_check.data:
            raise HTTPException(status_code=400, detail="NGM project not found")

        # Check if mapping already exists
        existing = supabase.table("qbo_budget_mapping") \
            .select("id") \
            .eq("qbo_budget_id", payload.qbo_budget_id) \
            .execute()

        if existing.data:
            # Update existing
            res = supabase.table("qbo_budget_mapping") \
                .update({
                    "ngm_project_id": payload.ngm_project_id,
                    "qbo_budget_name": payload.qbo_budget_name,
                    "qbo_fiscal_year": payload.qbo_fiscal_year,
                    "auto_matched": False
                }) \
                .eq("qbo_budget_id", payload.qbo_budget_id) \
                .execute()

            return {
                "message": "Mapping updated",
                "data": res.data[0] if res.data else None
            }

        # Create new
        res = supabase.table("qbo_budget_mapping").insert({
            "qbo_budget_id": payload.qbo_budget_id,
            "qbo_budget_name": payload.qbo_budget_name,
            "qbo_fiscal_year": payload.qbo_fiscal_year,
            "ngm_project_id": payload.ngm_project_id,
            "auto_matched": False
        }).execute()

        return {
            "message": "Mapping created",
            "data": res.data[0] if res.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/budgets/mapping/{qbo_budget_id}")
def update_budget_mapping(qbo_budget_id: str, payload: BudgetMappingUpdate):
    """
    Update an existing budget mapping.
    """
    try:
        # Check if exists
        existing = supabase.table("qbo_budget_mapping") \
            .select("id") \
            .eq("qbo_budget_id", qbo_budget_id) \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Mapping not found")

        # Prepare update data
        data = {}
        if payload.ngm_project_id is not None:
            if payload.ngm_project_id:
                # Verify project exists
                project_check = supabase.table("projects") \
                    .select("project_id") \
                    .eq("project_id", payload.ngm_project_id) \
                    .single() \
                    .execute()

                if not project_check.data:
                    raise HTTPException(status_code=400, detail="NGM project not found")

            data["ngm_project_id"] = payload.ngm_project_id

        if payload.qbo_budget_name is not None:
            data["qbo_budget_name"] = payload.qbo_budget_name

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        data["auto_matched"] = False  # Manual update

        res = supabase.table("qbo_budget_mapping") \
            .update(data) \
            .eq("qbo_budget_id", qbo_budget_id) \
            .execute()

        return {
            "message": "Mapping updated",
            "data": res.data[0] if res.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/budgets/mapping/{qbo_budget_id}")
def delete_budget_mapping(qbo_budget_id: str):
    """
    Delete a budget mapping.
    """
    try:
        supabase.table("qbo_budget_mapping") \
            .delete() \
            .eq("qbo_budget_id", qbo_budget_id) \
            .execute()

        return {"message": "Mapping deleted"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/budgets/mapping/auto-match")
async def auto_match_budgets(realm_id: str):
    """
    Attempt to automatically match QBO budgets to NGM projects by name similarity.
    """
    try:
        # Fetch budgets from QBO
        qbo_budgets = await fetch_budgets(realm_id)

        if not qbo_budgets:
            return {"message": "No budgets in QBO", "matched": 0}

        # Get all NGM projects
        projects_resp = supabase.table("projects") \
            .select("project_id, project_name") \
            .execute()

        projects = projects_resp.data or []

        if not projects:
            return {"message": "No NGM projects found", "matched": 0}

        # Normalize function
        def normalize(s):
            if not s:
                return ""
            return s.lower().strip().replace("-", " ").replace("_", " ")

        # Create normalized project map
        projects_normalized = {
            normalize(p["project_name"]): p for p in projects
        }

        matched_count = 0
        created_count = 0

        for budget in qbo_budgets:
            budget_id = str(budget.get("Id", ""))
            budget_name = budget.get("Name", "")
            start_date = budget.get("StartDate", "")

            if not budget_id:
                continue

            # Extract fiscal year
            fiscal_year = None
            if start_date:
                try:
                    fiscal_year = int(start_date.split("-")[0])
                except:
                    pass

            budget_normalized = normalize(budget_name)

            # Try exact match first
            matched_project = None
            if budget_normalized in projects_normalized:
                matched_project = projects_normalized[budget_normalized]
            else:
                # Try partial match
                for norm_name, project in projects_normalized.items():
                    if norm_name in budget_normalized or budget_normalized in norm_name:
                        matched_project = project
                        break

            # Check if mapping exists
            existing = supabase.table("qbo_budget_mapping") \
                .select("id, ngm_project_id") \
                .eq("qbo_budget_id", budget_id) \
                .execute()

            if existing.data:
                # Already has mapping - only update if currently unmapped and we found a match
                if not existing.data[0].get("ngm_project_id") and matched_project:
                    supabase.table("qbo_budget_mapping") \
                        .update({
                            "ngm_project_id": matched_project["project_id"],
                            "auto_matched": True
                        }) \
                        .eq("qbo_budget_id", budget_id) \
                        .execute()
                    matched_count += 1
            else:
                # Create new mapping
                supabase.table("qbo_budget_mapping").insert({
                    "qbo_budget_id": budget_id,
                    "qbo_budget_name": budget_name,
                    "qbo_fiscal_year": fiscal_year,
                    "ngm_project_id": matched_project["project_id"] if matched_project else None,
                    "auto_matched": matched_project is not None
                }).execute()
                created_count += 1
                if matched_project:
                    matched_count += 1

        return {
            "message": "Auto-match completed",
            "total_budgets": len(qbo_budgets),
            "new_mappings_created": created_count,
            "matched": matched_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/budgets/sync-mapped/{realm_id}")
async def sync_mapped_budgets(realm_id: str):
    """
    Sync budgets from QBO using the established mappings.
    Only imports budgets that have been mapped to NGM projects.
    """
    try:
        # Fetch budgets from QBO
        qbo_budgets = await fetch_budgets(realm_id)

        if not qbo_budgets:
            return {
                "message": "No budgets found in QuickBooks",
                "total_imported": 0
            }

        # Get mappings
        mappings_resp = supabase.table("qbo_budget_mapping") \
            .select("*") \
            .not_.is_("ngm_project_id", "null") \
            .execute()

        mappings = {m["qbo_budget_id"]: m for m in (mappings_resp.data or [])}

        if not mappings:
            return {
                "message": "No mapped budgets found. Please map budgets to projects first.",
                "total_imported": 0
            }

        # Fetch accounts metadata
        accounts_meta = await fetch_accounts_metadata(realm_id)

        imported_records = []
        now = datetime.utcnow().isoformat()

        for budget in qbo_budgets:
            budget_id = str(budget.get("Id", ""))

            # Skip if not mapped
            if budget_id not in mappings:
                continue

            mapping = mappings[budget_id]
            project_id = mapping["ngm_project_id"]

            budget_name = budget.get("Name", "Unnamed Budget")
            start_date = budget.get("StartDate")
            end_date = budget.get("EndDate")
            is_active = budget.get("Active", True)

            # Extract fiscal year
            budget_year = None
            if start_date:
                try:
                    budget_year = int(start_date.split("-")[0])
                except:
                    pass

            budget_details = budget.get("BudgetDetail", [])
            if not budget_details:
                continue

            # Sum by account
            account_totals = {}
            for detail in budget_details:
                account_ref = detail.get("AccountRef", {})
                account_id = str(account_ref.get("value", ""))
                account_name = account_ref.get("name", "")

                if not account_id:
                    continue

                if account_id in accounts_meta:
                    meta = accounts_meta[account_id]
                    account_name = account_name or meta.get("Name", "")

                amount = float(detail.get("Amount", 0) or 0)

                if account_id not in account_totals:
                    account_totals[account_id] = {
                        "account_name": account_name,
                        "total_amount": 0,
                        "line_count": 0
                    }

                account_totals[account_id]["total_amount"] += amount
                account_totals[account_id]["line_count"] += 1

            # Create records
            for account_id, data in account_totals.items():
                record = {
                    "ngm_project_id": project_id,
                    "budget_id_qbo": budget_id,
                    "budget_name": budget_name,
                    "year": budget_year,
                    "account_id": account_id,
                    "account_name": data["account_name"],
                    "amount_sum": data["total_amount"],
                    "lines_count": data["line_count"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "active": is_active,
                    "imported_at": now,
                    "import_source": "qbo_api"
                }
                imported_records.append(record)

        if not imported_records:
            return {
                "message": "No budget data to import",
                "total_imported": 0
            }

        # Delete existing QBO API budgets for mapped projects
        project_ids = list(set(r["ngm_project_id"] for r in imported_records))
        for pid in project_ids:
            supabase.table("budgets_qbo") \
                .delete() \
                .eq("ngm_project_id", pid) \
                .eq("import_source", "qbo_api") \
                .execute()

        # Insert
        batch_size = 100
        total_inserted = 0
        for i in range(0, len(imported_records), batch_size):
            batch = imported_records[i:i + batch_size]
            supabase.table("budgets_qbo").insert(batch).execute()
            total_inserted += len(batch)

        return {
            "message": "Sync completed using mappings",
            "total_imported": total_inserted,
            "projects_updated": len(project_ids),
            "budgets_synced": len(mappings)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
