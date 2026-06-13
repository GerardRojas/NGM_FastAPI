-- =============================================================================
-- GRANT — Financial Analyst: ver/operar Daneel + autorizar gastos
-- -----------------------------------------------------------------------------
-- Habilita para el rol "Financial Analyst" las dos compuertas que el dashboard
-- evalua:
--   (A) Ver (y opcionalmente comandar) a Daneel en la tarjeta "Agents".
--       -> agent_config.daneel_viewer_roles / daneel_operator_roles
--       -> + al menos un modulo de categoria "Costs and Estimates" (lo da la
--          fila 'expenses' con can_view=true que crea PART 3).
--   (B) Que aparezcan los gastos pendientes para autorizar en "My Work".
--       -> role_permissions.can_authorize = true en el modulo 'expenses'.
--
-- Idempotente y aditivo. Corre en STAGING primero, verifica, luego PROD.
-- Tras correrlo, el usuario debe cerrar y reabrir sesion (la mapa de permisos
-- se cachea en login).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\grant_financial_analyst_daneel_authorize.sql
-- =============================================================================

-- Si el nombre exacto del rol difiere (ej. "Financial Analyst " con espacio, u
-- otra capitalizacion), ajusta el literal en los tres bloques. ILIKE ya cubre
-- diferencias de mayusculas/minusculas.


-- =============================================================================
-- PART 1 — Daneel VIEWER: agregar "Financial Analyst" a daneel_viewer_roles
-- -----------------------------------------------------------------------------
-- Necesario para que Daneel aparezca en la tarjeta Agents del dashboard.
-- Hace append + dedupe: preserva cualquier rol ya configurado.
-- =============================================================================
INSERT INTO public.agent_config (key, value)
VALUES ('daneel_viewer_roles', '["Financial Analyst"]'::jsonb)
ON CONFLICT (key) DO UPDATE
SET value = (
        SELECT jsonb_agg(DISTINCT e)
        FROM jsonb_array_elements(
            (CASE WHEN jsonb_typeof(public.agent_config.value) = 'array'
                  THEN public.agent_config.value ELSE '[]'::jsonb END)
            || '["Financial Analyst"]'::jsonb
        ) AS e
    ),
    updated_at = now();


-- =============================================================================
-- PART 2 — Daneel OPERATOR: agregar "Financial Analyst" a daneel_operator_roles
-- -----------------------------------------------------------------------------
-- OPCIONAL. Solo si quieres que ademas pueda COMANDAR a Daneel (abrir la consola
-- y el boton "Resolve with Daneel" en la fila de gastos). Si solo quieres que lo
-- VEA, comenta este bloque.
-- =============================================================================
INSERT INTO public.agent_config (key, value)
VALUES ('daneel_operator_roles', '["Financial Analyst"]'::jsonb)
ON CONFLICT (key) DO UPDATE
SET value = (
        SELECT jsonb_agg(DISTINCT e)
        FROM jsonb_array_elements(
            (CASE WHEN jsonb_typeof(public.agent_config.value) = 'array'
                  THEN public.agent_config.value ELSE '[]'::jsonb END)
            || '["Financial Analyst"]'::jsonb
        ) AS e
    ),
    updated_at = now();


-- =============================================================================
-- PART 3 — Permiso de modulo 'expenses' con can_authorize + can_view
-- -----------------------------------------------------------------------------
-- Crea la fila de permiso si no existe (o la actualiza). can_view=true mete a
-- 'expenses' en la categoria "Costs and Estimates" => satisface la compuerta de
-- "accounting" de Daneel. can_authorize=true hace aparecer los gastos pendientes
-- en My Work. (can_edit/can_delete a gusto; aqui edit=true, delete=false.)
-- =============================================================================
INSERT INTO public.role_permissions
    (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete, can_authorize)
SELECT r.rol_id, 'expenses', 'Expenses', 'expenses.html', true, true, false, true
FROM public.rols r
WHERE r.rol_name ILIKE 'Financial Analyst'
ON CONFLICT (rol_id, module_key) DO UPDATE
SET can_view       = true,
    can_authorize  = true,
    updated_at     = now();


-- =============================================================================
-- VERIFICACION — corre esto despues de aplicar; deberia mostrar todo en verde:
-- =============================================================================
-- -- 1) Config de Daneel (debe listar "Financial Analyst" en ambas, o solo viewer
-- --    si comentaste PART 2):
-- select key, value
--   from public.agent_config
--  where key in ('daneel_viewer_roles', 'daneel_operator_roles');
--
-- -- 2) Permiso de expenses del rol (can_view=t y can_authorize=t):
-- select r.rol_name, rp.module_key, rp.can_view, rp.can_edit, rp.can_authorize
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--  where r.rol_name ilike 'Financial Analyst'
--    and rp.module_key = 'expenses';
-- =============================================================================
