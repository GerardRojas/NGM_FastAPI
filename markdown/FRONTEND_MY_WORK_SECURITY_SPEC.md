# Especificación para Frontend: My Work + Workload (Ajuste de seguridad y manejo de datos)

## 1. Objetivo
Actualizar el frontend de `my-work` para consumir los endpoints protegidos con JWT y permisos de módulo `my_work`, asegurando:
1. Envío de token `Bearer` en todas las requests.
2. Manejo correcto de errores `401` y `403`.
3. Extracción y normalización consistente de payloads de respuesta.

---

## 2. Endpoints a consumir

### Lectura
1. `GET /pipeline/my-work/{user_id}`
2. `GET /pipeline/my-work/team-overview`
3. `GET /pipeline/workload/user/{user_id}`
4. `GET /pipeline/workload/team`
5. `GET /pipeline/workload/next-available/{user_id}`

### Escritura
1. `PUT /pipeline/workload/capacity/{user_id}`
2. `POST /pipeline/workload/schedule-task`
3. `POST /pipeline/workload/recalculate/{user_id}`

---

## 3. Reglas de seguridad backend (ya activas)
1. Todas las rutas requieren token JWT en header: `Authorization: Bearer <token>`.
2. Si el token es inválido/expirado o la sesión no está activa: `401`.
3. Permisos por módulo:
   - `my_work:view` para consultas globales o sobre terceros.
   - `my_work:edit` para operaciones de modificación o recálculo.
4. Rutas con `user_id` permiten acceso si:
   - `current_user.user_id === user_id` (propio usuario), o
   - tiene permiso `my_work` correspondiente (`view` o `edit`).

---

## 4. Requisito de implementación frontend

### Cliente HTTP único
```js
export async function apiRequest(path, options = {}) {
  const token =
    sessionStorage.getItem("access_token") ||
    localStorage.getItem("access_token");

  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (res.status === 401) throw new Error("UNAUTHORIZED");
  if (res.status === 403) throw new Error("FORBIDDEN");

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP_${res.status}`);
  }

  return res.json();
}
```

---

## 5. Extracción y normalización por endpoint

### `GET /pipeline/my-work/{user_id}`
Campos esperados:
1. `tasks` (array)
2. `task_types` (array)
3. `workload` (objeto)

Normalización:
```js
const data = await apiRequest(
  `/pipeline/my-work/${userId}?hours_per_day=${hoursPerDay}&days_per_week=${daysPerWeek}`
);

const tasks = Array.isArray(data.tasks) ? data.tasks : [];
const taskTypes = Array.isArray(data.task_types) ? data.task_types : [];
const workload = data.workload || {};
```

### `GET /pipeline/my-work/team-overview`
Campos esperados:
1. `team` (array)
2. `settings` (objeto)

Normalización:
```js
const data = await apiRequest(
  `/pipeline/my-work/team-overview?hours_per_day=${hoursPerDay}&days_per_week=${daysPerWeek}`
);

const team = Array.isArray(data.team) ? data.team : [];
const settings = data.settings || {};
```

### `GET /pipeline/workload/user/{user_id}`
Campos esperados:
1. `user`
2. `capacity`
3. `workload`
4. `current_task`
5. `task_queue`

Normalización:
```js
const data = await apiRequest(`/pipeline/workload/user/${userId}`);

const user = data.user || null;
const capacity = data.capacity || {};
const workload = data.workload || {};
const currentTask = data.current_task || null;
const taskQueue = Array.isArray(data.task_queue) ? data.task_queue : [];
```

### `GET /pipeline/workload/team`
Campos esperados:
1. `team` (array)

Normalización:
```js
const data = await apiRequest(`/pipeline/workload/team`);
const team = Array.isArray(data.team) ? data.team : [];
```

### `GET /pipeline/workload/next-available/{user_id}`
Campos esperados:
1. `user_id`
2. `current_pending_hours`
3. `estimated_hours_requested`
4. `available_start_date`
5. `estimated_end_date`
6. `days_until_available`
7. `effective_hours_per_day`

Normalización:
```js
const data = await apiRequest(
  `/pipeline/workload/next-available/${userId}?estimated_hours=${estimatedHours}`
);
```

---

## 6. Requests de escritura

### `PUT /pipeline/workload/capacity/{user_id}`
Body ejemplo:
```json
{
  "hours_per_day": 8,
  "days_per_week": 5,
  "working_days": [1, 2, 3, 4, 5],
  "buffer_percent": 20
}
```

### `POST /pipeline/workload/schedule-task`
Body ejemplo:
```json
{
  "task_id": "UUID",
  "force_reschedule": false
}
```

### `POST /pipeline/workload/recalculate/{user_id}`
Sin body.

---

## 7. Manejo UX de errores
1. `UNAUTHORIZED` (`401`):
   - Limpiar sesión local/token.
   - Mostrar mensaje: "Tu sesión expiró, inicia sesión de nuevo".
   - Redirigir a login.
2. `FORBIDDEN` (`403`):
   - Mostrar mensaje: "No tienes permisos para esta acción".
   - No reintentar automáticamente.
3. Otros errores:
   - Mostrar mensaje genérico.
   - Permitir reintento manual.

---

## 8. Checklist QA
1. Todas las llamadas listadas incluyen `Authorization: Bearer ...`.
2. Un usuario puede consultar su propio `user_id`.
3. Un usuario sin permisos no puede consultar `user_id` de terceros.
4. `team-overview` y `workload/team` fallan con `403` cuando no hay `my_work:view`.
5. `capacity/schedule/recalculate` fallan con `403` cuando no hay `my_work:edit`.
6. El frontend maneja `401/403` sin romper pantalla.
