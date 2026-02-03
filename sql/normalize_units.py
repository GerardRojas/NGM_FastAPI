"""
Script para normalizar unidades en la tabla materials
1. Extrae todas las unidades unicas de materials.Unit
2. Normaliza variantes (Sf = sf = sqft, etc.)
3. Puebla la tabla units con unidades normalizadas
4. Actualiza materials.unit_id para vincular con units
"""
import os
import sys
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
from collections import defaultdict

# Cargar variables de entorno
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[ERROR] SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no estan configuradas")
    sys.exit(1)

# Crear cliente
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Diccionario de normalizacion de unidades
# Mapea variantes -> nombre normalizado
UNIT_NORMALIZATION = {
    # Square Feet
    "sf": "Sqft",
    "sqft": "Sqft",
    "sq ft": "Sqft",
    "sq.ft": "Sqft",
    "square feet": "Sqft",
    "square foot": "Sqft",

    # Linear Feet
    "lf": "LF",
    "lin ft": "LF",
    "linear feet": "LF",
    "linear foot": "LF",

    # Each/Unit
    "ea": "Each",
    "each": "Each",
    "unit": "Each",
    "pcs": "Each",
    "piece": "Each",

    # Box
    "box": "Box",
    "boxes": "Box",

    # Gallon
    "gal": "Gallon",
    "gallon": "Gallon",
    "gallons": "Gallon",

    # Pound
    "lb": "Lb",
    "lbs": "Lb",
    "pound": "Lb",
    "pounds": "Lb",

    # Yard
    "yd": "Yard",
    "yard": "Yard",
    "yards": "Yard",

    # Meter
    "m": "Meter",
    "meter": "Meter",
    "meters": "Meter",

    # Roll
    "roll": "Roll",
    "rolls": "Roll",

    # Sheet
    "sheet": "Sheet",
    "sheets": "Sheet",

    # Bag
    "bag": "Bag",
    "bags": "Bag",

    # Cubic Yard
    "cy": "CY",
    "cubic yard": "CY",
    "cubic yards": "CY",

    # Ton
    "ton": "Ton",
    "tons": "Ton",
}

def normalize_unit(unit_text):
    """
    Normaliza el nombre de la unidad
    """
    if not unit_text or not unit_text.strip():
        return None

    # Limpiar espacios y convertir a minusculas para matching
    cleaned = unit_text.strip().lower()

    # Buscar en el diccionario de normalizacion
    if cleaned in UNIT_NORMALIZATION:
        return UNIT_NORMALIZATION[cleaned]

    # Si no esta en el diccionario, usar capitalize
    # (Primera letra mayuscula, resto minusculas)
    return unit_text.strip().capitalize()

print("=" * 60)
print("NORMALIZACION DE UNIDADES")
print("=" * 60)

try:
    # PASO 1: Extraer todas las unidades unicas de materials
    print("\n[PASO 1] Extrayendo unidades unicas de materials...")
    print("-" * 60)

    response = supabase.table("materials").select('"Unit"').execute()

    # Contar ocurrencias de cada unidad
    unit_counts = defaultdict(int)
    for m in response.data:
        unit_text = m.get("Unit")
        if unit_text and unit_text.strip():
            unit_counts[unit_text] += 1

    print(f"  Total de variantes encontradas: {len(unit_counts)}")
    print(f"  Total de materiales: {len(response.data)}")

    # Mostrar las 10 unidades mas comunes
    print("\n  Top 10 unidades mas usadas:")
    sorted_units = sorted(unit_counts.items(), key=lambda x: -x[1])
    for i, (unit, count) in enumerate(sorted_units[:10], 1):
        print(f"    {i}. '{unit}': {count} materiales")

    # PASO 2: Normalizar y agrupar
    print("\n[PASO 2] Normalizando unidades...")
    print("-" * 60)

    # Mapear cada variante -> nombre normalizado
    normalized_map = {}
    normalized_counts = defaultdict(int)

    for unit_variant, count in unit_counts.items():
        normalized = normalize_unit(unit_variant)
        if normalized:
            normalized_map[unit_variant] = normalized
            normalized_counts[normalized] += count

    print(f"  Unidades normalizadas: {len(normalized_counts)}")
    print("\n  Unidades normalizadas con sus counts:")
    for i, (unit, count) in enumerate(sorted(normalized_counts.items(), key=lambda x: -x[1]), 1):
        print(f"    {i}. '{unit}': {count} materiales")

    # PASO 3: Crear/actualizar tabla units
    print("\n[PASO 3] Poblando tabla units...")
    print("-" * 60)

    # Primero, obtener units existentes
    existing_units_response = supabase.table("units").select("*").execute()
    existing_units = {u["unit_name"]: u for u in (existing_units_response.data or [])}

    print(f"  Unidades existentes en tabla units: {len(existing_units)}")

    units_to_insert = []
    units_created = 0
    units_skipped = 0

    for normalized_name in normalized_counts.keys():
        if normalized_name not in existing_units:
            # Generar UUID unico para la unidad
            unit_id = str(uuid.uuid4())

            units_to_insert.append({
                "id_unit": unit_id,
                "unit_name": normalized_name
            })
            units_created += 1
        else:
            units_skipped += 1

    # Insertar nuevas unidades
    if units_to_insert:
        print(f"\n  Insertando {len(units_to_insert)} nuevas unidades...")
        supabase.table("units").insert(units_to_insert).execute()
        print(f"  [OK] {units_created} unidades creadas")
    else:
        print("  [INFO] No hay nuevas unidades para crear")

    print(f"  [INFO] {units_skipped} unidades ya existian")

    # PASO 4: Actualizar materials.unit_id
    print("\n[PASO 4] Actualizando materials.unit_id...")
    print("-" * 60)

    # Obtener todas las units (ahora con las nuevas insertadas)
    all_units_response = supabase.table("units").select("*").execute()
    unit_name_to_id = {u["unit_name"]: u["id_unit"] for u in (all_units_response.data or [])}

    print(f"  Total units disponibles: {len(unit_name_to_id)}")

    # Actualizar cada material
    updated_count = 0
    skipped_count = 0
    error_count = 0

    for i, material in enumerate(response.data, 1):
        unit_text = material.get("Unit")
        material_id = material.get("ID")

        if not unit_text or not unit_text.strip():
            skipped_count += 1
            continue

        # Normalizar y buscar el ID
        normalized = normalize_unit(unit_text)
        if normalized and normalized in unit_name_to_id:
            unit_id = unit_name_to_id[normalized]

            try:
                # Actualizar material
                supabase.table("materials").update({
                    "unit_id": unit_id
                }).eq('"ID"', material_id).execute()

                updated_count += 1

                # Mostrar progreso cada 100 materiales
                if updated_count % 100 == 0:
                    print(f"  Progreso: {updated_count} materiales actualizados...")

            except Exception as e:
                print(f"  [ERROR] Material {material_id}: {str(e)}")
                error_count += 1
        else:
            print(f"  [WARN] No se encontro unit_id para '{unit_text}' (normalizado: '{normalized}')")
            skipped_count += 1

    print("\n" + "=" * 60)
    print("[RESUMEN]")
    print("=" * 60)
    print(f"  Total materiales procesados:    {len(response.data)}")
    print(f"  Materiales actualizados:        {updated_count}")
    print(f"  Materiales sin unidad (skip):   {skipped_count}")
    print(f"  Errores:                        {error_count}")
    print(f"\n  Unidades normalizadas:          {len(normalized_counts)}")
    print(f"  Unidades nuevas creadas:        {units_created}")

    print("\n" + "=" * 60)
    if error_count == 0:
        print("[EXITO] Normalizacion completada sin errores")
    else:
        print(f"[ATENCION] Normalizacion completada con {error_count} errores")
    print("=" * 60)

except Exception as e:
    print(f"\n[ERROR FATAL] {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
