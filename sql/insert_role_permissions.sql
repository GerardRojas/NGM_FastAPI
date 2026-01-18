-- ========================================
-- ROLE PERMISSIONS - INSERCIÓN COMPLETA
-- Basado en mapeo de permisos personalizado
-- ========================================

-- Limpiar permisos existentes (opcional, comentar si no se desea borrar)
-- DELETE FROM public.role_permissions;

-- ========================================
-- 1. CEO - Acceso total a todo
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  true,
  true,
  true
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html'),
    ('expenses', 'Expenses', 'expenses.html'),
    ('pipeline', 'Pipeline Manager', 'pipeline.html'),
    ('projects', 'Projects', 'projects.html'),
    ('vendors', 'Vendors', 'vendors.html'),
    ('accounts', 'Accounts', 'accounts.html'),
    ('estimator', 'Estimator Suite', 'estimator.html'),
    ('team', 'Team Management', 'team.html'),
    ('god_view', 'God View', 'god-view.html'),
    ('reporting', 'Reporting', 'reporting.html'),
    ('budgets', 'Budgets', 'budgets.html'),
    ('roles', 'Roles Management', 'roles.html'),
    ('settings', 'Settings', 'settings.html'),
    ('audit', 'Audit Logs', 'audit.html')
) AS module(key, name, url)
WHERE r.rol_name = 'CEO'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 2. COO - Acceso total a todo
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  true,
  true,
  true
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html'),
    ('expenses', 'Expenses', 'expenses.html'),
    ('pipeline', 'Pipeline Manager', 'pipeline.html'),
    ('projects', 'Projects', 'projects.html'),
    ('vendors', 'Vendors', 'vendors.html'),
    ('accounts', 'Accounts', 'accounts.html'),
    ('estimator', 'Estimator Suite', 'estimator.html'),
    ('team', 'Team Management', 'team.html'),
    ('god_view', 'God View', 'god-view.html'),
    ('reporting', 'Reporting', 'reporting.html'),
    ('budgets', 'Budgets', 'budgets.html'),
    ('roles', 'Roles Management', 'roles.html'),
    ('settings', 'Settings', 'settings.html'),
    ('audit', 'Audit Logs', 'audit.html')
) AS module(key, name, url)
WHERE r.rol_name = 'COO'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 3. KD COO - Solo Dashboard y Expenses
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'true', 'true'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'true')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'KD COO'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 4. General Coordinator
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'false'),
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'true', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'false'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'false', 'false'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'false', 'false'),
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'false', 'false'),
    ('team', 'Team Management', 'team.html', 'true', 'true', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'General Coordinator'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 5. Project Coordinator - Igual que General Coordinator
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'false'),
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'true', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'false'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'false', 'false'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'false', 'false'),
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'false', 'false'),
    ('team', 'Team Management', 'team.html', 'true', 'true', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Project Coordinator'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 6. Accounting Manager
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'true'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'true'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'true', 'true'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'true', 'true')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Accounting Manager'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 7. Bookkeeper - Igual que Accounting Manager
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'true'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'true'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'true', 'true'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'true', 'true')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Bookkeeper'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 8. Estimator
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'true'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'true'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'true', 'true'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'true', 'true'),
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'true', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Estimator'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 9. Architect - Solo Dashboard por ahora
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Architect'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 10. Financial Analyst - Solo lectura
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'false', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'false', 'false'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'false', 'false'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Financial Analyst'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;

-- ========================================
-- 11. Admin Guest - Dashboard y Expenses solo lectura
-- ========================================
INSERT INTO public.role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  module.key,
  module.name,
  module.url,
  module.can_view::boolean,
  module.can_edit::boolean,
  module.can_delete::boolean
FROM public.rols r
CROSS JOIN (
  VALUES
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'false', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Admin Guest'
ON CONFLICT (rol_id, module_key) DO UPDATE SET
  can_view = EXCLUDED.can_view,
  can_edit = EXCLUDED.can_edit,
  can_delete = EXCLUDED.can_delete;
