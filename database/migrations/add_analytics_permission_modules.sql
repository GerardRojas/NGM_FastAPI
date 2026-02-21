-- ================================
-- Migration: Add analytics permission modules
-- ================================
-- Adds new permission modules for project dashboards, KPIs,
-- vendor intelligence, and timeline manager.
-- CEO/COO get access by default; other roles start with no access.
--
-- Run this in your Supabase SQL editor.

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  m.module_key,
  m.module_name,
  m.module_url,
  CASE WHEN r.rol_name IN ('CEO', 'COO') THEN TRUE ELSE FALSE END AS can_view,
  CASE WHEN r.rol_name IN ('CEO', 'COO') THEN TRUE ELSE FALSE END AS can_edit,
  FALSE AS can_delete
FROM rols r
CROSS JOIN (VALUES
  ('project_dashboard', 'Project Dashboard',  'projects.html#dashboard'),
  ('project_kpis',      'Executive KPIs',     'projects.html#kpis'),
  ('cost_projection',   'Cost Projection',    'projects.html#cost'),
  ('vendor_intelligence','Vendor Intelligence','vendors.html#intelligence'),
  ('timeline_manager',  'Timeline Manager',   'timeline-manager.html')
) AS m(module_key, module_name, module_url)
WHERE NOT EXISTS (
  SELECT 1 FROM role_permissions rp
  WHERE rp.rol_id = r.rol_id AND rp.module_key = m.module_key
);
