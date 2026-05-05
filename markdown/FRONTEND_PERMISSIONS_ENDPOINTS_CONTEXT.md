# Frontend Context: Integración de Permisos y Menú por Sesión

Este documento describe cómo el frontend debe consumir los endpoints de permisos para:

1. Construir el menú del usuario una sola vez por sesión.
2. Evaluar permisos de acciones (`view`, `edit`, `delete`) de forma consistente.

---

## Endpoints a usar

### 1) `GET /permissions/user/{user_id}`

Propósito:
- Cargar permisos del rol del usuario.
- Cargar metadatos de menú (`menu_items`, `menu_categories`).
- Guardar todo en sesión para no recalcular menú en cada vista.
- Recibir el menú ya ordenado por `menu_categories.order` y luego `menu_items.order` (asc).

Auth requerida:
- Header `Authorization: Bearer <access_token>`

Notas de seguridad:
- Si `user_id` del path es distinto al del token, backend exige permiso de lectura en `team` o `roles`.

Respuesta esperada (success):

```json
{
  "user_id": "9f7c7d7a-0a9b-4f4f-8cf4-9f495dd6b2de",
  "rol_id": "701797b1-1f2f-4c8c-980a-f25ff33737ff",
  "rol_name": "COO",
  "permissions": [
    {
      "id": "2cb6db8a-0c4d-4eb7-8d96-dfc18f2f7d60",
      "module_key": "expenses",
      "module_name": "Expenses",
      "module_url": "expenses.html",
      "can_view": true,
      "can_edit": true,
      "can_delete": false,
      "menu_item_id": "53b73e2c-89a0-4b7c-8a57-9c0bc14bf90f"
    }
  ],
  "menu": [
    {
      "menu_item_id": "53b73e2c-89a0-4b7c-8a57-9c0bc14bf90f",
      "slug": "expenses",
      "icon_type": "fa",
      "icon_text": "fa-receipt",
      "category_id": "80a2f3f9-7c47-4c4d-9f2b-b9cb9f0d9e89",
      "category_name": "Finance",
      "category_order": 1,
      "item_order": 2,
      "module_key": "expenses",
      "module_name": "Expenses",
      "module_url": "expenses.html",
      "can_view": true,
      "can_edit": true,
      "can_delete": false
    }
  ],
  "rows": [
    {
      "rol_id": "701797b1-1f2f-4c8c-980a-f25ff33737ff",
      "rol_name": "COO",
      "permission_id": "2cb6db8a-0c4d-4eb7-8d96-dfc18f2f7d60",
      "module_key": "expenses",
      "module_name": "Expenses",
      "module_url": "expenses.html",
      "can_view": true,
      "can_edit": true,
      "can_delete": false,
      "menu_item_id": "53b73e2c-89a0-4b7c-8a57-9c0bc14bf90f",
      "slug": "expenses",
      "icon_type": "fa",
      "icon_text": "fa-receipt",
      "category_id": "80a2f3f9-7c47-4c4d-9f2b-b9cb9f0d9e89",
      "category_name": "Finance",
      "category_order": 1,
      "item_order": 2
    }
  ]
}
```

Casos especiales:
- Usuario sin rol:

```json
{
  "user_id": "9f7c7d7a-0a9b-4f4f-8cf4-9f495dd6b2de",
  "rol_id": null,
  "rol_name": null,
  "permissions": [],
  "menu": [],
  "rows": []
}
```

Errores:
- `401`: token faltante, inválido o expirado.
- `403`: no autorizado para consultar otro usuario.
- `404`: usuario no encontrado.
- `500`: error interno.

---

### 2) `GET /permissions/check?user_id=...&module_key=...&action=...`

Propósito:
- Validar un permiso puntual para una acción concreta.

Auth requerida:
- Header `Authorization: Bearer <access_token>`

Parámetros:
- `user_id` (obligatorio)
- `module_key` (obligatorio)
- `action` (opcional, default: `view`; soporta `view|edit|delete`)

Respuesta esperada (success):

```json
{
  "has_permission": true,
  "reason": null,
  "user_id": "9f7c7d7a-0a9b-4f4f-8cf4-9f495dd6b2de",
  "rol_id": "701797b1-1f2f-4c8c-980a-f25ff33737ff",
  "rol_name": "COO",
  "module_key": "expenses",
  "action": "edit",
  "permissions": {
    "rol_id": "701797b1-1f2f-4c8c-980a-f25ff33737ff",
    "rol_name": "COO",
    "permission_id": "2cb6db8a-0c4d-4eb7-8d96-dfc18f2f7d60",
    "module_key": "expenses",
    "module_name": "Expenses",
    "module_url": "expenses.html",
    "can_view": true,
    "can_edit": true,
    "can_delete": false,
    "menu_item_id": "53b73e2c-89a0-4b7c-8a57-9c0bc14bf90f",
    "slug": "expenses",
    "icon_type": "fa",
    "icon_text": "fa-receipt",
    "category_id": "80a2f3f9-7c47-4c4d-9f2b-b9cb9f0d9e89",
    "category_name": "Finance",
    "category_order": 1,
    "item_order": 2
  }
}
```

Caso sin permiso de módulo:

```json
{
  "has_permission": false,
  "reason": "No permission record found for this role and module",
  "user_id": "9f7c7d7a-0a9b-4f4f-8cf4-9f495dd6b2de",
  "rol_id": "701797b1-1f2f-4c8c-980a-f25ff33737ff",
  "rol_name": "COO",
  "module_key": "unknown_module",
  "action": "view",
  "permissions": null
}
```

---

## Cambios que debe hacer Frontend

## A) Carga de sesión al login

1. Login obtiene `access_token` y `user.user_id`.
2. Inmediatamente llamar `GET /permissions/user/{user_id}` con token.
3. Guardar en `sessionStorage` (o store global):
   - `permissions_payload` completo.
   - `permissions_map` por `module_key`.
   - `menu_items` (usar campo `menu`).
4. Construir menú UI desde `menu` y no desde reglas hardcodeadas.

Sugerencia de claves de sesión:
- `session.permissions.payload`
- `session.permissions.map`
- `session.menu.items`
- `session.user.id`
- `session.token`

## B) Resolución local de permisos (rápida)

Con el payload de sesión:
- `canView(moduleKey)` -> `permissions_map[moduleKey]?.can_view === true`
- `canEdit(moduleKey)` -> `permissions_map[moduleKey]?.can_edit === true`
- `canDelete(moduleKey)` -> `permissions_map[moduleKey]?.can_delete === true`

## C) Uso de `/permissions/check`

Usarlo sólo cuando:
- Se requiera validación puntual contra backend antes de una acción sensible.
- Existan dudas de sincronía de sesión.

No usar `/check` para pintar todo el menú (eso ya sale de `/user/{user_id}`).

## D) Invalidación de caché

Limpiar payload de permisos/menú cuando:
- Logout.
- Expiración o refresh de token.
- Cambio de usuario.

Opcional:
- Rehidratar permisos después de acciones de administración de roles.

---

## Ejemplo de contrato TypeScript (sugerido)

```ts
export type PermissionRecord = {
  id: string;
  module_key: string;
  module_name: string;
  module_url: string;
  can_view: boolean;
  can_edit: boolean;
  can_delete: boolean;
  menu_item_id: string | null;
};

export type MenuItemRecord = {
  menu_item_id: string;
  slug: string | null;
  icon_type: string | null;
  icon_text: string | null;
  category_id: string | null;
  category_name: string | null;
  category_order: number | null;
  item_order: number | null;
  module_key: string;
  module_name: string;
  module_url: string;
  can_view: boolean;
  can_edit: boolean;
  can_delete: boolean;
};

export type PermissionsUserResponse = {
  user_id: string;
  rol_id: string | null;
  rol_name: string | null;
  permissions: PermissionRecord[];
  menu: MenuItemRecord[];
  rows: Array<Record<string, unknown>>;
};

export type PermissionsCheckResponse = {
  has_permission: boolean;
  reason: string | null;
  user_id: string;
  rol_id: string | null;
  rol_name: string | null;
  module_key: string;
  action: "view" | "edit" | "delete" | string;
  permissions: Record<string, unknown> | null;
};
```

---

## Recomendación de implementación mínima

1. Adaptar `PermissionsManager.load(userId)` para consumir `GET /permissions/user/{user_id}`.
2. Guardar `menu` en estado global y renderizar navegación desde ese arreglo.
3. Mantener utilidades locales `canView/canEdit/canDelete` usando `permissions`.
4. Usar `/permissions/check` sólo para doble validación en acciones críticas.
