-- =============================================================================
-- EXPENSE AUTHORIZER — full setup (column + seed + audit), in ONE file.
-- Run this whole file in the Supabase SQL editor: STAGING first, then PROD.
-- Everything here is IDEMPOTENT and additive, so it is safe to re-run and safe
-- to run even if part 1 was already applied earlier.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\expense_authorizer_setup.sql
--
-- Run this BEFORE (or together with) deploying the backend: the new code reads
-- role_permissions.can_authorize, so the column must exist first. The audit
-- table (part 3) is best-effort in code (a failed insert never blocks a grant),
-- so it could go after the deploy — but just run the whole file up front.
--
-- After running, authorizers must refresh their session (log out/in) so the
-- cached permissions map picks up can_authorize.
-- =============================================================================


-- =============================================================================
-- PART 1 — can_authorize column
-- -----------------------------------------------------------------------------
-- Makes "who can authorize expenses" a real per-role permission instead of a
-- hardcoded role-name list. Applies to every role_permissions row but is only
-- meaningful for the 'expenses' module row; other modules keep it false.
-- =============================================================================

ALTER TABLE public.role_permissions
    ADD COLUMN IF NOT EXISTS can_authorize boolean NOT NULL DEFAULT false;


-- =============================================================================
-- PART 2 — seed the roles that could authorize before this change
-- -----------------------------------------------------------------------------
-- Preserves prior behavior. Mirrors the backend's authoritative AUTHORIZER_ROLES.
-- Only touches the 'expenses' module row; admins adjust the rest in the Roles
-- Management UI (Expense Authorization panel) or via Art ("let X approve expenses").
-- =============================================================================

UPDATE public.role_permissions rp
SET can_authorize = true
FROM public.rols r
WHERE rp.rol_id = r.rol_id
  AND rp.module_key = 'expenses'
  AND r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Project Manager')
  AND rp.can_authorize IS DISTINCT FROM true;


-- =============================================================================
-- PART 3 — role-permission change audit
-- -----------------------------------------------------------------------------
-- A who/when/what trail for changes to role_permissions made outside the generic
-- grid (today: can_authorize for expenses, set via the Roles UI panel or by Art
-- on a CEO/COO command). rol_id / actor_user_id are stored as text (no FK) so the
-- log survives if a role/user is deleted and doesn't care whether rol_id is uuid
-- or bigint in this DB. Writes are best-effort: a failed audit insert never blocks
-- the permission change itself (see set_role_can_authorize() in
-- api/routers/permissions.py).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.role_permission_audit (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rol_id        text,
    rol_name      text,
    module_key    text NOT NULL,
    field         text NOT NULL,              -- e.g. 'can_authorize'
    old_value     boolean,
    new_value     boolean,
    actor_user_id text,                       -- who made the change (null = unknown)
    actor_name    text,
    actor_role    text,
    source        text NOT NULL DEFAULT 'roles_ui',  -- 'roles_ui' | 'art'
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_role_permission_audit_rol
    ON public.role_permission_audit (rol_id);
CREATE INDEX IF NOT EXISTS idx_role_permission_audit_created
    ON public.role_permission_audit (created_at DESC);


-- =============================================================================
-- VERIFICATION (optional) -----------------------------------------------------
-- =============================================================================
-- -- Who can authorize expenses now:
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete, rp.can_authorize
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'expenses'
--  order by r.rol_name;
--
-- -- Recent permission changes (after using the UI or Art):
-- select created_at, source, actor_name, actor_role, rol_name, field,
--        old_value, new_value
--   from public.role_permission_audit
--  order by created_at desc
--  limit 50;
-- =============================================================================
