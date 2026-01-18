# Sistema de Permisos Basado en Roles

Este sistema centraliza el control de acceso a módulos en la base de datos, eliminando la necesidad de hardcodear permisos en el frontend.

## Arquitectura

### Base de Datos (Supabase)

**Tabla: `role_permissions`**
```sql
- id: uuid (PK)
- rol_id: bigint (FK a tabla rols)
- module_key: text (identificador único del módulo, e.g., 'expenses', 'projects')
- module_name: text (nombre visible del módulo)
- module_url: text (URL del módulo, e.g., 'expenses.html')
- can_view: boolean (puede ver el módulo)
- can_edit: boolean (puede editar/crear)
- can_delete: boolean (puede eliminar)
- created_at: timestamp
- updated_at: timestamp
```

### Backend (FastAPI)

**Router: `/permissions`**

Endpoints disponibles:

1. `GET /permissions/roles` - Lista todos los roles
2. `GET /permissions/role/{rol_id}` - Obtiene permisos de un rol específico
3. `GET /permissions/user/{user_id}` - Obtiene permisos de un usuario basado en su rol
4. `GET /permissions/modules` - Lista todos los módulos disponibles
5. `GET /permissions/check?user_id=X&module_key=Y&action=view` - Verifica un permiso específico

### Frontend (JavaScript)

**Script: `assets/js/permissions.js`**

API pública expuesta en `window.PermissionsManager`:

```javascript
// Inicializar sistema de permisos
await PermissionsManager.init();

// Cargar permisos de un usuario
await PermissionsManager.load(userId);

// Verificar permisos
PermissionsManager.canView('expenses');    // true/false
PermissionsManager.canEdit('projects');    // true/false
PermissionsManager.canDelete('vendors');   // true/false

// Verificar permiso genérico
PermissionsManager.hasPermission('accounts', 'edit'); // true/false

// Obtener módulos visibles para el usuario actual
const visibleModules = PermissionsManager.getVisibleModules();

// Aplicar permisos al DOM
PermissionsManager.apply();

// Acceder a permisos cargados
console.log(PermissionsManager.permissions);
console.log(PermissionsManager.roleId);
```

## Uso en el Frontend

### 1. Incluir el script en todas las páginas

```html
<script src="assets/js/config.js" defer></script>
<script src="assets/js/permissions.js" defer></script>
```

### 2. Marcar módulos con `data-module`

En lugar de usar `data-roles` hardcodeado:

**❌ ANTES (hardcoded):**
```html
<a class="module-card" href="expenses.html"
   data-roles="COO,CEO,Accounting Manager,Bookkeeper">
   Expenses
</a>
```

**✅ AHORA (dinámico desde DB):**
```html
<a class="module-card" href="expenses.html"
   data-module="expenses">
   Expenses
</a>
```

### 3. Marcar botones con permisos requeridos

```html
<!-- Requiere permiso de edición -->
<button data-requires-edit="expenses" id="btnEditExpenses">
   Edit Expenses
</button>

<!-- Requiere permiso de eliminación -->
<button data-requires-delete="vendors" class="btn-delete">
   Delete Vendor
</button>
```

El sistema automáticamente:
- Oculta módulos a los que el usuario no tiene acceso (`can_view = false`)
- Deshabilita botones de edición si `can_edit = false`
- Deshabilita botones de eliminación si `can_delete = false`

## Configuración Inicial

### 1. Crear la tabla y permisos

Ejecutar el script SQL en Supabase:

```bash
# En la consola SQL de Supabase
psql> \i sql/create_role_permissions.sql
```

O copiar y ejecutar manualmente el contenido de `sql/create_role_permissions.sql`.

### 2. Verificar permisos por defecto

El script crea permisos por defecto para estos roles:
- **CEO**: Acceso total a todos los módulos
- **COO**: Acceso a operaciones (expenses, pipeline, projects, etc.)
- **Project Manager**: Acceso a proyectos y pipeline
- **Accounting Manager**: Acceso a finanzas (expenses, vendors, accounts)
- **Bookkeeper**: Acceso de solo lectura a finanzas
- **Field Supervisor**: Acceso a proyectos y pipeline
- **Estimator**: Acceso a estimador y proyectos
- **Crew Member**: Acceso limitado a dashboard y pipeline

### 3. Personalizar permisos

Actualizar permisos directamente en Supabase:

```sql
-- Dar permiso de edición de expenses a Bookkeeper
UPDATE role_permissions
SET can_edit = true
WHERE rol_id = (SELECT rol_id FROM rols WHERE rol_name = 'Bookkeeper')
AND module_key = 'expenses';

-- Agregar nuevo módulo
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
VALUES (
  (SELECT rol_id FROM rols WHERE rol_name = 'CEO'),
  'reports',
  'Reports',
  'reports.html',
  true,
  true,
  true
);
```

## Módulos Disponibles

Los siguientes módulos están configurados:

| module_key | module_name | module_url |
|------------|-------------|------------|
| dashboard | Dashboard | dashboard.html |
| expenses | Expenses | expenses.html |
| pipeline | Pipeline Manager | pipeline.html |
| projects | Projects | projects.html |
| vendors | Vendors | vendors.html |
| accounts | Accounts | accounts.html |
| estimator | Estimator Suite | estimator.html |
| team | Team Management | team.html |

## Agregar Nuevos Módulos

### 1. Backend

Ya está todo configurado. Solo necesitas agregar los permisos en la base de datos:

```sql
-- Para todos los roles o solo algunos
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  'new_module',
  'New Module',
  'new_module.html',
  true,
  true,
  false
FROM rols r
WHERE r.rol_name IN ('CEO', 'COO');
```

### 2. Frontend

Agregar `data-module` en dashboard.html:

```html
<a class="module-card" href="new_module.html" data-module="new_module">
   <div class="module-icon">
       <span class="module-icon-badge">NM</span>
   </div>
   <h3 class="module-title">New Module</h3>
   <p class="module-desc">Description of the new module.</p>
</a>
```

## Ventajas del Sistema

1. **Centralizado**: Los permisos están en la base de datos, no hardcodeados
2. **Consistente**: Misma fuente de verdad para todo el sistema
3. **Mantenible**: Fácil actualizar permisos sin tocar código
4. **Granular**: Control de view/edit/delete por módulo y rol
5. **Escalable**: Agregar nuevos módulos o roles es trivial
6. **Auditable**: Historial de cambios en la base de datos

## Troubleshooting

### Los módulos no se ocultan

1. Verificar que `permissions.js` esté cargado:
   ```javascript
   console.log(window.PermissionsManager);
   ```

2. Verificar que el usuario tenga datos en localStorage:
   ```javascript
   console.log(localStorage.getItem('user_data'));
   ```

3. Verificar que los permisos se cargaron:
   ```javascript
   console.log(PermissionsManager.permissions);
   ```

### El backend devuelve error 404

1. Verificar que la tabla `role_permissions` existe en Supabase
2. Verificar que el router está registrado en `main.py`
3. Verificar que el usuario tiene un rol asignado en la tabla `users`

### Los permisos no se actualizan

1. El sistema usa auto-refresh cuando el DOM cambia
2. Para forzar actualización:
   ```javascript
   await PermissionsManager.load(userId);
   PermissionsManager.apply();
   ```

## Próximos Pasos

1. Implementar UI para administrar permisos (página de admin)
2. Agregar permisos a nivel de campo (ocultar columnas específicas)
3. Implementar permisos basados en proyectos (acceso por proyecto)
4. Agregar logs de acceso para auditoría
