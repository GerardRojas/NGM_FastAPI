from pathlib import Path
from dotenv import load_dotenv
import os

# ========================================
# Cargar .env desde la raíz del proyecto
# ========================================
BASE_DIR = Path(__file__).resolve().parent.parent  # .../NGM_API
env_path = BASE_DIR / ".env"
load_dotenv(env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.supabase_client import supabase

# ========= ROUTERS EXISTENTES =========
from api.schema import router as schema_router
from api.routers.projects import router as projects_router
from api.routers.pipeline import router as pipeline_router
from api.routers.estimator import router as estimator_router
from api.routers.debug_supabase import router as debug_supabase_router
from api.routers.team import router as team_router
from api.routers.expenses import router as expenses_router
from api.routers.vendors import router as vendors_router
from api.routers.accounts import router as accounts_router
from api.routers.permissions import router as permissions_router
from api.routers.budgets import router as budgets_router
from api.routers.payment_methods import router as payment_methods_router
from api.routers.reconciliations import router as reconciliations_router

# ========= MATERIALS DATABASE =========
from api.routers.materials import router as materials_router
from api.routers.material_categories import router as material_categories_router
from api.routers.material_classes import router as material_classes_router
from api.routers.units import router as units_router
from api.routers.concepts import router as concepts_router
from api.routers.qbo import router as qbo_router
from api.routers.bills import router as bills_router

# ========= NUEVO: AUTH LOGIN ==========
from api.auth import router as auth_router

# ========= ARTURITO: Chat Bot ==========
from api.routers.arturito import router as arturito_router

# ========= MESSAGES: Chat Module ==========
from api.routers.messages import router as messages_router

# ========= NOTIFICATIONS: Push Notifications ==========
from api.routers.notifications import router as notifications_router

# ========= BUDGET ALERTS: Budget Monitoring ==========
from api.routers.budget_alerts import router as budget_alerts_router

# ========= PENDING RECEIPTS: Receipt Management ==========
from api.routers.pending_receipts import router as pending_receipts_router

# ========= PROCESS MANAGER: Code-based Process Documentation ==========
from api.routers.processes import router as processes_router
from api.routers.process_manager import router as process_manager_router


# ========================================
# Inicializar FastAPI
# ========================================
# redirect_slashes=False evita redirects HTTP que causan Mixed Content errors
app = FastAPI(title="NGM HUB API", redirect_slashes=False)


# ========================================
# CORS (permitir acceso desde NGM HUB frontend)
# ========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ngm-hub-frontend.onrender.com",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================================
# Registrar Routers
# ========================================

# Inspector de base de datos
app.include_router(schema_router)

# Módulo de autenticación (LOGIN)
app.include_router(auth_router)

# Módulo de proyectos
app.include_router(projects_router)

# Pipeline Manager (tasks, grouped, etc.)
app.include_router(pipeline_router)

# Cotizador – Base Structure (.ngm generator)
app.include_router(estimator_router)

app.include_router(debug_supabase_router)

app.include_router(team_router)

app.include_router(expenses_router)

# Reconciliations (vincular gastos manuales con facturas QBO)
app.include_router(reconciliations_router)

# QBO Integration (OAuth, sync de gastos, mapeo de proyectos)
app.include_router(qbo_router)

# Budgets (presupuestos de QuickBooks)
app.include_router(budgets_router)

# Vendors, Accounts & Payment Methods (catálogos de expenses)
app.include_router(vendors_router)
app.include_router(accounts_router)
app.include_router(payment_methods_router)

# Bills (metadata de facturas/recibos)
app.include_router(bills_router)

# Permissions (control de acceso basado en roles)
app.include_router(permissions_router)

# Arturito Chat Bot (Google Chat integration)
app.include_router(arturito_router)

# Messages (Chat module - channels, threads, reactions)
app.include_router(messages_router)

# Notifications (Push notification token management)
app.include_router(notifications_router)

# Budget Alerts (automated budget monitoring)
app.include_router(budget_alerts_router)

# Pending Receipts (receipt management for expenses)
app.include_router(pending_receipts_router)

# Materials Database (materiales, categorias, clases, unidades, conceptos)
app.include_router(materials_router)
app.include_router(material_categories_router)
app.include_router(material_classes_router)
app.include_router(units_router)
app.include_router(concepts_router)

# Process Manager (code-based process documentation)
app.include_router(processes_router)

# Process Manager Visual State (shared state for process manager UI)
app.include_router(process_manager_router)

# ========================================
# Root & Healthcheck
# ========================================

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "NGM HUB API running"}

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

@app.get("/departments")
async def get_departments():
    """
    Get list of all departments.
    Used by various modules for department selection dropdowns.
    """
    try:
        response = supabase.table("task_departments").select(
            "department_id, department_name"
        ).order("department_name").execute()
        return {"departments": response.data or []}
    except Exception as e:
        return {"departments": []}
