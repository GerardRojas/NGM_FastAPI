-- ========================================
-- FIX: RLS Policies for process_manager_state
-- ========================================
-- Este script actualiza las políticas RLS para permitir
-- acceso tanto para usuarios anon como authenticated

-- 1. Eliminar políticas existentes
DROP POLICY IF EXISTS "process_manager_state_select" ON public.process_manager_state;
DROP POLICY IF EXISTS "process_manager_state_insert" ON public.process_manager_state;
DROP POLICY IF EXISTS "process_manager_state_update" ON public.process_manager_state;
DROP POLICY IF EXISTS "process_manager_history_select" ON public.process_manager_history;

-- 2. Crear políticas para PUBLIC (anon + authenticated)

-- Permitir SELECT (lectura)
CREATE POLICY "process_manager_state_select"
ON public.process_manager_state FOR SELECT
TO public
USING (true);

-- Permitir INSERT
CREATE POLICY "process_manager_state_insert"
ON public.process_manager_state FOR INSERT
TO public
WITH CHECK (true);

-- Permitir UPDATE
CREATE POLICY "process_manager_state_update"
ON public.process_manager_state FOR UPDATE
TO public
USING (true)
WITH CHECK (true);

-- History: Solo lectura para public
CREATE POLICY "process_manager_history_select"
ON public.process_manager_history FOR SELECT
TO public
USING (true);

-- 3. Verificar que RLS está habilitado
ALTER TABLE public.process_manager_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.process_manager_history ENABLE ROW LEVEL SECURITY;

-- 4. Comentarios
COMMENT ON POLICY "process_manager_state_select" ON public.process_manager_state IS
'Permite lectura publica del estado del process manager';

COMMENT ON POLICY "process_manager_state_insert" ON public.process_manager_state IS
'Permite crear estados del process manager';

COMMENT ON POLICY "process_manager_state_update" ON public.process_manager_state IS
'Permite actualizar estados del process manager';
