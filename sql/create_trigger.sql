-- ========================================
-- TRIGGER: Sincronización automática de "Price" -> price_numeric
-- ========================================
-- Este trigger se ejecutará automáticamente cada vez que se inserte
-- o actualice la columna "Price" (texto) y sincronizará el valor
-- a la columna price_numeric (numérico).
-- ========================================

-- 1. Crear función que sincroniza el precio
CREATE OR REPLACE FUNCTION sync_price_to_numeric()
RETURNS TRIGGER AS $$
BEGIN
    -- Solo procesar si "Price" tiene un valor
    IF NEW."Price" IS NOT NULL AND TRIM(NEW."Price") != '' THEN
        BEGIN
            -- Limpiar el texto: quitar $, comas, espacios
            -- Luego convertir a numérico
            NEW.price_numeric := CAST(
                NULLIF(
                    TRIM(
                        REPLACE(
                            REPLACE(NEW."Price", '$', ''),
                            ',',
                            ''
                        )
                    ),
                    ''
                ) AS NUMERIC(12, 2)
            );
        EXCEPTION
            -- Si la conversión falla (texto no numérico), dejar NULL
            WHEN OTHERS THEN
                NEW.price_numeric := NULL;
        END;
    ELSE
        -- Si "Price" está vacío, poner NULL en price_numeric
        NEW.price_numeric := NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Crear trigger que ejecuta la función
DROP TRIGGER IF EXISTS trigger_sync_price ON materials;

CREATE TRIGGER trigger_sync_price
    BEFORE INSERT OR UPDATE OF "Price"
    ON materials
    FOR EACH ROW
    EXECUTE FUNCTION sync_price_to_numeric();

-- 3. Comentarios para documentación
COMMENT ON FUNCTION sync_price_to_numeric() IS
'Sincroniza automáticamente la columna "Price" (texto) con price_numeric (numérico).
Limpia el texto removiendo $, comas y espacios antes de convertir.
Si la conversión falla, establece NULL en price_numeric.';

COMMENT ON TRIGGER trigger_sync_price ON materials IS
'Ejecuta sync_price_to_numeric() antes de INSERT o UPDATE de "Price".
Mantiene price_numeric sincronizado automáticamente.';
