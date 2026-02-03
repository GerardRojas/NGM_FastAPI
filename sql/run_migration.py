"""
Script para migrar datos de "Price" (texto) a price_numeric (numérico)
"""
import os
import sys
from supabase import create_client, Client
from dotenv import load_dotenv
import re

# Cargar variables de entorno
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[ERROR] SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no estan configuradas")
    sys.exit(1)

# Crear cliente
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def clean_price(price_text):
    """
    Limpia el texto del precio y lo convierte a float
    Maneja formatos: "123.45", "1,234.56", "$123.45", "$1,234.56"
    """
    if not price_text or not price_text.strip():
        return None

    # Remover espacios, $, y otros símbolos
    cleaned = price_text.strip()
    cleaned = cleaned.replace('$', '')
    cleaned = cleaned.replace(',', '')  # Remover comas
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None

print("=" * 60)
print("MIGRACION DE PRECIOS: Price (texto) -> price_numeric")
print("=" * 60)

try:
    # 1. Cargar todos los materiales que necesitan migración
    print("\n[PASO 1] Cargando materiales a migrar...")
    response = supabase.table("materials").select("*").execute()

    materiales_a_migrar = [
        m for m in response.data
        if m.get("price_numeric") is None
        and m.get("Price")
        and m.get("Price").strip()
    ]

    print(f"  Total a migrar: {len(materiales_a_migrar)}")

    if not materiales_a_migrar:
        print("\n[OK] No hay materiales para migrar")
        sys.exit(0)

    # 2. Procesar cada material
    print("\n[PASO 2] Migrando precios...")
    print("-" * 60)

    exitosos = 0
    fallidos = 0
    errores = []

    for i, material in enumerate(materiales_a_migrar, 1):
        material_id = material.get("ID")
        price_text = material.get("Price")
        price_numeric = clean_price(price_text)

        if price_numeric is None:
            print(f"  [{i}/{len(materiales_a_migrar)}] SKIP {material_id} - No se pudo parsear: '{price_text}'")
            fallidos += 1
            errores.append({"id": material_id, "price": price_text, "error": "No se pudo parsear"})
            continue

        try:
            # Actualizar en Supabase
            supabase.table("materials").update({
                "price_numeric": price_numeric
            }).eq('"ID"', material_id).execute()

            exitosos += 1
            if i % 50 == 0:  # Mostrar progreso cada 50 registros
                print(f"  [{i}/{len(materiales_a_migrar)}] Procesados...")

        except Exception as e:
            print(f"  [{i}/{len(materiales_a_migrar)}] ERROR {material_id} - {str(e)}")
            fallidos += 1
            errores.append({"id": material_id, "price": price_text, "error": str(e)})

    print("\n" + "=" * 60)
    print("[RESUMEN DE MIGRACION]")
    print("=" * 60)
    print(f"  Total procesados:   {len(materiales_a_migrar)}")
    print(f"  Exitosos:           {exitosos}")
    print(f"  Fallidos:           {fallidos}")

    if errores:
        print("\n[ERRORES DETALLADOS]")
        print("-" * 60)
        for err in errores[:10]:  # Mostrar primeros 10 errores
            print(f"  ID: {err['id']} | Price: '{err['price']}' | Error: {err['error']}")
        if len(errores) > 10:
            print(f"  ... y {len(errores) - 10} errores más")

    print("\n" + "=" * 60)
    if fallidos == 0:
        print("[EXITO] Migracion completada sin errores")
    else:
        print(f"[ATENCION] Migracion completada con {fallidos} errores")
    print("=" * 60)

except Exception as e:
    print(f"\n[ERROR FATAL] {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
