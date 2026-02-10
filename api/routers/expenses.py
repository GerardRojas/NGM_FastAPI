from fastapi import APIRouter, HTTPException, File, UploadFile, Form, BackgroundTasks
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List
import base64
import os
from openai import OpenAI
import json
import io
from pdf2image import convert_from_bytes
from PIL import Image
import pdfplumber
from concurrent.futures import ThreadPoolExecutor, as_completed
from services.receipt_scanner import (
    extract_text_from_pdf as _extract_text_from_pdf,
    scan_receipt as _scan_receipt_core,
    auto_categorize as _auto_categorize_core,
)

router = APIRouter(prefix="/expenses", tags=["Expenses"])


# ====== MODELOS ======

class ExpenseCreate(BaseModel):
    project: str  # UUID del proyecto
    txn_type: Optional[str] = None  # UUID del tipo de transacción
    TxnDate: Optional[str] = None  # Fecha en formato ISO
    bill_id: Optional[str] = None  # Invoice/Bill number
    vendor_id: Optional[str] = None  # UUID del vendor
    payment_type: Optional[str] = None  # UUID del método de pago
    Amount: Optional[float] = None
    LineDescription: Optional[str] = None
    TxnId_QBO: Optional[str] = None
    LineUID: Optional[str] = None
    show_on_reports: Optional[bool] = None
    coinciliation_status: Optional[bool] = None
    account_id: Optional[str] = None
    created_by: Optional[str] = None


class ExpenseBatchCreate(BaseModel):
    """Modelo para crear múltiples gastos en una sola llamada"""
    expenses: List[ExpenseCreate]


class ExpenseUpdate(BaseModel):
    txn_type: Optional[str] = None
    TxnDate: Optional[str] = None
    bill_id: Optional[str] = None  # Invoice/Bill number
    vendor_id: Optional[str] = None
    payment_type: Optional[str] = None
    Amount: Optional[float] = None
    LineDescription: Optional[str] = None
    TxnId_QBO: Optional[str] = None
    LineUID: Optional[str] = None
    show_on_reports: Optional[bool] = None
    coinciliation_status: Optional[bool] = None
    account_id: Optional[str] = None
    auth_status: Optional[bool] = None
    auth_by: Optional[str] = None
    receipt_url: Optional[str] = None  # URL del recibo/factura en Storage
    status: Optional[str] = None  # 'pending', 'auth', 'review' - for auto-review on field changes
    status_reason: Optional[str] = None  # Reason for status change (used with auto-review)


class ExpenseUpdateItem(BaseModel):
    """Un item para actualización batch - incluye el ID"""
    expense_id: str
    data: ExpenseUpdate


class ExpenseBatchUpdate(BaseModel):
    """Modelo para actualizar múltiples gastos en una sola llamada"""
    updates: List[ExpenseUpdateItem]


# ====== HELPERS ======

def extract_rel_value(row: dict, rel_name: str, field: str):
    """
    Extrae un valor desde una relación embebida de Supabase.
    Soporta tanto dict como list.
    """
    rel = row.get(rel_name)
    if rel is None:
        return None

    if isinstance(rel, list):
        if not rel:
            return None
        rel = rel[0]

    if isinstance(rel, dict):
        return rel.get(field)

    return None


# ====== ENDPOINTS ======

@router.post("", status_code=201)
def create_expense(payload: ExpenseCreate, background_tasks: BackgroundTasks):
    """
    Crea un nuevo gasto
    """
    try:
        data = payload.model_dump(exclude_none=True)

        # Validar project
        if data.get("project"):
            project = supabase.table("projects").select("project_id").eq("project_id", data["project"]).single().execute()
            if not project.data:
                raise HTTPException(status_code=400, detail="Invalid project")

        # Validar txn_type si se proporciona
        if data.get("txn_type"):
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Insertar gasto
        res = supabase.table("expenses_manual_COGS").insert(data).execute()

        # Trigger Daneel auto-auth check for new pending expense
        created = res.data[0] if res.data else {}
        expense_id = created.get("expense_id")
        project_id = created.get("project")
        if expense_id and project_id and created.get("status", "pending") == "pending":
            from api.services.daneel_auto_auth import trigger_auto_auth_check
            background_tasks.add_task(trigger_auto_auth_check, expense_id, project_id)

        return {
            "message": "Expense created",
            "expense": created,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch", status_code=201)
def create_expenses_batch(payload: ExpenseBatchCreate, background_tasks: BackgroundTasks):
    """
    Crea múltiples gastos en una sola operación (bulk insert).
    Mucho más eficiente que crear uno por uno.

    Returns:
        - created: lista de gastos creados exitosamente
        - failed: lista de errores (si los hay)
        - summary: resumen de la operación
    """
    try:
        expenses_data = []
        failed = []

        # Validar project una sola vez (asumimos mismo proyecto para todos)
        project_ids = set()
        for exp in payload.expenses:
            if exp.project:
                project_ids.add(exp.project)

        # Validar que todos los proyectos existan
        for project_id in project_ids:
            project = supabase.table("projects").select("project_id").eq("project_id", project_id).single().execute()
            if not project.data:
                raise HTTPException(status_code=400, detail=f"Invalid project: {project_id}")

        # Preparar datos para inserción bulk
        for idx, exp in enumerate(payload.expenses):
            try:
                data = exp.model_dump(exclude_none=True)
                expenses_data.append(data)
            except Exception as e:
                failed.append({
                    "index": idx,
                    "error": str(e),
                    "data": exp.model_dump() if exp else None
                })

        if not expenses_data:
            raise HTTPException(status_code=400, detail="No valid expenses to create")

        # Inserción bulk - una sola operación a la base de datos
        res = supabase.table("expenses_manual_COGS").insert(expenses_data).execute()

        created_expenses = res.data or []

        # Trigger Daneel auto-auth for each created pending expense
        from api.services.daneel_auto_auth import trigger_auto_auth_check
        for exp in created_expenses:
            exp_id = exp.get("expense_id")
            proj_id = exp.get("project")
            if exp_id and proj_id and exp.get("status", "pending") == "pending":
                background_tasks.add_task(trigger_auto_auth_check, exp_id, proj_id)

        return {
            "message": f"Batch insert completed: {len(created_expenses)} created",
            "created": created_expenses,
            "failed": failed,
            "summary": {
                "total_requested": len(payload.expenses),
                "total_created": len(created_expenses),
                "total_failed": len(failed)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/batch")
def update_expenses_batch(payload: ExpenseBatchUpdate):
    """
    Actualiza múltiples gastos en una sola operación.
    Cada update se procesa individualmente pero en una sola llamada HTTP.

    Returns:
        - updated: lista de gastos actualizados exitosamente
        - failed: lista de errores (si los hay)
        - summary: resumen de la operación
    """
    try:
        updated = []
        failed = []

        for item in payload.updates:
            try:
                # Preparar datos para actualización (excluir None)
                update_data = item.data.model_dump(exclude_none=True)

                if not update_data:
                    failed.append({
                        "expense_id": item.expense_id,
                        "error": "No fields to update"
                    })
                    continue

                # Actualizar el gasto
                res = supabase.table("expenses_manual_COGS").update(update_data).eq(
                    "expense_id", item.expense_id
                ).execute()

                if res.data:
                    updated.append(res.data[0])
                else:
                    failed.append({
                        "expense_id": item.expense_id,
                        "error": "Expense not found or no changes made"
                    })

            except Exception as e:
                failed.append({
                    "expense_id": item.expense_id,
                    "error": str(e)
                })

        return {
            "message": f"Batch update completed: {len(updated)} updated, {len(failed)} failed",
            "updated": updated,
            "failed": failed,
            "summary": {
                "total_requested": len(payload.updates),
                "total_updated": len(updated),
                "total_failed": len(failed)
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_expenses(project: Optional[str] = None, limit: Optional[int] = None):
    """
    Lista todos los gastos, opcionalmente filtrados por project.
    Incluye información de tipo de transacción, proyecto, vendor, etc.
    Si no se especifica limit, devuelve todos los gastos.
    """
    try:
        # Obtener los gastos
        query = supabase.table("expenses_manual_COGS").select("*")

        if project:
            query = query.eq("project", project)

        query = query.order("TxnDate", desc=True)

        # Solo aplicar límite si se especifica
        if limit is not None:
            query = query.limit(limit)

        resp = query.execute()
        raw_expenses = resp.data or []

        # Obtener tipos de transacción
        txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        txn_types_map = {t["TnxType_id"]: t for t in (txn_types_resp.data or [])}

        # Obtener proyectos
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {p["project_id"]: p for p in (projects_resp.data or [])}

        # Obtener vendors
        vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        vendors_map = {v["id"]: v for v in (vendors_resp.data or [])}

        # Enriquecer los gastos con datos relacionados
        expenses = []
        for row in raw_expenses:
            # Agregar nombre de tipo de transacción
            txn = txn_types_map.get(row.get("txn_type"))
            if txn:
                row["txn_type_name"] = txn.get("TnxType_name")

            # Agregar nombre de proyecto
            proj = projects_map.get(row.get("project"))
            if proj:
                row["project_name"] = proj.get("project_name")

            # Agregar nombre de vendor
            vendor = vendors_map.get(row.get("vendor_id"))
            if vendor:
                row["vendor_name"] = vendor.get("vendor_name")

            expenses.append(row)

        return {"data": expenses}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo expenses: {e}")


@router.get("/all")
def list_all_expenses(limit: Optional[int] = 1000):
    """
    Lista todos los gastos de todos los proyectos.
    Incluye información del proyecto en cada gasto.
    Por defecto limita a 1000 gastos para evitar problemas de rendimiento.

    PERFORMANCE: Metadata queries ejecutadas en paralelo con ThreadPoolExecutor
    """
    try:
        # Obtener los gastos primero (necesitamos esto antes de enriquecer)
        query = supabase.table("expenses_manual_COGS").select("*")
        query = query.order("TxnDate", desc=True)

        if limit is not None:
            query = query.limit(limit)

        resp = query.execute()
        raw_expenses = resp.data or []

        if not raw_expenses:
            return {"data": []}

        # Define metadata fetch functions
        def fetch_txn_types():
            return supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()

        def fetch_projects():
            return supabase.table("projects").select("project_id, project_name").execute()

        def fetch_vendors():
            return supabase.table("Vendors").select("id, vendor_name").execute()

        def fetch_payments():
            return supabase.table("paymet_methods").select("id, payment_method_name").execute()

        def fetch_accounts():
            return supabase.table("accounts").select("account_id, Name").execute()

        # Execute all metadata queries in parallel
        txn_types_map = {}
        projects_map = {}
        vendors_map = {}
        payment_map = {}
        accounts_map = {}

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(fetch_txn_types): "txn_types",
                executor.submit(fetch_projects): "projects",
                executor.submit(fetch_vendors): "vendors",
                executor.submit(fetch_payments): "payments",
                executor.submit(fetch_accounts): "accounts",
            }

            for future in as_completed(futures):
                key = futures[future]
                try:
                    result = future.result()
                    data = result.data or []
                    if key == "txn_types":
                        txn_types_map = {t["TnxType_id"]: t for t in data}
                    elif key == "projects":
                        projects_map = {p["project_id"]: p for p in data}
                    elif key == "vendors":
                        vendors_map = {v["id"]: v for v in data}
                    elif key == "payments":
                        payment_map = {p["id"]: p for p in data}
                    elif key == "accounts":
                        accounts_map = {a["account_id"]: a for a in data}
                except Exception as exc:
                    print(f"[EXPENSES ALL] Error fetching {key}: {exc}")

        # Enriquecer cada gasto con nombres
        expenses = []
        for row in raw_expenses:
            txn = txn_types_map.get(row.get("txn_type"))
            proj = projects_map.get(row.get("project"))
            vendor = vendors_map.get(row.get("vendor_id"))
            payment = payment_map.get(row.get("payment_type"))
            account = accounts_map.get(row.get("account_id"))

            row["txn_type_name"] = txn.get("TnxType_name") if txn else None
            row["project_name"] = proj.get("project_name") if proj else None
            row["vendor_name"] = vendor.get("vendor_name") if vendor else None
            row["payment_method_name"] = payment.get("payment_method_name") if payment else None
            row["account_name"] = account.get("Name") if account else None

            expenses.append(row)

        return {"data": expenses}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo all expenses: {e}")


@router.get("/meta")
def get_expenses_meta():
    """
    Devuelve catálogos necesarios para la UI de expenses:
      - txn_types: tipos de transacción
      - projects: proyectos
      - vendors: proveedores
      - payment_methods: métodos de pago
      - accounts: cuentas contables

    PERFORMANCE: Queries ejecutadas en paralelo usando ThreadPoolExecutor
    """
    try:
        # Define query functions
        def fetch_txn_types():
            return supabase.table("txn_types").select("*").order("TnxType_name").execute()

        def fetch_projects():
            return supabase.table("projects").select("project_id, project_name").order("project_name").execute()

        def fetch_vendors():
            return supabase.table("Vendors").select("*").order("vendor_name").execute()

        def fetch_payment_methods():
            return supabase.table("paymet_methods").select("*").execute()

        def fetch_accounts():
            return supabase.table("accounts").select("*").execute()

        # Execute all queries in parallel
        results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(fetch_txn_types): "txn_types",
                executor.submit(fetch_projects): "projects",
                executor.submit(fetch_vendors): "vendors",
                executor.submit(fetch_payment_methods): "payment_methods",
                executor.submit(fetch_accounts): "accounts",
            }

            for future in as_completed(futures):
                key = futures[future]
                try:
                    resp = future.result()
                    results[key] = resp.data or []
                except Exception as exc:
                    print(f"[EXPENSES META] Error fetching {key}: {exc}")
                    results[key] = []

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo meta de expenses: {e}")


# ====== DUPLICATE DETECTION ENDPOINTS ======
# IMPORTANT: These routes MUST be before /{expense_id} to avoid route conflicts

class DismissDuplicateRequest(BaseModel):
    user_id: str  # UUID del usuario
    expense_id_1: str  # UUID del primer expense
    expense_id_2: str  # UUID del segundo expense
    reason: Optional[str] = "not_duplicate"


@router.get("/dismissed-duplicates")
def get_dismissed_duplicates(user_id: str):
    """
    Obtiene todos los pares de duplicados descartados por un usuario.
    """
    try:
        print(f"[DISMISSED] Fetching for user_id: {user_id}")
        result = supabase.table("dismissed_expense_duplicates").select(
            "id, expense_id_1, expense_id_2, dismissed_at, dismissed_reason"
        ).eq("user_id", user_id).order("dismissed_at", desc=True).execute()

        dismissals = result.data or []
        print(f"[DISMISSED] Found {len(dismissals)} dismissals")

        return {
            "data": dismissals,
            "count": len(dismissals)
        }

    except Exception as e:
        import traceback
        print(f"[DISMISSED] ERROR: {str(e)}")
        print(f"[DISMISSED] TRACEBACK:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error getting dismissed duplicates: {str(e)}")


@router.post("/dismissed-duplicates", status_code=201)
def dismiss_duplicate_pair(payload: DismissDuplicateRequest):
    """
    Marca un par de expenses como "no es duplicado".
    """
    try:
        # Ordenar IDs para consistencia
        id1, id2 = sorted([payload.expense_id_1, payload.expense_id_2])

        # Insertar con ON CONFLICT para idempotencia
        result = supabase.table("dismissed_expense_duplicates").upsert({
            "user_id": payload.user_id,
            "expense_id_1": id1,
            "expense_id_2": id2,
            "dismissed_reason": payload.reason
        }, on_conflict="user_id,expense_id_1,expense_id_2").execute()

        return {
            "message": "Duplicate pair dismissed successfully",
            "user_id": payload.user_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error dismissing duplicate pair: {str(e)}")


@router.delete("/dismissed-duplicates/{dismissal_id}")
def reactivate_duplicate_alert(dismissal_id: str, user_id: str):
    """
    Elimina un dismissal para reactivar la alerta de duplicado.
    """
    try:
        supabase.table("dismissed_expense_duplicates").delete().eq("id", dismissal_id).eq("user_id", user_id).execute()
        return {"message": "Duplicate alert reactivated successfully", "dismissal_id": dismissal_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reactivating duplicate alert: {str(e)}")


@router.get("/{expense_id}")
def get_expense(expense_id: str):
    """
    Obtiene un gasto específico por ID.
    Incluye información de transacción, proyecto, vendor, etc.
    """
    try:
        resp = (
            supabase
            .table("expenses_manual_COGS")
            .select("*")
            .eq("expense_id", expense_id)
            .single()
            .execute()
        )

        if not resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        row = resp.data

        # Obtener información de tipo de transacción
        if row.get("txn_type"):
            txn_resp = supabase.table("txn_types").select("*").eq("TnxType_id", row["txn_type"]).single().execute()
            if txn_resp.data:
                row["txn_type_name"] = txn_resp.data.get("TnxType_name")

        # Obtener información de proyecto
        if row.get("project"):
            proj_resp = supabase.table("projects").select("project_name").eq("project_id", row["project"]).single().execute()
            if proj_resp.data:
                row["project_name"] = proj_resp.data.get("project_name")

        # Obtener información de vendor
        if row.get("vendor_id"):
            vendor_resp = supabase.table("Vendors").select("*").eq("id", row["vendor_id"]).single().execute()
            if vendor_resp.data:
                row["vendor_name"] = vendor_resp.data.get("vendor_name")

        return {"data": row}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo expense: {e}")


@router.put("/{expense_id}")
def update_expense(expense_id: str, payload: ExpenseUpdate):
    """
    Actualiza un gasto existente
    """
    try:
        # Verificar que el gasto existe
        existing = supabase.table("expenses_manual_COGS").select("expense_id").eq("expense_id", expense_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Preparar datos para actualizar (solo campos no nulos)
        data = {k: v for k, v in payload.model_dump().items() if v is not None}

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validar txn_type si se está actualizando
        if "txn_type" in data:
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Actualizar
        res = supabase.table("expenses_manual_COGS").update(data).eq("expense_id", expense_id).execute()

        return {
            "message": "Expense updated",
            "expense": res.data[0] if res.data else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{expense_id}")
def patch_expense(expense_id: str, payload: ExpenseUpdate, background_tasks: BackgroundTasks, user_id: Optional[str] = None, change_reason: Optional[str] = None):
    """
    Actualiza parcialmente un gasto existente con logging de cambios.
    Solo se actualizan los campos proporcionados en el body.

    Campos actualizables:
    - TxnDate: fecha de la transacción (formato ISO)
    - txn_type: UUID del tipo de transacción
    - vendor_id: UUID del vendor
    - payment_type: UUID del método de pago
    - account_id: UUID de la cuenta/categoría
    - Amount: monto del gasto
    - LineDescription: descripción del gasto
    - auth_status: estado de autorización (boolean) - DEPRECATED, use /status endpoint
    - auth_by: UUID del usuario que autorizó

    Query params:
    - user_id: UUID of user making changes (required for audit log)
    - change_reason: Optional reason for changes (e.g., "Client correction", "Categorization error")
    """
    try:
        # Verificar que el gasto existe y obtener datos actuales
        existing_resp = supabase.table("expenses_manual_COGS").select("*").eq("expense_id", expense_id).single().execute()
        if not existing_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        existing = existing_resp.data
        current_status = existing.get("status") or ("auth" if existing.get("auth_status") else "pending")

        # Preparar datos para actualizar (solo campos proporcionados)
        data = {k: v for k, v in payload.model_dump().items() if v is not None}

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validar txn_type si se está actualizando
        if "txn_type" in data:
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Validar vendor_id si se está actualizando
        if "vendor_id" in data:
            vendor = supabase.table("Vendors").select("id").eq("id", data["vendor_id"]).single().execute()
            if not vendor.data:
                raise HTTPException(status_code=400, detail="Invalid vendor_id")

        # Validar payment_type si se está actualizando
        if "payment_type" in data:
            payment = supabase.table("paymet_methods").select("id").eq("id", data["payment_type"]).single().execute()
            if not payment.data:
                raise HTTPException(status_code=400, detail="Invalid payment_type")

        # Validar account_id si se está actualizando
        if "account_id" in data:
            account = supabase.table("accounts").select("account_id").eq("account_id", data["account_id"]).single().execute()
            if not account.data:
                raise HTTPException(status_code=400, detail="Invalid account_id")

        # Handle status change if included in payload
        status_reason = data.pop("status_reason", None)
        new_status = data.get("status")
        status_changed = False

        if new_status:
            valid_statuses = ['pending', 'auth', 'review']
            if new_status not in valid_statuses:
                raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

            if new_status != current_status:
                status_changed = True
                # Update auth_status for backwards compatibility
                if new_status == 'auth':
                    data["auth_status"] = True
                    data["auth_by"] = user_id
                elif new_status in ['review', 'pending']:
                    data["auth_status"] = False
                    data["auth_by"] = None

        # Log field changes if expense is in 'review' or 'auth' status
        change_logs = []
        if current_status in ['review', 'auth'] and user_id:
            # Important fields to track
            tracked_fields = ['account_id', 'Amount', 'LineDescription', 'txn_type', 'vendor_id', 'payment_type', 'TxnDate']

            for field in tracked_fields:
                if field in data:
                    old_value = str(existing.get(field)) if existing.get(field) is not None else None
                    new_value = str(data[field]) if data[field] is not None else None

                    if old_value != new_value:
                        change_logs.append({
                            "expense_id": expense_id,
                            "field_name": field,
                            "old_value": old_value,
                            "new_value": new_value,
                            "changed_by": user_id,
                            "expense_status": current_status,
                            "change_reason": change_reason or status_reason or "Manual correction"
                        })

            # Insert change logs
            if change_logs:
                supabase.table("expense_change_log").insert(change_logs).execute()

        # Log status change in expense_status_log
        if status_changed and user_id:
            log_data = {
                "expense_id": expense_id,
                "old_status": current_status,
                "new_status": new_status,
                "changed_by": user_id,
                "reason": status_reason or change_reason or "Field modification",
                "metadata": {"via_patch": True}
            }
            supabase.table("expense_status_log").insert(log_data).execute()

        # Actualizar
        res = supabase.table("expenses_manual_COGS").update(data).eq("expense_id", expense_id).execute()

        # Trigger Daneel re-check when bill_id or receipt_url is updated on a pending expense
        updated_exp = res.data[0] if res.data else {}
        if (updated_exp.get("status", current_status) == "pending"
                and ("bill_id" in data or "receipt_url" in data)):
            project_id = updated_exp.get("project") or existing.get("project")
            if project_id:
                from api.services.daneel_auto_auth import trigger_auto_auth_check
                background_tasks.add_task(trigger_auto_auth_check, expense_id, project_id)

        return {
            "message": "Expense updated successfully",
            "expense": updated_exp,
            "changes_logged": len(change_logs),
            "status_changed": status_changed
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{expense_id}")
def delete_expense(expense_id: str, user_id: Optional[str] = None, delete_reason: Optional[str] = None):
    """
    Elimina un gasto con logging si estaba autorizado.

    Query params:
    - user_id: UUID of user deleting the expense (required for authorized expenses)
    - delete_reason: Reason for deletion (required for authorized expenses)
    """
    try:
        # Verificar que el gasto existe y obtener status
        existing_resp = supabase.table("expenses_manual_COGS").select(
            "expense_id, status, auth_status, Amount, LineDescription, account_id"
        ).eq("expense_id", expense_id).single().execute()

        if not existing_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        existing = existing_resp.data
        current_status = existing.get("status") or ("auth" if existing.get("auth_status") else "pending")

        # If expense is authorized or in review, require user_id and reason
        if current_status in ['auth', 'review']:
            if not user_id:
                raise HTTPException(
                    status_code=400,
                    detail="user_id is required when deleting authorized expenses"
                )
            if not delete_reason:
                raise HTTPException(
                    status_code=400,
                    detail="delete_reason is required when deleting authorized expenses"
                )

            # Log the deletion
            log_data = {
                "expense_id": expense_id,
                "old_status": current_status,
                "new_status": "deleted",
                "changed_by": user_id,
                "reason": delete_reason,
                "metadata": {
                    "deleted": True,
                    "amount": existing.get("Amount"),
                    "description": existing.get("LineDescription"),
                    "account_id": existing.get("account_id")
                }
            }

            supabase.table("expense_status_log").insert(log_data).execute()

        # Eliminar
        supabase.table("expenses_manual_COGS").delete().eq("expense_id", expense_id).execute()

        return {
            "message": "Expense deleted",
            "was_authorized": current_status in ['auth', 'review'],
            "logged": current_status in ['auth', 'review']
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====== STATUS MANAGEMENT ======

class ExpenseStatusUpdate(BaseModel):
    status: str  # 'pending', 'auth', 'review'
    reason: Optional[str] = None
    metadata: Optional[dict] = None


@router.patch("/{expense_id}/status")
def update_expense_status(expense_id: str, payload: ExpenseStatusUpdate, user_id: str, background_tasks: BackgroundTasks):
    """
    Update expense status with audit logging.

    Status flow:
    - pending -> auth (manager approval)
    - auth -> review (flagged by manager/COO/CEO for categorization review)
    - review -> auth (after correction)

    Only manager, COO, or CEO can set status to 'review'.

    Args:
        expense_id: UUID of the expense
        payload: New status and optional reason/metadata
        user_id: UUID of user making the change

    Returns:
        Updated expense with log entry
    """
    try:
        # Validate status
        valid_statuses = ['pending', 'auth', 'review']
        if payload.status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )

        # Get current expense data
        expense_resp = supabase.table("expenses_manual_COGS").select(
            "expense_id, status, auth_status, project"
        ).eq("expense_id", expense_id).single().execute()

        if not expense_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        current_expense = expense_resp.data
        old_status = current_expense.get("status") or ("auth" if current_expense.get("auth_status") else "pending")

        # Permission check for 'review' status
        if payload.status == 'review':
            user_resp = supabase.table("users").select(
                "user_id, rols!users_user_rol_fkey(rol_name)"
            ).eq("user_id", user_id).single().execute()

            if not user_resp.data:
                raise HTTPException(status_code=404, detail="User not found")

            role_info = user_resp.data.get("rols") or {}
            role_name = role_info.get("rol_name", "")

            REVIEW_ALLOWED_ROLES = ["CEO", "COO", "Accounting Manager", "Project Manager"]

            if role_name not in REVIEW_ALLOWED_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"Only {', '.join(REVIEW_ALLOWED_ROLES)} can set status to 'review'"
                )

        # Update expense status
        update_data = {"status": payload.status}

        # Also update legacy auth_status for backwards compatibility
        if payload.status == 'auth':
            update_data["auth_status"] = True
            update_data["auth_by"] = user_id
        elif payload.status == 'pending':
            update_data["auth_status"] = False
            update_data["auth_by"] = None

        supabase.table("expenses_manual_COGS").update(update_data).eq(
            "expense_id", expense_id
        ).execute()

        # Log the status change
        log_data = {
            "expense_id": expense_id,
            "old_status": old_status,
            "new_status": payload.status,
            "changed_by": user_id,
            "reason": payload.reason,
            "metadata": payload.metadata or {}
        }

        supabase.table("expense_status_log").insert(log_data).execute()

        # Trigger budget check when expense is authorized
        if payload.status == 'auth':
            project_id = current_expense.get("project")
            if project_id:
                from api.services.budget_monitor import trigger_project_budget_check
                background_tasks.add_task(trigger_project_budget_check, project_id)

        return {
            "message": "Status updated successfully",
            "expense_id": expense_id,
            "old_status": old_status,
            "new_status": payload.status,
            "logged": True
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating status: {str(e)}")


@router.post("/{expense_id}/soft-delete")
def soft_delete_expense(expense_id: str, user_id: str):
    """
    Soft-delete: cambia el status a 'review' para que un manager confirme la eliminacion.
    Cualquier usuario puede solicitar soft-delete (no requiere rol de manager).

    Query params:
        user_id: UUID del usuario que solicita la eliminacion
    """
    try:
        # Verificar que el gasto existe
        existing_resp = supabase.table("expenses_manual_COGS").select(
            "expense_id, status, auth_status, Amount, LineDescription, account_id"
        ).eq("expense_id", expense_id).single().execute()

        if not existing_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        existing = existing_resp.data
        old_status = existing.get("status") or ("auth" if existing.get("auth_status") else "pending")

        # Si ya esta en review, no hacer nada
        if old_status == "review":
            return {
                "message": "Expense is already in review",
                "expense_id": expense_id,
                "status": "review"
            }

        # Cambiar status a review
        update_data = {
            "status": "review",
            "status_reason": "Deletion requested",
            "auth_status": False,
            "auth_by": None
        }

        supabase.table("expenses_manual_COGS").update(update_data).eq(
            "expense_id", expense_id
        ).execute()

        # Log the soft-delete
        log_data = {
            "expense_id": expense_id,
            "old_status": old_status,
            "new_status": "review",
            "changed_by": user_id,
            "reason": "Deletion requested (soft-delete)",
            "metadata": {
                "soft_delete": True,
                "amount": existing.get("Amount"),
                "description": existing.get("LineDescription"),
                "account_id": existing.get("account_id")
            }
        }

        supabase.table("expense_status_log").insert(log_data).execute()

        return {
            "message": "Expense marked for review (soft-delete)",
            "expense_id": expense_id,
            "old_status": old_status,
            "new_status": "review"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error soft-deleting expense: {str(e)}")


@router.get("/{expense_id}/status-history")
def get_expense_status_history(expense_id: str):
    """
    Get status change history for an expense.

    Returns chronological list of all status changes with user info.
    """
    try:
        # Verify expense exists
        expense_resp = supabase.table("expenses_manual_COGS").select(
            "expense_id"
        ).eq("expense_id", expense_id).single().execute()

        if not expense_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Get status log with user info
        log_resp = supabase.table("expense_status_log").select(
            """
            id,
            old_status,
            new_status,
            changed_at,
            reason,
            metadata,
            changed_by,
            users!expense_status_log_changed_by_fkey(user_name, avatar_color)
            """
        ).eq("expense_id", expense_id).order("changed_at", desc=False).execute()

        history = []
        for entry in (log_resp.data or []):
            user_info = entry.get("users") or {}
            history.append({
                "id": entry["id"],
                "old_status": entry["old_status"],
                "new_status": entry["new_status"],
                "changed_at": entry["changed_at"],
                "reason": entry.get("reason"),
                "metadata": entry.get("metadata", {}),
                "changed_by": {
                    "id": entry.get("changed_by"),
                    "name": user_info.get("user_name"),
                    "avatar_color": user_info.get("avatar_color")
                }
            })

        return {"expense_id": expense_id, "history": history}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting status history: {str(e)}")


@router.get("/{expense_id}/audit-trail")
def get_expense_audit_trail(expense_id: str):
    """
    Get complete audit trail for an expense (status changes + field changes).

    Returns chronological list of all changes with user info.
    Useful for manager review and client accountability.
    """
    try:
        # Verify expense exists
        expense_resp = supabase.table("expenses_manual_COGS").select(
            "expense_id"
        ).eq("expense_id", expense_id).single().execute()

        if not expense_resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Get status changes
        status_resp = supabase.table("expense_status_log").select(
            """
            id,
            old_status,
            new_status,
            changed_at,
            reason,
            metadata,
            changed_by,
            users!expense_status_log_changed_by_fkey(user_name, avatar_color)
            """
        ).eq("expense_id", expense_id).execute()

        # Get field changes (no FK to users, resolve names separately)
        field_resp = supabase.table("expense_change_log").select(
            """
            id,
            field_name,
            old_value,
            new_value,
            changed_at,
            expense_status,
            change_reason,
            changed_by
            """
        ).eq("expense_id", expense_id).execute()

        # Collect unique user IDs from field changes to resolve names in one query
        field_user_ids = set()
        for entry in (field_resp.data or []):
            uid = entry.get("changed_by")
            if uid:
                field_user_ids.add(uid)

        user_lookup = {}
        if field_user_ids:
            users_resp = supabase.table("users").select(
                "user_id, user_name, avatar_color"
            ).in_("user_id", list(field_user_ids)).execute()
            for u in (users_resp.data or []):
                user_lookup[u["user_id"]] = u

        # Combine and sort by date
        all_changes = []

        # Add status changes
        for entry in (status_resp.data or []):
            user_info = entry.get("users") or {}
            all_changes.append({
                "type": "status_change",
                "id": entry["id"],
                "timestamp": entry["changed_at"],
                "old_status": entry["old_status"],
                "new_status": entry["new_status"],
                "reason": entry.get("reason"),
                "metadata": entry.get("metadata", {}),
                "changed_by": {
                    "id": entry.get("changed_by"),
                    "name": user_info.get("user_name"),
                    "avatar_color": user_info.get("avatar_color")
                }
            })

        # Add field changes
        for entry in (field_resp.data or []):
            uid = entry.get("changed_by")
            user_info = user_lookup.get(uid, {})
            all_changes.append({
                "type": "field_change",
                "id": entry["id"],
                "timestamp": entry["changed_at"],
                "field_name": entry["field_name"],
                "old_value": entry["old_value"],
                "new_value": entry["new_value"],
                "expense_status": entry["expense_status"],
                "reason": entry.get("change_reason"),
                "changed_by": {
                    "id": uid,
                    "name": user_info.get("user_name"),
                    "avatar_color": user_info.get("avatar_color")
                }
            })

        # Sort by timestamp
        all_changes.sort(key=lambda x: x["timestamp"] or "")

        return {
            "expense_id": expense_id,
            "audit_trail": all_changes,
            "total_changes": len(all_changes)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting audit trail: {str(e)}")


@router.get("/summary/by-txn-type")
def get_expenses_summary_by_txn_type(project: Optional[str] = None):
    """
    Obtiene un resumen de gastos agrupados por tipo de transacción.
    Opcionalmente filtrado por proyecto.
    """
    try:
        query = supabase.table("expenses_manual_COGS").select("txn_type, Amount")

        if project:
            query = query.eq("project", project)

        resp = query.execute()
        raw_expenses = resp.data or []

        # Obtener todos los tipos de transacción
        txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        txn_types_map = {t["TnxType_id"]: t for t in (txn_types_resp.data or [])}

        # Agrupar por tipo de transacción
        summary = {}
        for expense in raw_expenses:
            txn_id = expense.get("txn_type")
            amount = expense.get("Amount", 0) or 0
            txn = txn_types_map.get(txn_id)

            if txn_id not in summary:
                summary[txn_id] = {
                    "txn_type_id": txn_id,
                    "txn_type_name": txn.get("TnxType_name") if txn else None,
                    "total": 0,
                    "count": 0
                }

            summary[txn_id]["total"] += amount
            summary[txn_id]["count"] += 1

        return {"data": list(summary.values())}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculando resumen: {e}")


@router.get("/summary/by-project")
def get_expenses_summary_by_project():
    """
    Obtiene un resumen de gastos agrupados por proyecto.
    """
    try:
        resp = supabase.table("expenses_manual_COGS").select("project, Amount").execute()
        raw_expenses = resp.data or []

        # Obtener todos los proyectos
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {proj["project_id"]: proj for proj in (projects_resp.data or [])}

        # Agrupar por proyecto
        summary = {}
        for expense in raw_expenses:
            proj_id = expense.get("project")
            amount = expense.get("Amount", 0) or 0
            proj = projects_map.get(proj_id)

            if proj_id not in summary:
                summary[proj_id] = {
                    "project_id": proj_id,
                    "project_name": proj.get("project_name") if proj else None,
                    "total": 0,
                    "count": 0
                }

            summary[proj_id]["total"] += amount
            summary[proj_id]["count"] += 1

        return {"data": list(summary.values())}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculando resumen: {e}")



@router.post("/auto-categorize")
async def auto_categorize_expenses(payload: dict):
    """
    Auto-categorizes expenses using GPT-4 based on construction stage and description.
    Delegates to shared service: services/receipt_scanner.auto_categorize()
    """
    try:
        stage = payload.get("stage")
        expenses = payload.get("expenses", [])

        if not stage or not expenses:
            raise HTTPException(status_code=400, detail="Missing stage or expenses")

        categorizations = _auto_categorize_core(stage=stage, expenses=expenses)

        return {
            "success": True,
            "categorizations": categorizations
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error auto-categorizing expenses: {str(e)}")


# ====== PDF TEXT EXTRACTION HELPER ======
# Delegated to shared service: services/receipt_scanner.extract_text_from_pdf()
extract_text_from_pdf = _extract_text_from_pdf


@router.post("/parse-receipt")
async def parse_receipt(
    file: UploadFile = File(...),
    model: str = Form("fast"),
    correction_context: str = Form(None)
):
    """
    Parsea un recibo/factura usando OpenAI Vision API.
    Delegates to shared service: services/receipt_scanner.scan_receipt()
    """
    import time
    start_time = time.time()

    print(f"[PARSE-RECEIPT] Using model: {model}")
    try:
        # Validate file type
        allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: JPG, PNG, WebP, GIF, PDF. Got: {file.content_type}"
            )

        # Read file bytes
        file_content = await file.read()

        if len(file_content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

        # Parse correction_context from JSON string to dict
        correction_data = None
        if correction_context:
            try:
                correction_data = json.loads(correction_context)
            except json.JSONDecodeError:
                print(f"[PARSE-RECEIPT] WARNING: Invalid correction_context JSON, ignoring")
                correction_data = None

        # Delegate to shared service
        result = _scan_receipt_core(
            file_content=file_content,
            file_type=file.content_type,
            model=model,
            correction_context=correction_data,
        )

        execution_time = round(time.time() - start_time, 2)
        print(f"[PARSE-RECEIPT] COMPLETADO - metodo: {result['extraction_method']}, items: {len(result['expenses'])}, tiempo: {execution_time}s")

        return {
            "success": True,
            "data": {
                "expenses": result["expenses"],
                "tax_summary": result.get("tax_summary"),
                "validation": result.get("validation"),
            },
            "count": len(result["expenses"]),
            "model_used": result["model_used"],
            "extraction_method": result["extraction_method"],
            "execution_time_seconds": execution_time,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        print(f"[PARSE-RECEIPT] ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error parsing receipt: {str(e)}")


# NOTE: Old inline parse-receipt implementation (prompts, vision/text mode,
# correction mode, JSON parsing) has been moved to services/receipt_scanner.py
# as the shared scan_receipt() function. Both this endpoint and the Receipt
# Agent (pending_receipts.py) now call the same service.


@router.get("/pending-authorization/count")
def get_pending_authorization_count(user_id: str):
    """
    Get count of expenses pending authorization for a user.

    Returns count based on user's role permissions:
    - CEO/COO: Can authorize all expenses
    - Project Manager: Can authorize expenses for their assigned projects
    - Other roles: Based on expense authorization permissions

    Args:
        user_id: UUID of the user requesting the count

    Returns:
        - count: Number of expenses pending authorization
        - can_authorize: Whether this user can authorize expenses
        - role: User's role name
    """
    try:
        # Get user info including role
        user_resp = supabase.table("users").select(
            "user_id, user_name, user_rol, rols!users_user_rol_fkey(rol_id, rol_name)"
        ).eq("user_id", user_id).single().execute()

        if not user_resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = user_resp.data
        role_info = user_data.get("rols") or {}
        role_name = role_info.get("rol_name", "")

        # Define roles that can authorize expenses
        AUTHORIZER_ROLES = ["CEO", "COO", "Accounting Manager", "Project Manager"]

        can_authorize = role_name in AUTHORIZER_ROLES

        if not can_authorize:
            return {
                "count": 0,
                "can_authorize": False,
                "role": role_name,
                "message": "User role does not have authorization permissions"
            }

        # Get pending expenses (status = 'pending')
        # For CEO/COO: All pending expenses
        # For Project Manager: Only their assigned projects (future enhancement)

        query = supabase.table("expenses_manual_COGS").select(
            "expense_id", count="exact"
        ).eq("status", "pending")

        # Execute query
        result = query.execute()

        pending_count = result.count if result.count else 0

        return {
            "count": pending_count,
            "can_authorize": True,
            "role": role_name
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting pending authorization count: {str(e)}")


@router.get("/pending-authorization/summary")
def get_pending_authorization_summary(user_id: str):
    """
    Get summary of expenses pending authorization for dashboard display.

    Returns grouped data by project with counts and totals.

    Args:
        user_id: UUID of the user requesting the summary

    Returns:
        - total_count: Total number of pending expenses
        - total_amount: Sum of all pending expense amounts
        - by_project: Breakdown by project
        - can_authorize: Whether this user can authorize expenses
    """
    try:
        # Get user info including role
        user_resp = supabase.table("users").select(
            "user_id, user_name, user_rol, rols!users_user_rol_fkey(rol_id, rol_name)"
        ).eq("user_id", user_id).single().execute()

        if not user_resp.data:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = user_resp.data
        role_info = user_data.get("rols") or {}
        role_name = role_info.get("rol_name", "")

        # Define roles that can authorize expenses
        AUTHORIZER_ROLES = ["CEO", "COO", "Accounting Manager", "Project Manager"]

        can_authorize = role_name in AUTHORIZER_ROLES

        if not can_authorize:
            return {
                "total_count": 0,
                "total_amount": 0,
                "by_project": [],
                "can_authorize": False,
                "role": role_name
            }

        # Get pending expenses with project info
        query = supabase.table("expenses_manual_COGS").select(
            "expense_id, project, Amount, status"
        ).eq("status", "pending")

        result = query.execute()
        expenses = result.data or []

        # Get project names
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {p["project_id"]: p["project_name"] for p in (projects_resp.data or [])}

        # Group by project
        by_project = {}
        total_amount = 0

        for exp in expenses:
            proj_id = exp.get("project")
            amount = exp.get("Amount") or 0
            total_amount += amount

            if proj_id not in by_project:
                by_project[proj_id] = {
                    "project_id": proj_id,
                    "project_name": projects_map.get(proj_id, "Unknown Project"),
                    "count": 0,
                    "amount": 0
                }

            by_project[proj_id]["count"] += 1
            by_project[proj_id]["amount"] += amount

        # Sort by count descending
        project_list = sorted(by_project.values(), key=lambda x: x["count"], reverse=True)

        return {
            "total_count": len(expenses),
            "total_amount": round(total_amount, 2),
            "by_project": project_list[:5],  # Top 5 projects
            "can_authorize": True,
            "role": role_name
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting pending authorization summary: {str(e)}")


@router.get("/metrics/categorization-errors")
def get_categorization_error_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[str] = None
):
    """
    Get metrics on categorization errors based on expenses flagged for review.

    Calculates:
    - Total expenses reviewed
    - Error rate (% flagged as 'review')
    - Breakdown by reason
    - Trend over time

    Args:
        start_date: Filter from this date (ISO format)
        end_date: Filter until this date (ISO format)
        user_id: Optional filter by user who flagged for review

    Returns:
        Metrics object with error rates and trends
    """
    try:
        # Build query for status log
        query = supabase.table("expense_status_log").select(
            "id, expense_id, old_status, new_status, changed_at, reason, metadata, changed_by"
        )

        if start_date:
            query = query.gte("changed_at", start_date)
        if end_date:
            query = query.lte("changed_at", end_date)
        if user_id:
            query = query.eq("changed_by", user_id)

        log_resp = query.execute()
        logs = log_resp.data or []

        # Calculate metrics
        total_reviewed = 0  # Total expenses that went through auth
        flagged_for_review = 0  # Expenses marked as 'review'
        reasons = {}  # Breakdown by reason
        by_month = {}  # Trend by month

        for log in logs:
            new_status = log.get("new_status")
            old_status = log.get("old_status")
            reason = log.get("reason") or "Not specified"
            changed_at = log.get("changed_at", "")

            # Count transitions to auth (approved)
            if new_status == "auth" and old_status == "pending":
                total_reviewed += 1

            # Count transitions to review (flagged)
            if new_status == "review":
                flagged_for_review += 1

                # Count by reason
                if reason not in reasons:
                    reasons[reason] = 0
                reasons[reason] += 1

                # Count by month
                if changed_at:
                    month_key = changed_at[:7]  # YYYY-MM
                    if month_key not in by_month:
                        by_month[month_key] = 0
                    by_month[month_key] += 1

        # Calculate error rate
        error_rate = 0.0
        if total_reviewed > 0:
            error_rate = round((flagged_for_review / total_reviewed) * 100, 2)

        # Sort reasons by count
        reason_list = [
            {"reason": k, "count": v}
            for k, v in sorted(reasons.items(), key=lambda x: x[1], reverse=True)
        ]

        # Sort months chronologically
        trend_list = [
            {"month": k, "count": v}
            for k, v in sorted(by_month.items())
        ]

        return {
            "total_reviewed": total_reviewed,
            "flagged_for_review": flagged_for_review,
            "error_rate_percent": error_rate,
            "by_reason": reason_list,
            "trend_by_month": trend_list,
            "date_range": {
                "start": start_date,
                "end": end_date
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting categorization metrics: {str(e)}")
