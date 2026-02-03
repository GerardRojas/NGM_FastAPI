-- ========================================
-- FIX: Políticas RLS para estimator-images bucket
-- ========================================
-- Este script elimina políticas conflictivas y crea nuevas
-- que permiten operaciones de Storage para usuarios anónimos

-- 1. Eliminar todas las políticas existentes del bucket estimator-images
DROP POLICY IF EXISTS "Allow anon uploads to estimator-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow anon updates to estimator-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow anon deletes to estimator-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow authenticated uploads" ON storage.objects;
DROP POLICY IF EXISTS "Allow authenticated updates" ON storage.objects;
DROP POLICY IF EXISTS "Allow authenticated deletes" ON storage.objects;
DROP POLICY IF EXISTS "Public read access" ON storage.objects;
DROP POLICY IF EXISTS "public_read_estimator_images" ON storage.objects;
DROP POLICY IF EXISTS "anon_insert_estimator_images" ON storage.objects;
DROP POLICY IF EXISTS "anon_update_estimator_images" ON storage.objects;
DROP POLICY IF EXISTS "anon_delete_estimator_images" ON storage.objects;

-- 2. Crear políticas nuevas para PUBLIC (más permisivo que anon)
-- Esto permite operaciones tanto para usuarios anónimos como autenticados

-- Permitir SELECT (lectura pública)
CREATE POLICY "estimator_images_public_select"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'estimator-images');

-- Permitir INSERT (upload)
CREATE POLICY "estimator_images_public_insert"
ON storage.objects FOR INSERT
TO public
WITH CHECK (bucket_id = 'estimator-images');

-- Permitir UPDATE
CREATE POLICY "estimator_images_public_update"
ON storage.objects FOR UPDATE
TO public
USING (bucket_id = 'estimator-images')
WITH CHECK (bucket_id = 'estimator-images');

-- Permitir DELETE
CREATE POLICY "estimator_images_public_delete"
ON storage.objects FOR DELETE
TO public
USING (bucket_id = 'estimator-images');

-- 3. Comentarios
COMMENT ON POLICY "estimator_images_public_select" ON storage.objects IS
'Permite lectura pública de imágenes en estimator-images bucket';

COMMENT ON POLICY "estimator_images_public_insert" ON storage.objects IS
'Permite uploads públicos (anon + authenticated) a estimator-images bucket';

COMMENT ON POLICY "estimator_images_public_update" ON storage.objects IS
'Permite updates públicos a estimator-images bucket';

COMMENT ON POLICY "estimator_images_public_delete" ON storage.objects IS
'Permite deletes públicos a estimator-images bucket';
