-- ========================================
-- PASO 1: Verificar registros con price_numeric NULL
-- ========================================

-- 1.1 Contar registros con price_numeric NULL pero que tienen "Price"
SELECT
    COUNT(*) as total_con_problema,
    COUNT(CASE WHEN "Price" IS NOT NULL AND "Price" != '' THEN 1 END) as tienen_precio_texto
FROM materials
WHERE price_numeric IS NULL;

-- 1.2 Ver algunos ejemplos de registros problemáticos
SELECT
    "ID",
    "Short Description" as nombre,
    "Price" AS precio_texto,
    price_numeric AS precio_numerico
FROM materials
WHERE price_numeric IS NULL
  AND "Price" IS NOT NULL
  AND "Price" != ''
LIMIT 10;

-- 1.3 Ver estadísticas generales
SELECT
    COUNT(*) as total_materiales,
    COUNT(CASE WHEN price_numeric IS NOT NULL THEN 1 END) as con_price_numeric,
    COUNT(CASE WHEN price_numeric IS NULL THEN 1 END) as sin_price_numeric,
    COUNT(CASE WHEN "Price" IS NOT NULL AND "Price" != '' THEN 1 END) as con_price_texto
FROM materials;
