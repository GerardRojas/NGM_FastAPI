# NGM Data Intelligence — Planning Document

> **Fecha:** 2026-02-20
> **Scope:** Backend endpoints + Frontend pages para aprovechar la data centralizada
> **Prioridad:** Enriquecer páginas existentes (Projects, Vendors) + Timeline Manager standalone

---

## Tabla de Contenidos

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura General](#2-arquitectura-general)
3. [Charting Library — Decisión Técnica](#3-charting-library)
4. [Tab System Reutilizable](#4-tab-system-reutilizable)
5. [Feature 1: Project Dashboard (Pestañas en Projects)](#5-feature-1-project-dashboard)
6. [Feature 2: Cost Projection vs Real](#6-feature-2-cost-projection-vs-real)
7. [Feature 3: Executive KPIs en Projects](#7-feature-3-executive-kpis)
8. [Feature 4: Vendor Intelligence](#8-feature-4-vendor-intelligence)
9. [Feature 5: Timeline Manager (Página Standalone)](#9-feature-5-timeline-manager)
10. [Permisos — Nuevos Módulos](#10-permisos-nuevos-modulos)
11. [Memory Leak Prevention Checklist](#11-memory-leak-prevention)
12. [Endpoints Existentes que se Reutilizan](#12-endpoints-existentes)
13. [Endpoints Nuevos Requeridos](#13-endpoints-nuevos)
14. [Plan de Ejecución por Fases](#14-plan-de-ejecucion)
15. [Riesgos y Mitigaciones](#15-riesgos)

---

## 1. Resumen Ejecutivo

NGM tiene 77+ tablas con data de gastos, presupuestos, proyectos, vendors, tareas, materiales y agentes AI — todo centralizado en Supabase. Hoy esa data se consume de forma aislada (una tabla/página por dominio). Este plan conecta esa data en dashboards accionables dentro de las páginas que ya existen.

**Lo que se construye:**

| Feature | Ubicación | Tipo |
|---------|-----------|------|
| Project Health Dashboard | `projects.html` → pestaña nueva | Enriquecer página existente |
| Cost Projection vs Real | `projects.html` → pestaña nueva | Enriquecer página existente |
| Executive KPIs | `projects.html` → pestaña nueva | Enriquecer página existente |
| Vendor Intelligence | `vendors.html` → sección nueva | Enriquecer página existente |
| Timeline Manager | `timeline-manager.html` (nueva) | Página standalone |

**Lo que NO se toca:** Ninguna función existente se modifica. Todo es aditivo.

---

## 2. Arquitectura General

```
┌─────────────────────────────────────────────────┐
│                   FRONTEND (NGM_HUB)            │
│                                                 │
│  projects.html ──► projects_dashboard.js (NEW)  │
│                ──► projects_kpis.js (NEW)        │
│                ──► projects_cost.js (NEW)        │
│  vendors.html  ──► vendors_intelligence.js (NEW)│
│  timeline-manager.html ──► timeline.js (NEW)    │
│                                                 │
│  Shared:                                        │
│  ├── ngm_tabs.js (NEW — tab system reutilizable)│
│  ├── ngm_charts.js (NEW — Chart.js wrapper)     │
│  └── api.js (EXISTING — no se modifica)         │
└──────────────────────┬──────────────────────────┘
                       │ window.NGM.api()
                       ▼
┌─────────────────────────────────────────────────┐
│                   BACKEND (NGM_FastAPI)          │
│                                                 │
│  NUEVOS ROUTERS:                                │
│  ├── /analytics/projects/{id}/health            │
│  ├── /analytics/projects/{id}/cost-trends       │
│  ├── /analytics/projects/{id}/budget-vs-actual  │
│  ├── /analytics/projects/{id}/kpis              │
│  ├── /analytics/vendors/intelligence            │
│  ├── /analytics/vendors/{id}/scorecard          │
│  └── /timeline/* (CRUD + Gantt data)            │
│                                                 │
│  REUTILIZA:                                     │
│  ├── /daneel/auto-auth/project-summary          │
│  ├── /expenses/summary/by-txn-type              │
│  ├── /expenses/summary/by-project               │
│  ├── /budget-alerts/summary                     │
│  ├── /qbo/pnl                                   │
│  └── /pending-receipts/unprocessed/count         │
└─────────────────────────────────────────────────┘
```

### Principio: Separación de archivos JS

Cada pestaña/feature tiene su propio archivo `.js`. No se infla `projects.js` ni `vendors.js` existentes. Los archivos existentes solo reciben un hook mínimo para inicializar las pestañas:

```javascript
// En projects.js existente — UNICA modificación (3 líneas al final):
if (window.NGMTabs) {
  window.NGMTabs.init('projects-tabs');
}
```

---

## 3. Charting Library

### Decisión: **Chart.js v4 via CDN**

| Criterio | Chart.js | D3.js | ApexCharts |
|----------|----------|-------|------------|
| Vanilla JS friendly | ✅ Nativo | ✅ Nativo | ✅ Nativo |
| Peso (gzip) | ~70KB | ~90KB | ~130KB |
| Curva aprendizaje | Baja | Alta | Media |
| Tipos de gráfico | 8 built-in | Ilimitado | 12 built-in |
| Dark theme | Plugin | Manual | Built-in |
| Responsive | Built-in | Manual | Built-in |
| Uso en producción | Masivo | Masivo | Alto |

**Chart.js gana** porque: vanilla JS nativo, ligero, dark theme con plugin, y cubre todos los gráficos que necesitamos (bar, line, doughnut, radar).

### Integración

```html
<!-- En <head> de cada página que use charts -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
```

### Wrapper reutilizable: `ngm_charts.js`

```javascript
// assets/js/ngm_charts.js — Wrapper para Chart.js con tema NGM
window.NGMCharts = (() => {
  const NGM_COLORS = {
    primary: '#3ecf8e',
    danger: '#ef4444',
    warning: '#f59e0b',
    info: '#3b82f6',
    muted: '#6b7280',
    bg: '#1a1a2e',
    grid: '#2a2a3e',
    text: '#e2e8f0',
    palette: [
      '#3ecf8e', '#3b82f6', '#f59e0b', '#ef4444',
      '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'
    ]
  };

  // Defaults globales para dark theme
  Chart.defaults.color = NGM_COLORS.text;
  Chart.defaults.borderColor = NGM_COLORS.grid;
  Chart.defaults.backgroundColor = NGM_COLORS.bg;

  // Registry de charts activos (para cleanup)
  const _activeCharts = new Map();

  function create(canvasId, config) {
    // Destruir chart previo si existe (previene memory leak)
    destroy(canvasId);

    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const chart = new Chart(ctx, config);
    _activeCharts.set(canvasId, chart);
    return chart;
  }

  function destroy(canvasId) {
    const existing = _activeCharts.get(canvasId);
    if (existing) {
      existing.destroy();
      _activeCharts.delete(canvasId);
    }
  }

  function destroyAll() {
    _activeCharts.forEach((chart, id) => {
      chart.destroy();
    });
    _activeCharts.clear();
  }

  // Pre-built chart configs
  function barChart(canvasId, { labels, datasets, stacked = false }) { ... }
  function lineChart(canvasId, { labels, datasets, fill = false }) { ... }
  function doughnutChart(canvasId, { labels, data, colors }) { ... }

  return { create, destroy, destroyAll, NGM_COLORS, barChart, lineChart, doughnutChart };
})();

// Cleanup global
window.addEventListener('beforeunload', () => {
  if (window.NGMCharts) window.NGMCharts.destroyAll();
});
```

**Memory leak prevention:** Cada chart se registra en `_activeCharts`. Al cambiar de pestaña o salir de la página, se destruyen todos.

---

## 4. Tab System Reutilizable

### `ngm_tabs.js` + `ngm_tabs.css`

Sistema de pestañas genérico que se puede usar en cualquier página.

```javascript
// assets/js/ngm_tabs.js
window.NGMTabs = (() => {
  const _instances = new Map();

  function init(containerId, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const buttons = container.querySelectorAll('[data-tab]');
    const panels = container.querySelectorAll('[data-tab-panel]');
    const onSwitch = options.onSwitch || null; // callback(tabKey)
    const permissionCheck = options.permissionCheck || null; // fn(tabKey) → bool

    buttons.forEach(btn => {
      // Verificar permisos antes de mostrar pestaña
      const tabKey = btn.dataset.tab;
      if (permissionCheck && !permissionCheck(tabKey)) {
        btn.style.display = 'none';
        return;
      }

      btn.addEventListener('click', () => switchTab(containerId, tabKey));
    });

    _instances.set(containerId, { buttons, panels, onSwitch });

    // Activar primera pestaña visible
    const firstVisible = [...buttons].find(b => b.style.display !== 'none');
    if (firstVisible) switchTab(containerId, firstVisible.dataset.tab);
  }

  function switchTab(containerId, tabKey) {
    const inst = _instances.get(containerId);
    if (!inst) return;

    inst.buttons.forEach(b => b.classList.toggle('ngm-tab-active', b.dataset.tab === tabKey));
    inst.panels.forEach(p => {
      const isActive = p.dataset.tabPanel === tabKey;
      p.classList.toggle('ngm-tab-active', isActive);
      p.hidden = !isActive;
    });

    if (inst.onSwitch) inst.onSwitch(tabKey);
  }

  function destroy(containerId) {
    _instances.delete(containerId);
  }

  return { init, switchTab, destroy };
})();
```

### Integración con permisos

Las pestañas que contienen data sensible (KPIs ejecutivos) se validan contra el sistema de permisos existente:

```javascript
NGMTabs.init('projects-tabs', {
  permissionCheck: (tabKey) => {
    if (tabKey === 'kpis') {
      return window.NGM.permissions.canView('project_kpis');
    }
    return true; // pestañas públicas
  },
  onSwitch: (tabKey) => {
    // Lazy-load: solo carga data cuando se activa la pestaña
    if (tabKey === 'dashboard') window.ProjectDashboard?.load();
    if (tabKey === 'cost')      window.ProjectCost?.load();
    if (tabKey === 'kpis')      window.ProjectKPIs?.load();
  }
});
```

---

## 5. Feature 1: Project Dashboard (Pestaña en Projects)

### Qué muestra

Vista de salud de un proyecto seleccionado. Se activa al hacer clic en un proyecto de la tabla existente.

```
┌─────────────────────────────────────────────────────┐
│  [Lista ▼] [Dashboard] [Costo vs Proyección] [KPIs]│
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Budget  │ │ Gastado  │ │ Pendiente│ │ Receipts│  │
│  │ $120K   │ │ $85K     │ │ $12K     │ │ 8 pend │  │
│  │         │ │ 70.8%    │ │ auth     │ │        │  │
│  └─────────┘ └──────────┘ └──────────┘ └────────┘  │
│                                                     │
│  ┌──────────────────────┐ ┌──────────────────────┐  │
│  │  Gasto por Categoría │ │  Top 5 Vendors       │  │
│  │  [Doughnut Chart]    │ │  [Horizontal Bar]    │  │
│  │                      │ │                      │  │
│  └──────────────────────┘ └──────────────────────┘  │
│                                                     │
│  ┌──────────────────────┐ ┌──────────────────────┐  │
│  │  Gasto Mensual       │ │  Daneel Stats        │  │
│  │  [Line Chart]        │ │  Auto-auth rate: 78% │  │
│  │                      │ │  Pending info: 5     │  │
│  └──────────────────────┘ └──────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Tareas del Proyecto                         │   │
│  │  ████████░░ 75% completas (12/16)            │   │
│  │  Backlog: 2 | In Progress: 2 | Done: 12     │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Archivos involucrados

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `projects.html` | MODIFICAR | Agregar estructura de tabs + containers para panels |
| `assets/js/projects.js` | MODIFICAR | Hook mínimo: init tabs + click handler en filas |
| `assets/js/projects_dashboard.js` | NUEVO | Lógica del dashboard |
| `assets/css/projects_dashboard.css` | NUEVO | Estilos del dashboard |
| `api/routers/analytics.py` | NUEVO | Endpoints de analytics |

### Backend endpoint: `GET /analytics/projects/{project_id}/health`

```python
# Response shape
{
  "project_id": "uuid",
  "project_name": "Sunset Remodel",

  # Budget overview
  "budget_total": 120000.00,
  "spent_total": 85000.00,
  "spent_percent": 70.83,
  "remaining": 35000.00,

  # Authorization status
  "pending_auth_count": 12,
  "pending_auth_amount": 8500.00,
  "authorized_count": 150,
  "authorized_amount": 76500.00,

  # Pending receipts
  "pending_receipts_count": 8,

  # Gasto por categoría (txn_type)
  "by_category": [
    {"name": "Materials", "amount": 45000, "count": 80},
    {"name": "Labor", "amount": 30000, "count": 50},
    {"name": "Equipment", "amount": 10000, "count": 20}
  ],

  # Top vendors
  "top_vendors": [
    {"vendor_name": "Home Depot", "amount": 25000, "count": 45},
    {"vendor_name": "Lowes", "amount": 15000, "count": 30},
    ...
  ],

  # Gasto mensual (últimos 12 meses)
  "monthly_spend": [
    {"month": "2025-03", "amount": 12000},
    {"month": "2025-04", "amount": 15000},
    ...
  ],

  # Daneel stats
  "daneel": {
    "authorization_rate": 78.5,
    "pending_info": 5,
    "total_processed": 200
  },

  # Tareas
  "tasks": {
    "total": 16,
    "completed": 12,
    "in_progress": 2,
    "backlog": 2,
    "completion_percent": 75.0
  }
}
```

### Backend implementation notes

```python
# api/routers/analytics.py
from fastapi import APIRouter, Depends
from api.auth import get_current_user
from api.supabase_client import supabase  # singleton — NO crear nuevo

router = APIRouter(prefix="/analytics", tags=["Analytics"])

@router.get("/projects/{project_id}/health")
async def project_health(project_id: str, user=Depends(get_current_user)):
    """
    Agrega data de múltiples tablas en un solo response.
    Cada query es independiente → se pueden paralelizar con asyncio.gather
    si se migra a async client. Por ahora, secuencial.

    IMPORTANTE: Todas las queries filtran por project_id.
    No se usa .select("*") — solo columnas necesarias.
    Paginación en expenses con loop (límite 1000 por page).
    """
    # 1. Budget total
    budget_resp = supabase.table("budgets_qbo") \
        .select("amount_sum") \
        .eq("ngm_project_id", project_id) \
        .execute()

    # 2. Expenses aggregation (con paginación para >1000 rows)
    # ... usar el patrón existente de bva_handler.py

    # 3. Pending receipts count
    receipts_resp = supabase.table("pending_receipts") \
        .select("id", count="exact") \
        .eq("project_id", project_id) \
        .is_("expense_id", "null") \
        .execute()

    # 4. Tasks grouped by status
    tasks_resp = supabase.table("tasks") \
        .select("task_id, status_id, tasks_status(status_name)") \
        .eq("project_id", project_id) \
        .execute()

    # ... combinar y retornar
```

### Memory leak prevention (frontend)

```javascript
// projects_dashboard.js
window.ProjectDashboard = (() => {
  let _loaded = false;
  let _abortController = null;

  async function load(projectId) {
    // 1. Cancelar request anterior si está en vuelo
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();

    // 2. Destruir charts anteriores
    window.NGMCharts.destroy('chart-by-category');
    window.NGMCharts.destroy('chart-top-vendors');
    window.NGMCharts.destroy('chart-monthly-spend');

    try {
      const data = await window.NGM.api(
        `/analytics/projects/${projectId}/health`,
        { signal: _abortController.signal }
      );
      renderCards(data);
      renderCharts(data);
      _loaded = true;
    } catch (err) {
      if (err.name === 'AbortError') return; // ignorar — request cancelado
      console.error('Dashboard load failed:', err);
    }
  }

  function unload() {
    // Llamado al cambiar de pestaña o de proyecto
    window.NGMCharts.destroy('chart-by-category');
    window.NGMCharts.destroy('chart-top-vendors');
    window.NGMCharts.destroy('chart-monthly-spend');
    _loaded = false;
    if (_abortController) {
      _abortController.abort();
      _abortController = null;
    }
  }

  return { load, unload };
})();
```

---

## 6. Feature 2: Cost Projection vs Real

### Qué muestra

Contraste entre el modelo de proyección (cómo *debería* ir el gasto según el presupuesto y timeline) vs el gasto real acumulado.

```
┌─────────────────────────────────────────────────────┐
│  [Lista] [Dashboard] [Costo vs Proyección ▼] [KPIs]│
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  BUDGET vs ACTUAL — Acumulado                │   │
│  │                                              │   │
│  │  $120K ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ budget │   │
│  │       /                    ___--- proyección │   │
│  │      / ___---─────────────        (lineal)   │   │
│  │     //                                       │   │
│  │    /   ──── gasto real (acumulado)           │   │
│  │   /                                          │   │
│  │  Jan  Feb  Mar  Apr  May  Jun  Jul  Aug      │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────┐ ┌──────────────────────┐  │
│  │  Variance por Cuenta │ │  Proyección a cierre │  │
│  │  [Horizontal bar]    │ │                      │  │
│  │  Materials: +$5K ██▓ │ │  Estimado:  $118K    │  │
│  │  Labor:    -$2K ██░  │ │  Budget:    $120K    │  │
│  │  Equipment: +$1K █▓  │ │  Diferencia: -$2K   │  │
│  └──────────────────────┘ │  Status: ✅ On track │  │
│                           └──────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Budget vs Actual por Cuenta (tabla)         │   │
│  │  ┌────────────┬────────┬────────┬──────────┐ │   │
│  │  │ Account    │ Budget │ Actual │ Variance │ │   │
│  │  ├────────────┼────────┼────────┼──────────┤ │   │
│  │  │ Materials  │ $50K   │ $55K   │ +$5K ▲   │ │   │
│  │  │ Labor      │ $40K   │ $38K   │ -$2K ▼   │ │   │
│  │  └────────────┴────────┴────────┴──────────┘ │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Backend endpoint: `GET /analytics/projects/{project_id}/budget-vs-actual`

```python
{
  "project_id": "uuid",
  "budget_year": 2025,

  # Por cuenta
  "by_account": [
    {
      "account_name": "Materials",
      "account_id": "uuid",
      "budget": 50000.00,
      "actual": 55000.00,
      "variance": 5000.00,       # actual - budget
      "variance_pct": 10.0       # (variance / budget) * 100
    },
    ...
  ],

  # Totales
  "total_budget": 120000.00,
  "total_actual": 85000.00,
  "total_variance": -35000.00,

  # Serie temporal — gasto real acumulado por mes
  "cumulative_actual": [
    {"month": "2025-01", "cumulative": 12000},
    {"month": "2025-02", "cumulative": 27000},
    ...
  ],

  # Proyección lineal (budget distribuido uniformemente o por peso histórico)
  "projection": [
    {"month": "2025-01", "projected_cumulative": 10000},
    {"month": "2025-02", "projected_cumulative": 20000},
    ...
  ],

  # Estimado a cierre (extrapolación del ritmo actual)
  "estimated_at_completion": 118000.00,
  "estimated_variance": -2000.00,
  "status": "on_track"  # on_track | at_risk | over_budget
}
```

### Lógica de proyección

```python
def calculate_projection(budget_total, project_start, project_end, monthly_actual):
    """
    Modelo simple: distribución lineal del budget a lo largo del timeline.
    Se puede mejorar con pesos por fase si se tiene data de milestones.

    Estimate at Completion (EAC):
    - CPI = budget_total / actual_to_date (Cost Performance Index)
    - EAC = budget_total / CPI
    - Si CPI > 1: under budget. Si CPI < 1: over budget.
    """
    total_months = months_between(project_start, project_end)
    monthly_budget = budget_total / total_months

    # Proyección lineal
    projection = []
    cumulative = 0
    for i in range(total_months):
        cumulative += monthly_budget
        projection.append({"month": ..., "projected_cumulative": cumulative})

    # EAC
    actual_to_date = sum(m["amount"] for m in monthly_actual)
    months_elapsed = len(monthly_actual)
    if months_elapsed > 0 and actual_to_date > 0:
        burn_rate = actual_to_date / months_elapsed
        remaining_months = total_months - months_elapsed
        eac = actual_to_date + (burn_rate * remaining_months)
    else:
        eac = budget_total

    return projection, eac
```

### Frontend: `projects_cost.js`

Mismo patrón IIFE que `projects_dashboard.js`. Charts:
- **Line chart (principal):** 3 datasets — budget ceiling (horizontal), proyección (dashed), gasto real acumulado (solid)
- **Horizontal bar:** variance por cuenta (verde = under, rojo = over)
- **Card:** EAC, budget, diferencia, status badge

---

## 7. Feature 3: Executive KPIs en Projects

### Qué muestra

Vista consolidada multi-proyecto. Visible solo para roles con permiso `project_kpis`.

```
┌─────────────────────────────────────────────────────┐
│  [Lista] [Dashboard] [Costo vs Proyección] [KPIs ▼]│
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Proyect │ │ Gasto    │ │ Margen   │ │ Daneel │  │
│  │ Activos │ │ Total    │ │ Promedio │ │ Auth%  │  │
│  │   12    │ │ $1.2M    │ │  32%     │ │  78%   │  │
│  └─────────┘ └──────────┘ └──────────┘ └────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Burn Rate por Proyecto                      │   │
│  │  [Stacked Bar — budget vs actual por proj]   │   │
│  │                                              │   │
│  │  Proj A  ████████████░░░░ 78%                │   │
│  │  Proj B  ████████░░░░░░░ 55%                 │   │
│  │  Proj C  ██████████████████ 105% ⚠️           │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────┐ ┌──────────────────────┐  │
│  │  Gasto Mensual Total │ │  Vendor Concentración│  │
│  │  [Line — all projs]  │ │  [Doughnut]          │  │
│  └──────────────────────┘ └──────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Tabla KPI por Proyecto                      │   │
│  │  ┌──────┬───────┬───────┬──────┬───────────┐ │   │
│  │  │ Proj │Budget │Actual │Margin│ Status    │ │   │
│  │  ├──────┼───────┼───────┼──────┼───────────┤ │   │
│  │  │ A    │ $120K │ $85K  │ 35%  │ On Track  │ │   │
│  │  │ B    │ $80K  │ $44K  │ 28%  │ On Track  │ │   │
│  │  │ C    │ $200K │ $210K │ -5%  │ Over ⚠️   │ │   │
│  │  └──────┴───────┴───────┴──────┴───────────┘ │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Backend endpoint: `GET /analytics/executive/kpis`

```python
{
  "active_projects": 12,
  "total_spend": 1200000.00,
  "total_budget": 1500000.00,
  "avg_margin_pct": 32.0,
  "daneel_auth_rate": 78.5,

  # Por proyecto
  "projects": [
    {
      "project_id": "uuid",
      "project_name": "Sunset Remodel",
      "budget": 120000,
      "actual": 85000,
      "burn_pct": 70.8,
      "margin_pct": 35.0,
      "status": "on_track",
      "pending_auth": 5,
      "pending_receipts": 3,
      "tasks_completion_pct": 75.0
    },
    ...
  ],

  # Gasto mensual agregado (todos los proyectos)
  "monthly_spend_total": [
    {"month": "2025-01", "amount": 95000},
    ...
  ],

  # Top vendors (across all projects)
  "top_vendors": [
    {"vendor_name": "Home Depot", "amount": 180000, "project_count": 8},
    ...
  ]
}
```

### Control de acceso

Este endpoint verifica que el usuario tenga permiso `project_kpis.can_view`:

```python
@router.get("/executive/kpis")
async def executive_kpis(user=Depends(get_current_user)):
    # Verificar permiso
    perm = supabase.table("role_permissions") \
        .select("can_view") \
        .eq("rol_id", user["role_id"]) \
        .eq("module_key", "project_kpis") \
        .single() \
        .execute()

    if not perm.data or not perm.data.get("can_view"):
        raise HTTPException(403, "No tienes acceso a KPIs ejecutivos")

    # ... agregar data
```

---

## 8. Feature 4: Vendor Intelligence

### Qué muestra

Sección nueva dentro de `vendors.html`, debajo de la tabla existente. Se activa al hacer clic en un vendor.

```
┌─────────────────────────────────────────────────────┐
│  VENDORS                              [+ Add] [Edit]│
│  ┌──────────────────────────────────────────────┐   │
│  │  Search: [________________]                  │   │
│  │  ┌────────────────┬───────────┬──────────┐   │   │
│  │  │ Vendor Name    │ Total $   │ # Txns   │   │   │
│  │  ├────────────────┼───────────┼──────────┤   │   │
│  │  │ Home Depot ►   │ $180,000  │ 245      │   │   │
│  │  │ Lowes          │ $95,000   │ 120      │   │   │
│  │  │ ABC Supply     │ $45,000   │ 60       │   │   │
│  │  └────────────────┴───────────┴──────────┘   │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ═══════ VENDOR INTELLIGENCE: Home Depot ═══════    │
│                                                     │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Total $ │ │ Avg Txn  │ │ Projects │ │ Since  │  │
│  │ $180K   │ │ $735     │ │ 8 active │ │ Jan '24│  │
│  └─────────┘ └──────────┘ └──────────┘ └────────┘  │
│                                                     │
│  ┌──────────────────────┐ ┌──────────────────────┐  │
│  │  Gasto Mensual       │ │  Por Proyecto        │  │
│  │  [Line Chart]        │ │  [Doughnut]          │  │
│  └──────────────────────┘ └──────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Price Trends (por categoría)                │   │
│  │  Materials avg: $450 → $520 (+15.5%) ▲ ⚠️    │   │
│  │  Equipment avg: $200 → $195 (-2.5%) ▼        │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Risk: Concentración                         │   │
│  │  Este vendor representa el 35% del gasto     │   │
│  │  total en Materials. Considere diversificar.  │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Archivos involucrados

| Archivo | Acción |
|---------|--------|
| `vendors.html` | MODIFICAR — agregar container para intelligence panel |
| `assets/js/vendors.js` | MODIFICAR — hook mínimo: click handler en filas |
| `assets/js/vendors_intelligence.js` | NUEVO — lógica de intelligence |
| `assets/css/vendors_intelligence.css` | NUEVO — estilos |

### Backend endpoints

**`GET /analytics/vendors/summary`** — tabla enriquecida (reemplaza tabla básica)

```python
{
  "vendors": [
    {
      "id": "uuid",
      "vendor_name": "Home Depot",
      "total_amount": 180000.00,
      "txn_count": 245,
      "project_count": 8,
      "first_txn_date": "2024-01-15",
      "last_txn_date": "2025-08-20",
      "avg_txn_amount": 734.69,
      "concentration_pct": 15.2  # % del gasto total
    },
    ...
  ],
  "total_spend_all_vendors": 1185000.00
}
```

**`GET /analytics/vendors/{vendor_id}/scorecard`** — detalle de un vendor

```python
{
  "vendor_id": "uuid",
  "vendor_name": "Home Depot",

  # Resumen
  "total_amount": 180000.00,
  "txn_count": 245,
  "avg_txn_amount": 734.69,
  "active_projects": 8,
  "first_txn": "2024-01-15",

  # Gasto mensual
  "monthly_spend": [
    {"month": "2025-01", "amount": 15000},
    ...
  ],

  # Por proyecto
  "by_project": [
    {"project_name": "Sunset", "amount": 45000, "count": 60},
    ...
  ],

  # Por categoría con trend
  "by_category": [
    {
      "txn_type": "Materials",
      "total": 120000,
      "avg_unit_price_3mo_ago": 450.00,
      "avg_unit_price_current": 520.00,
      "price_change_pct": 15.5
    },
    ...
  ],

  # Riesgo de concentración
  "concentration": {
    "pct_of_total_spend": 15.2,
    "pct_of_category_spend": {
      "Materials": 35.0,
      "Equipment": 12.0
    },
    "risk_level": "medium"  # low (<20%) | medium (20-40%) | high (>40%)
  }
}
```

---

## 9. Feature 5: Timeline Manager (Página Standalone)

### Por qué standalone

Timeline Manager es casi otra herramienta. Requiere:
- Vista Gantt interactiva
- Drag & drop para mover tareas
- Dependencias visuales (flechas entre barras)
- Zoom temporal (día / semana / mes)
- Milestones
- Integración futura con MS Project / similar

Esto no cabe como pestaña — necesita espacio completo y su propia lógica pesada.

### Wireframe

```
┌─────────────────────────────────────────────────────────────┐
│  TIMELINE MANAGER                    [Proyecto: ▼ Sunset]  │
│  [Day] [Week] [Month]  ◄ Mar 2025 ►  [+ Milestone] [Export]│
├──────────────┬──────────────────────────────────────────────┤
│              │  W1    W2    W3    W4    W5    W6    W7      │
│  Phase       │  03/01      03/15      03/29      04/12     │
│ ─────────────┼──────────────────────────────────────────────┤
│ Foundation   │  ████████████                                │
│  └ Excavation│  █████                                       │
│  └ Pour      │       ███████                                │
│  └ Cure ◆    │              ◆ (milestone)                   │
│ Framing      │              ███████████████                  │
│  └ Walls     │              ████████                         │
│  └ Roof      │                      ███████                  │
│ Electrical   │                    ██████████████             │
│  └ Rough-in  │                    █████████                  │
│  └ Fixtures  │                             █████            │
│ ─────────────┼──────────────────────────────────────────────┤
│  CRITICAL    │         ─────────────────────────► (path)    │
│  PATH        │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

### Modelo de datos — Tablas nuevas

```sql
-- ================================
-- Migration: Timeline Manager tables
-- ================================

-- Milestones del proyecto
CREATE TABLE IF NOT EXISTS project_milestones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id),
  name TEXT NOT NULL,
  target_date DATE NOT NULL,
  actual_date DATE,             -- NULL si no se ha completado
  status TEXT DEFAULT 'pending', -- pending | completed | delayed
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Fases del proyecto (agrupan tareas en el Gantt)
CREATE TABLE IF NOT EXISTS project_phases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id),
  name TEXT NOT NULL,
  start_date DATE,
  end_date DATE,
  parent_phase_id UUID REFERENCES project_phases(id), -- sub-fases
  milestone_id UUID REFERENCES project_milestones(id),
  color TEXT DEFAULT '#3ecf8e',
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Timeline entries (tareas en el Gantt)
CREATE TABLE IF NOT EXISTS timeline_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id),
  phase_id UUID REFERENCES project_phases(id),
  task_id UUID REFERENCES tasks(task_id),     -- link opcional a pipeline task
  title TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  progress_pct NUMERIC(5,2) DEFAULT 0,        -- 0-100
  assigned_to UUID REFERENCES users(user_id),
  depends_on UUID[] DEFAULT '{}',             -- array de timeline_entry IDs
  is_milestone BOOLEAN DEFAULT FALSE,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Índices
CREATE INDEX idx_timeline_project ON timeline_entries(project_id);
CREATE INDEX idx_timeline_phase ON timeline_entries(phase_id);
CREATE INDEX idx_phases_project ON project_phases(project_id);
CREATE INDEX idx_milestones_project ON project_milestones(project_id);

-- RLS
ALTER TABLE project_milestones ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_phases ENABLE ROW LEVEL SECURITY;
ALTER TABLE timeline_entries ENABLE ROW LEVEL SECURITY;

-- Policy: authenticated users can read/write
-- (Fine-grained perms checked in API layer via role_permissions)
CREATE POLICY "Authenticated access" ON project_milestones
  FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Authenticated access" ON project_phases
  FOR ALL USING (auth.role() = 'authenticated');
CREATE POLICY "Authenticated access" ON timeline_entries
  FOR ALL USING (auth.role() = 'authenticated');
```

### Backend: `api/routers/timeline.py`

```python
router = APIRouter(prefix="/timeline", tags=["Timeline"])

# --- Phases ---
GET    /timeline/projects/{project_id}/phases          # List phases
POST   /timeline/projects/{project_id}/phases          # Create phase
PATCH  /timeline/phases/{phase_id}                     # Update phase
DELETE /timeline/phases/{phase_id}                     # Delete phase

# --- Milestones ---
GET    /timeline/projects/{project_id}/milestones      # List milestones
POST   /timeline/projects/{project_id}/milestones      # Create milestone
PATCH  /timeline/milestones/{milestone_id}             # Update
DELETE /timeline/milestones/{milestone_id}             # Delete

# --- Entries (Gantt bars) ---
GET    /timeline/projects/{project_id}/entries         # All entries for Gantt
POST   /timeline/projects/{project_id}/entries         # Create entry
PATCH  /timeline/entries/{entry_id}                    # Update (move, resize, progress)
DELETE /timeline/entries/{entry_id}                    # Delete
PATCH  /timeline/entries/{entry_id}/progress           # Quick progress update
POST   /timeline/entries/batch-update                  # Batch move (drag multiple)

# --- Gantt view (optimized read) ---
GET    /timeline/projects/{project_id}/gantt            # Full Gantt data
# Returns: phases + entries + milestones + dependencies in one call
```

### Gantt response shape

```python
# GET /timeline/projects/{project_id}/gantt
{
  "project_id": "uuid",
  "project_name": "Sunset Remodel",
  "date_range": {"start": "2025-01-01", "end": "2025-09-30"},

  "phases": [
    {
      "id": "uuid",
      "name": "Foundation",
      "start_date": "2025-01-01",
      "end_date": "2025-02-15",
      "color": "#3ecf8e",
      "entries": [
        {
          "id": "uuid",
          "title": "Excavation",
          "start_date": "2025-01-01",
          "end_date": "2025-01-15",
          "progress_pct": 100,
          "assigned_to": "John",
          "depends_on": [],
          "is_milestone": false
        },
        {
          "id": "uuid",
          "title": "Pour Concrete",
          "start_date": "2025-01-16",
          "end_date": "2025-02-05",
          "progress_pct": 60,
          "depends_on": ["<excavation-id>"]
        }
      ],
      "milestone": {
        "id": "uuid",
        "name": "Foundation Complete",
        "target_date": "2025-02-15",
        "status": "pending"
      }
    }
  ],

  "unphased_entries": [...]  // entries sin fase asignada
}
```

### Frontend: Gantt rendering

Para el Gantt, **NO usar Chart.js** (no soporta Gantt nativo). Opciones:

| Opción | Pros | Contras |
|--------|------|---------|
| **Canvas custom** | Control total, matches codebase style | Mucho trabajo |
| **frappe-gantt** (CDN) | Ligero (40KB), SVG, vanilla JS | Read-only drag limited |
| **dhtmlxGantt** (free) | Completo, drag&drop, dependencies | Más pesado (200KB) |
| **Custom SVG** | Ligero, control total | Trabajo medio |

**Recomendación: frappe-gantt** para v1 (simple, ligero, SVG). Si se queda corto, migrar a custom canvas o dhtmlxGantt.

```html
<!-- timeline-manager.html -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.css">
<script src="https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.umd.min.js"></script>
```

### Memory leak prevention — Timeline

```javascript
// timeline.js
window.TimelineManager = (() => {
  let _gantt = null;
  let _abortController = null;

  function destroy() {
    if (_gantt) {
      // frappe-gantt: limpiar SVG + event listeners
      const svg = document.querySelector('.gantt');
      if (svg) svg.innerHTML = '';
      _gantt = null;
    }
    if (_abortController) {
      _abortController.abort();
      _abortController = null;
    }
  }

  async function load(projectId) {
    destroy(); // siempre limpiar antes de cargar
    _abortController = new AbortController();

    const data = await window.NGM.api(
      `/timeline/projects/${projectId}/gantt`,
      { signal: _abortController.signal }
    );

    const tasks = flattenToGanttTasks(data);
    _gantt = new Gantt('#gantt-container', tasks, {
      view_mode: 'Week',
      on_date_change: (task, start, end) => updateEntry(task.id, { start_date: start, end_date: end }),
      on_progress_change: (task, progress) => updateProgress(task.id, progress),
    });
  }

  window.addEventListener('beforeunload', destroy);

  return { load, destroy };
})();
```

---

## 10. Permisos — Nuevos Módulos

### Nuevos módulos en `role_permissions`

```sql
-- Agregar módulos para dashboards
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
  r.rol_id,
  m.module_key,
  m.module_name,
  m.module_url,
  CASE WHEN r.rol_name IN ('CEO', 'COO') THEN TRUE ELSE FALSE END,
  CASE WHEN r.rol_name IN ('CEO', 'COO') THEN TRUE ELSE FALSE END,
  FALSE
FROM rols r
CROSS JOIN (VALUES
  ('project_dashboard', 'Project Dashboard', 'projects.html#dashboard'),
  ('project_kpis',      'Executive KPIs',     'projects.html#kpis'),
  ('cost_projection',   'Cost Projection',    'projects.html#cost'),
  ('vendor_intelligence','Vendor Intelligence','vendors.html#intelligence'),
  ('timeline_manager',  'Timeline Manager',   'timeline-manager.html')
) AS m(module_key, module_name, module_url);
```

### Frontend permission check

```javascript
// En cada módulo nuevo, verificar permiso antes de cargar
async function checkModuleAccess(moduleKey) {
  const perms = JSON.parse(localStorage.getItem('sidebar_permissions') || '[]');
  const mod = perms.find(p => p.module_key === moduleKey);
  return mod && mod.can_view;
}
```

### Sidebar — Timeline Manager aparece como página aparte

```javascript
// sidebar.js — agregar entrada para Timeline Manager
// Solo visible si tiene permiso timeline_manager.can_view
{
  module_key: 'timeline_manager',
  module_name: 'Timeline Manager',
  module_url: 'timeline-manager.html',
  icon: 'calendar-gantt' // o el icono que corresponda
}
```

---

## 11. Memory Leak Prevention Checklist

Cada nuevo archivo JS **DEBE** cumplir estos 7 puntos:

| # | Regla | Cómo |
|---|-------|------|
| 1 | **Chart cleanup** | Usar `NGMCharts.destroy(id)` antes de re-render y en `beforeunload` |
| 2 | **AbortController** | Toda request cancela la anterior: `if (_ac) _ac.abort()` |
| 3 | **Intervals/Timeouts** | Guardar referencia, limpiar en `beforeunload` |
| 4 | **Event listeners** | `{ once: true }` o `removeEventListener` explícito |
| 5 | **DOM references** | No guardar refs a nodos que se van a destruir (tabs) |
| 6 | **Gantt/SVG** | Limpiar `innerHTML` del container SVG al destruir |
| 7 | **Lazy loading** | Solo cargar data cuando la pestaña se activa (no al abrir página) |

### Template para cada nuevo módulo JS

```javascript
window.ModuleName = (() => {
  // --- State ---
  let _loaded = false;
  let _abortController = null;
  let _pollInterval = null;

  // --- Public ---
  async function load(params) {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    // ... fetch + render
    _loaded = true;
  }

  function unload() {
    if (_abortController) { _abortController.abort(); _abortController = null; }
    if (_pollInterval) { clearInterval(_pollInterval); _pollInterval = null; }
    // Destroy charts
    window.NGMCharts?.destroy('chart-id-1');
    window.NGMCharts?.destroy('chart-id-2');
    _loaded = false;
  }

  // --- Cleanup ---
  window.addEventListener('beforeunload', unload);

  return { load, unload };
})();
```

---

## 12. Endpoints Existentes que se Reutilizan

Estos endpoints YA existen y se pueden consumir directamente sin cambios:

| Endpoint | Uso en Feature | Data que provee |
|----------|----------------|-----------------|
| `GET /daneel/auto-auth/project-summary` | Project Dashboard | Expense breakdown por proyecto |
| `GET /expenses/summary/by-txn-type?project={id}` | Project Dashboard | Gasto por categoría |
| `GET /expenses/summary/by-project` | Executive KPIs | Gasto total por proyecto |
| `GET /budget-alerts/summary?project_id={id}` | Project Dashboard | Alertas activas |
| `GET /pending-receipts/unprocessed/count` | Project Dashboard | Receipts pendientes |
| `GET /qbo/pnl?project={id}` | Cost Projection | P&L completo |
| `GET /pipeline/grouped` | Project Dashboard (tasks) | Tareas por status |
| `GET /pipeline/my-work/team-overview` | Executive KPIs | Workload del equipo |
| `GET /daneel/auto-auth/status` | Executive KPIs | Auth rate global |
| `GET /vendors` | Vendor Intelligence | Lista base de vendors |
| `GET /budgets?project={id}` | Cost Projection | Budget por proyecto |
| `GET /projects/meta` | Todos | Dropdown de proyectos |
| `GET /permissions/user/{id}` | Todos | Verificar acceso a módulos |

**NOTA:** Ninguno de estos endpoints se modifica. Se consumen tal como están.

---

## 13. Endpoints Nuevos Requeridos

### Router: `api/routers/analytics.py`

| # | Método | Path | Feature | Descripción |
|---|--------|------|---------|-------------|
| 1 | GET | `/analytics/projects/{id}/health` | Project Dashboard | Salud agregada de un proyecto |
| 2 | GET | `/analytics/projects/{id}/cost-trends` | Project Dashboard | Serie temporal de gasto mensual |
| 3 | GET | `/analytics/projects/{id}/budget-vs-actual` | Cost Projection | Variance por cuenta + proyección |
| 4 | GET | `/analytics/executive/kpis` | Executive KPIs | Multi-proyecto aggregation |
| 5 | GET | `/analytics/vendors/summary` | Vendor Intelligence | Tabla enriquecida de vendors |
| 6 | GET | `/analytics/vendors/{id}/scorecard` | Vendor Intelligence | Detalle de un vendor |

### Router: `api/routers/timeline.py`

| # | Método | Path | Descripción |
|---|--------|------|-------------|
| 7 | GET | `/timeline/projects/{id}/gantt` | Full Gantt data (phases + entries + milestones) |
| 8 | POST | `/timeline/projects/{id}/phases` | Crear fase |
| 9 | PATCH | `/timeline/phases/{id}` | Actualizar fase |
| 10 | DELETE | `/timeline/phases/{id}` | Eliminar fase |
| 11 | POST | `/timeline/projects/{id}/milestones` | Crear milestone |
| 12 | PATCH | `/timeline/milestones/{id}` | Actualizar milestone |
| 13 | DELETE | `/timeline/milestones/{id}` | Eliminar milestone |
| 14 | POST | `/timeline/projects/{id}/entries` | Crear entrada Gantt |
| 15 | PATCH | `/timeline/entries/{id}` | Actualizar entrada (move, resize) |
| 16 | DELETE | `/timeline/entries/{id}` | Eliminar entrada |
| 17 | PATCH | `/timeline/entries/{id}/progress` | Quick progress update |
| 18 | POST | `/timeline/entries/batch-update` | Batch move |

### Consideraciones de implementación backend

```python
# TODAS las queries DEBEN:

# 1. Usar el singleton de supabase
from api.supabase_client import supabase   # ← SIEMPRE desde aquí

# 2. Usar get_current_user para auth
from api.auth import get_current_user
@router.get("/...")
async def endpoint(user=Depends(get_current_user)):

# 3. Paginar queries de expenses (límite 1000 por page)
# Patrón de bva_handler.py:
all_rows = []
page = 0
while True:
    start = page * 1000
    resp = supabase.table("expenses_manual_COGS") \
        .select("Amount, TxnDate, vendor_id, txn_type_id, account_id") \
        .eq("project_id", project_id) \
        .eq("status", "authorized") \
        .range(start, start + 999) \
        .execute()
    all_rows.extend(resp.data)
    if len(resp.data) < 1000:
        break
    page += 1

# 4. No usar .select("*") — solo columnas necesarias
# 5. No crear nuevas instancias de OpenAI/Supabase client
# 6. Manejar errores con try/except y HTTPException
```

---

## 14. Plan de Ejecución por Fases

### Fase 0: Infraestructura Compartida (1 sprint)

**Entregables:**
- [ ] `assets/js/ngm_tabs.js` — Tab system reutilizable
- [ ] `assets/css/ngm_tabs.css` — Estilos de tabs
- [ ] `assets/js/ngm_charts.js` — Chart.js wrapper con tema NGM
- [ ] Chart.js CDN link en páginas que lo necesiten
- [ ] `api/routers/analytics.py` — Router vacío registrado en `main.py`
- [ ] Migration SQL: nuevos módulos en `role_permissions`

**Verificación:** Tab system funciona en projects.html con tabs placeholder.

---

### Fase 1: Project Dashboard (2 sprints)

**Backend:**
- [ ] `GET /analytics/projects/{id}/health` — endpoint completo
- [ ] `GET /analytics/projects/{id}/cost-trends` — serie temporal

**Frontend:**
- [ ] Modificar `projects.html` — agregar estructura de tabs
- [ ] Modificar `projects.js` — hook mínimo (init tabs + project click)
- [ ] `assets/js/projects_dashboard.js` — lógica completa
- [ ] `assets/css/projects_dashboard.css` — estilos
- [ ] KPI cards (budget, gastado, pendiente, receipts)
- [ ] Doughnut: gasto por categoría
- [ ] Horizontal bar: top 5 vendors
- [ ] Line chart: gasto mensual
- [ ] Progress bar: tareas completadas
- [ ] Daneel stats card

**Verificación:** Dashboard carga al seleccionar un proyecto. Charts se destruyen al cambiar de proyecto.

---

### Fase 2: Cost Projection vs Real (1 sprint)

**Backend:**
- [ ] `GET /analytics/projects/{id}/budget-vs-actual` — con proyección y EAC

**Frontend:**
- [ ] `assets/js/projects_cost.js`
- [ ] `assets/css/projects_cost.css`
- [ ] Line chart principal: budget ceiling + proyección + gasto real acumulado
- [ ] Horizontal bar: variance por cuenta
- [ ] Card: EAC, budget, diferencia, status badge
- [ ] Tabla: budget vs actual por cuenta

**Verificación:** Gráfico de proyección vs real se actualiza al cambiar proyecto.

---

### Fase 3: Executive KPIs (1 sprint)

**Backend:**
- [ ] `GET /analytics/executive/kpis` — aggregation multi-proyecto con permission check

**Frontend:**
- [ ] `assets/js/projects_kpis.js`
- [ ] `assets/css/projects_kpis.css`
- [ ] KPI cards globales (proyectos activos, gasto total, margen, Daneel rate)
- [ ] Stacked bar: burn rate por proyecto
- [ ] Line: gasto mensual total
- [ ] Doughnut: vendor concentración
- [ ] Tabla: KPI por proyecto
- [ ] Permission check: ocultar pestaña si no tiene `project_kpis.can_view`

**Verificación:** Solo CEO/COO ven la pestaña. Data de todos los proyectos activos se agrega correctamente.

---

### Fase 4: Vendor Intelligence (1 sprint)

**Backend:**
- [ ] `GET /analytics/vendors/summary` — tabla enriquecida
- [ ] `GET /analytics/vendors/{id}/scorecard` — detalle con trends

**Frontend:**
- [ ] Modificar `vendors.html` — agregar intelligence panel
- [ ] Modificar `vendors.js` — hook mínimo (click handler + enriched table)
- [ ] `assets/js/vendors_intelligence.js` — lógica completa
- [ ] `assets/css/vendors_intelligence.css` — estilos
- [ ] Tabla enriquecida: vendor + total $ + # txns
- [ ] Scorecard panel: cards + charts al hacer clic
- [ ] Price trends con alertas de cambio significativo
- [ ] Concentración risk badge

**Verificación:** Click en vendor muestra scorecard. Charts se destruyen al cambiar de vendor.

---

### Fase 5: Timeline Manager (2-3 sprints)

**Backend:**
- [ ] Migration SQL: `project_milestones`, `project_phases`, `timeline_entries`
- [ ] `api/routers/timeline.py` — CRUD completo (12 endpoints)
- [ ] `GET /timeline/projects/{id}/gantt` — read optimizado

**Frontend:**
- [ ] `timeline-manager.html` — página nueva
- [ ] `assets/js/timeline.js` — lógica completa
- [ ] `assets/css/timeline.css` — estilos
- [ ] Integrar frappe-gantt (o alternativa)
- [ ] Project selector dropdown
- [ ] Phase management (crear, editar, eliminar)
- [ ] Milestone management
- [ ] Entry CRUD (crear, mover, resize barras)
- [ ] Dependency lines (visual)
- [ ] Zoom: Day / Week / Month
- [ ] Sidebar: entry details panel
- [ ] Agregar a sidebar navigation con permission check
- [ ] Registrar módulo `timeline_manager` en permissions

**Verificación:** Gantt renderiza correctamente. Drag & drop actualiza backend. SVG se limpia al cambiar proyecto.

---

## 15. Riesgos y Mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| **Queries lentas en analytics** | Dashboard tarda >3s | Indexes en `expenses_manual_COGS(project_id, status, TxnDate)`. Cache en frontend (5 min TTL). |
| **Supabase 1000-row limit** | Data truncada | Ya tenemos patrón de paginación en `bva_handler.py` — replicar. |
| **Memory leaks en charts** | Browser se ralentiza | `NGMCharts.destroy()` obligatorio. Template de módulo con cleanup. |
| **Chart.js CDN down** | Charts no cargan | Fallback: guardar copia local en `assets/vendor/chart.min.js`. |
| **Permisos mal configurados** | Data sensible expuesta | Backend verifica permisos EN CADA endpoint. Frontend solo oculta UI. |
| **Gantt library limitada** | UX pobre en timeline | Evaluar frappe-gantt en Fase 5, si no alcanza → custom canvas. |
| **Conflictos con JS existente** | Funciones existentes se rompen | Cada feature en su propio IIFE. No se modifica lógica existente — solo hooks mínimos. |
| **N+1 queries en analytics** | Backend lento | Agregar en un solo endpoint con queries paralelas, no llamadas individuales desde frontend. |

---

## Resumen de Archivos

### Nuevos (Frontend — NGM_HUB)

```
assets/js/ngm_tabs.js              ← Tab system reutilizable
assets/js/ngm_charts.js            ← Chart.js wrapper
assets/js/projects_dashboard.js    ← Project health dashboard
assets/js/projects_cost.js         ← Cost projection vs real
assets/js/projects_kpis.js         ← Executive KPIs
assets/js/vendors_intelligence.js  ← Vendor scorecard
assets/js/timeline.js              ← Timeline Manager
assets/css/ngm_tabs.css            ← Tab styles
assets/css/projects_dashboard.css  ← Dashboard styles
assets/css/projects_cost.css       ← Cost projection styles
assets/css/projects_kpis.css       ← KPIs styles
assets/css/vendors_intelligence.css← Vendor intelligence styles
assets/css/timeline.css            ← Timeline styles
timeline-manager.html              ← Nueva página
```

### Nuevos (Backend — NGM_FastAPI)

```
api/routers/analytics.py           ← Analytics endpoints (6 endpoints)
api/routers/timeline.py            ← Timeline CRUD (12 endpoints)
database/migrations/add_timeline_tables.sql
database/migrations/add_permission_modules.sql
```

### Modificados (mínimamente)

```
NGM_HUB/projects.html              ← Agregar tabs structure
NGM_HUB/assets/js/projects.js      ← Hook: init tabs (3 líneas)
NGM_HUB/vendors.html               ← Agregar intelligence container
NGM_HUB/assets/js/vendors.js       ← Hook: click handler (5 líneas)
NGM_FastAPI/api/main.py             ← Registrar analytics + timeline routers
```

### NO se modifican

```
api/routers/projects.py             ← Intacto
api/routers/expenses.py             ← Intacto
api/routers/budgets.py              ← Intacto
api/routers/vendors.py              ← Intacto
api/routers/reporting.py            ← Intacto
api/routers/daneel.py               ← Intacto
api/routers/pipeline.py             ← Intacto
api/supabase_client.py              ← Intacto
api/auth.py                         ← Intacto
assets/js/api.js                    ← Intacto
assets/js/permissions.js            ← Intacto
assets/js/sidebar.js                ← Solo agregar timeline entry
```
