-- ========================================
-- ROLE PERMISSIONS TABLE
-- Centraliza el control de acceso por roles
-- ========================================

-- Crear tabla de permisos si no existe
CREATE TABLE IF NOT EXISTS public.role_permissions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  rol_id bigint NOT NULL,
  module_key text NOT NULL, -- Identificador único del módulo (e.g., 'dashboard', 'expenses', 'projects')
  module_name text NOT NULL, -- Nombre visible del módulo
  module_url text NOT NULL, -- URL del módulo
  can_view boolean NOT NULL DEFAULT true, -- Puede ver el módulo
  can_edit boolean NOT NULL DEFAULT false, -- Puede editar
  can_delete boolean NOT NULL DEFAULT false, -- Puede eliminar
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT role_permissions_pkey PRIMARY KEY (id),
  CONSTRAINT role_permissions_unique UNIQUE (rol_id, module_key),
  CONSTRAINT role_permissions_rol_fkey FOREIGN KEY (rol_id) REFERENCES public.rols(rol_id) ON DELETE CASCADE
) TABLESPACE pg_default;

-- Índices para mejorar performance
CREATE INDEX IF NOT EXISTS idx_role_permissions_rol_id ON public.role_permissions(rol_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_module_key ON public.role_permissions(module_key);

-- ========================================
-- INSERTAR PERMISOS POR DEFECTO
-- ========================================

-- Primero, limpiar datos existentes si es necesario
-- TRUNCATE TABLE public.role_permissions;

-- Obtener IDs de roles (asumiendo que existen estos roles)
-- Ajusta los nombres según tu tabla 'rols'

-- CEO: Acceso total
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
    ('team', 'Team Management', 'team.html')
) AS module(key, name, url)
WHERE r.rol_name = 'CEO'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- COO: Acceso a operaciones
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
    ('dashboard', 'Dashboard', 'dashboard.html', 'true', 'true', 'false'),
    ('expenses', 'Expenses', 'expenses.html', 'true', 'true', 'false'),
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'true', 'true'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'true'),
    ('vendors', 'Vendors', 'vendors.html', 'true', 'true', 'false'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'false', 'false'),
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'true', 'false'),
    ('team', 'Team Management', 'team.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'COO'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Project Manager: Acceso a proyectos y pipeline
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
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'true', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'true', 'false'),
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'true', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Project Manager'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Accounting Manager: Acceso a finanzas
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
    ('vendors', 'Vendors', 'vendors.html', 'true', 'true', 'true'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'true', 'true')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Accounting Manager'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Bookkeeper: Acceso a contabilidad básica
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
    ('vendors', 'Vendors', 'vendors.html', 'true', 'false', 'false'),
    ('accounts', 'Accounts', 'accounts.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Bookkeeper'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Field Supervisor: Acceso a proyectos y pipeline
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
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'true', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Field Supervisor'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Estimator: Acceso a estimador
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
    ('estimator', 'Estimator Suite', 'estimator.html', 'true', 'true', 'false'),
    ('projects', 'Projects', 'projects.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Estimator'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- Crew Member: Acceso limitado solo a dashboard
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
    ('pipeline', 'Pipeline Manager', 'pipeline.html', 'true', 'false', 'false')
) AS module(key, name, url, can_view, can_edit, can_delete)
WHERE r.rol_name = 'Crew Member'
ON CONFLICT (rol_id, module_key) DO NOTHING;

-- ========================================
-- COMENTARIOS
-- ========================================

COMMENT ON TABLE public.role_permissions IS 'Centraliza los permisos de acceso a módulos por rol';
COMMENT ON COLUMN public.role_permissions.module_key IS 'Identificador único del módulo (debe coincidir con data-module en el frontend)';
COMMENT ON COLUMN public.role_permissions.can_view IS 'Usuario puede ver el módulo en el menú y acceder a él';
COMMENT ON COLUMN public.role_permissions.can_edit IS 'Usuario puede editar/crear registros en el módulo';
COMMENT ON COLUMN public.role_permissions.can_delete IS 'Usuario puede eliminar registros en el módulo';
