-- ========================================
-- Agregar política SELECT para estimator-images
-- ========================================
-- Permite lectura pública de imágenes en el bucket

CREATE POLICY "Allow anon selects to estimator-images"
ON storage.objects FOR SELECT
TO anon
USING (bucket_id = 'estimator-images');
