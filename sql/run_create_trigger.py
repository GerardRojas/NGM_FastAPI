"""
Script para crear el trigger de sincronización de precios en Supabase
"""
import os
import sys
from supabase import create_client, Client
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[ERROR] SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no estan configuradas")
    sys.exit(1)

# Crear cliente
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("=" * 60)
print("CREACION DE TRIGGER: sync_price_to_numeric")
print("=" * 60)

# Leer el archivo SQL
sql_file = os.path.join(os.path.dirname(__file__), "create_trigger.sql")
with open(sql_file, 'r', encoding='utf-8') as f:
    sql_content = f.read()

try:
    print("\n[PASO 1] Ejecutando SQL para crear función y trigger...")
    print("-" * 60)

    # Ejecutar SQL usando rpc con el método directo de Supabase
    # Nota: Supabase Python client no soporta SQL directo, así que usaremos la REST API
    import requests

    # URL para ejecutar SQL directo (usando PostgREST)
    url = f"{SUPABASE_URL}/rest/v1/rpc"

    # Header con la key
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    print("  NOTA: El cliente Python de Supabase no soporta SQL directo.")
    print("  Por favor, ejecuta el archivo create_trigger.sql manualmente en:")
    print("  1. Ve a Supabase Dashboard > SQL Editor")
    print(f"  2. Copia y pega el contenido de: {sql_file}")
    print("  3. Ejecuta el script")
    print("\n  O usa psql/pgAdmin para conectarte directamente a Postgres.")

    print("\n" + "=" * 60)
    print("[INFO] Instrucciones para ejecutar el trigger mostradas")
    print("=" * 60)

except Exception as e:
    print(f"\n[ERROR] {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
