# schema.py
import os
import asyncpg
from fastapi import APIRouter, HTTPException

# Creamos un router para montar estas rutas en main.py luego
router = APIRouter()

# Tomamos la URL de conexión desde las variables de entorno
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SCHEMA_NAME = "public"  # si después usas otro schema, lo cambiamos


async def get_db_connection() -> asyncpg.Connection:
    """
    Abre una conexión a la base de datos de Supabase usando SUPABASE_DB_URL.
    """
    if not SUPABASE_DB_URL:
        raise RuntimeError("Falta la variable de entorno SUPABASE_DB_URL.")

    # asyncpg soporta la URL directa (DSN)
    return await asyncpg.connect(SUPABASE_DB_URL)


async def fetch_basic_schema():
    """
    Devuelve un dict con:
    - tablas + columnas
    - relationships (foreign keys globales)
    - incoming_fks / outgoing_fks por tabla
    """
    conn = await get_db_connection()
    try:
        # 1) Columnas
        col_rows = await conn.fetch(
            """
            SELECT 
                table_name,
                column_name,
                data_type,
                is_nullable,
                column_default,
                ordinal_position
            FROM information_schema.columns
            WHERE table_schema = $1
            ORDER BY table_name, ordinal_position;
            """,
            SCHEMA_NAME,
        )

        schema: dict = {
            "schema": SCHEMA_NAME,
            "tables": {},
            "relationships": []
        }

        for r in col_rows:
            table_name = r["table_name"]
            if table_name not in schema["tables"]:
                schema["tables"][table_name] = {
                    "columns": [],
                    # las llenaremos después
                    # "incoming_fks": [],
                    # "outgoing_fks": [],
                }

            schema["tables"][table_name]["columns"].append(
                {
                    "name": r["column_name"],
                    "data_type": r["data_type"],
                    "is_nullable": (r["is_nullable"] == "YES"),
                    "default": r["column_default"],
                    "ordinal_position": r["ordinal_position"],
                }
            )

        # 2) Foreign Keys (relaciones globales)
        fk_rows = await conn.fetch(
            """
            SELECT
                tc.constraint_name,
                tc.table_name       AS table_from,
                kcu.column_name     AS column_from,
                ccu.table_name      AS table_to,
                ccu.column_name     AS column_to
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE 
                tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = $1
            ORDER BY 
                table_from;
            """,
            SCHEMA_NAME,
        )

        for r in fk_rows:
            schema["relationships"].append(
                {
                    "constraint_name": r["constraint_name"],
                    "from_table": r["table_from"],
                    "from_column": r["column_from"],
                    "to_table": r["table_to"],
                    "to_column": r["column_to"],
                }
            )

        # 3) Distribuir relaciones por tabla (incoming/outgoing)
        for rel in schema["relationships"]:
            from_table = rel["from_table"]
            to_table = rel["to_table"]

            # asegurar claves en ambas tablas
            schema["tables"][from_table].setdefault("outgoing_fks", [])
            schema["tables"][to_table].setdefault("incoming_fks", [])

            schema["tables"][from_table]["outgoing_fks"].append(
                {
                    "constraint_name": rel["constraint_name"],
                    "column": rel["from_column"],
                    "references_table": rel["to_table"],
                    "references_column": rel["to_column"],
                }
            )

            schema["tables"][to_table]["incoming_fks"].append(
                {
                    "constraint_name": rel["constraint_name"],
                    "column": rel["to_column"],
                    "referenced_by_table": rel["from_table"],
                    "referenced_by_column": rel["from_column"],
                }
            )

        return schema
    finally:
        await conn.close()



@router.get("/schema")
async def get_schema():
    """
    Endpoint básico para consultar el schema:
    - nombre del schema
    - tablas
    - columnas por tabla
    """
    try:
        schema = await fetch_basic_schema()
        return schema
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener el schema: {e}",
        )
