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
from api.routers.qbo import router as qbo_router
from api.routers.bills import router as bills_router

# ========= NUEVO: AUTH LOGIN ==========
from api.auth import router as auth_router

# ========= ARTURITO: Chat Bot ==========
from api.routers.arturito import router as arturito_router

# ========= MESSAGES: Chat Module ==========
from api.routers.messages import router as messages_router


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

# ========================================
# Root & Healthcheck
# ========================================

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "NGM HUB API running"}

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
