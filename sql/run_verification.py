"""
Script para verificar el estado de price_numeric en la tabla materials
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
    print("❌ Error: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas")
    sys.exit(1)

# Crear cliente
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("=" * 60)
print("VERIFICACION DE price_numeric EN TABLA materials")
print("=" * 60)

try:
    # Verificacion 1: Contar registros con problemas
    print("\n[ESTADISTICAS] 1. Estadisticas generales:")
    print("-" * 60)

    response = supabase.table("materials").select("*").execute()
    total = len(response.data)

    con_price_numeric = sum(1 for m in response.data if m.get("price_numeric") is not None)
    sin_price_numeric = total - con_price_numeric
    con_price_texto = sum(1 for m in response.data if m.get("Price") and m.get("Price").strip())

    print(f"  Total materiales:        {total}")
    print(f"  Con price_numeric:       {con_price_numeric}")
    print(f"  Sin price_numeric:       {sin_price_numeric}")
    print(f"  Con Price (texto):       {con_price_texto}")

    # Verificacion 2: Registros problematicos (tienen Price pero no price_numeric)
    print("\n[PROBLEMA] 2. Registros con problema (tienen Price pero no price_numeric):")
    print("-" * 60)

    problematicos = [
        m for m in response.data
        if m.get("price_numeric") is None
        and m.get("Price")
        and m.get("Price").strip()
    ]

    print(f"  Total con problema:      {len(problematicos)}")

    if problematicos:
        print("\n  Ejemplos (primeros 10):")
        for i, m in enumerate(problematicos[:10], 1):
            print(f"    {i}. ID: {m.get('ID')} | Nombre: {m.get('Short Description', 'N/A')[:40]} | Price: {m.get('Price')}")

    # Verificacion 3: Verificar formato de precios en texto
    print("\n[FORMATO] 3. Analisis de formatos de precio (texto):")
    print("-" * 60)

    precio_formats = {}
    for m in response.data:
        price_text = m.get("Price")
        if price_text and price_text.strip():
            # Identificar patron
            if "$" in price_text:
                if "," in price_text:
                    formato = "Con $ y comas (ej: $1,234.56)"
                else:
                    formato = "Con $ sin comas (ej: $123.45)"
            elif "," in price_text:
                formato = "Sin $ con comas (ej: 1,234.56)"
            else:
                formato = "Sin $ ni comas (ej: 123.45)"

            precio_formats[formato] = precio_formats.get(formato, 0) + 1

    for formato, count in sorted(precio_formats.items(), key=lambda x: -x[1]):
        print(f"  {formato}: {count} registros")

    print("\n" + "=" * 60)
    if problematicos:
        print(f"[ACCION REQUERIDA] {len(problematicos)} registros necesitan migracion")
        print("=" * 60)
    else:
        print("[OK] TODOS LOS REGISTROS ESTAN CORRECTOS")
        print("=" * 60)

except Exception as e:
    print(f"\n[ERROR] Error ejecutando verificacion: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
