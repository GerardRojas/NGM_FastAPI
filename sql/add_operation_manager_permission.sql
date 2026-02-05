-- ========================================
-- Agregar Operation Manager a role_permissions
-- ========================================
-- Este script agrega el módulo "operation_manager" a todos los roles existentes
-- con permisos de solo visualización por defecto

-- Insertar operation_manager para todos los roles existentes
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
    r.rol_id,
    'operation_manager' as module_key,
    'Operation Manager' as module_name,
    'operation-manager.html' as module_url,
    CASE
        WHEN r.rol_name IN ('CEO', 'COO') THEN true
        ELSE false
    END as can_view,
    false as can_edit,
    false as can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id
    AND rp.module_key = 'operation_manager'
);

-- Verificar que se agregó correctamente
SELECT
    r.rol_name,
    rp.module_key,
    rp.module_name,
    rp.can_view,
    rp.can_edit,
    rp.can_delete
FROM role_permissions rp
INNER JOIN rols r ON rp.rol_id = r.rol_id
WHERE rp.module_key = 'operation_manager'
ORDER BY r.rol_name;
