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

# Routers existentes
from api.schema import router as schema_router
from api.routers.projects import router as projects_router
from api.routers.pipeline import router as pipeline_router

# Nuevo router del cotizador / estructura base
from api.routers.estimator import router as estimator_router


# ========================================
# Inicializar FastAPI
# ========================================
app = FastAPI(title="NGM HUB API")


# ========================================
# CORS (permitir acceso desde NGM HUB frontend)
# ========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Ajustar a producción después
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========================================
# Registrar Routers
# ========================================

# Inspector de base de datos
app.include_router(schema_router)

# Módulo de proyectos
app.include_router(projects_router)

# Pipeline Manager (tasks, grouped, etc.)
app.include_router(pipeline_router)

# ⚡ NUEVO: Cotizador – Base Structure (.ngm generator)
app.include_router(estimator_router)


# ========================================
# Root
# ========================================
@app.get("/")
def root():
    return {"message": "NGM HUB API running"}
