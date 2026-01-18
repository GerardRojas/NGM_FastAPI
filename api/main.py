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

# ========= NUEVO: AUTH LOGIN ==========
from api.auth import router as auth_router


# ========================================
# Inicializar FastAPI
# ========================================
app = FastAPI(title="NGM HUB API")


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

# ========================================
# Root & Healthcheck
# ========================================

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "NGM HUB API running"}

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
