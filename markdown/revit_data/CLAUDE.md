# NGM Revit Suite

## Filosofia: BIM as Code

Todo el flujo de trabajo de modelado, templates, familias, estilos, vistas y entregables vive en **codigo y JSON en GitHub**, no dentro de un archivo .rvt de 200MB imposible de navegar y compartir.

El archivo Revit es solo un runtime - un blank canvas. Toda la inteligencia (que muros usar, que familias cargar, como nombrar vistas, que sheets crear, que estilos graficos aplicar) esta definida en archivos legibles, versionados y compartibles.

```
ANTES: Template 200MB → copiar → rezar que funcione → imposible de actualizar
AHORA: base.rvt 3MB + codigo en GitHub → build → modelo consistente siempre
```

**Ventajas:**
- Un dev nuevo clona el repo y tiene todo el estandar de la empresa
- Cambiar un tipo de muro es editar un JSON, no buscar en el template
- Git diff muestra exactamente que cambio entre versiones
- Compartir es un `git pull`, no un archivo de 200MB por WeTransfer
- Cada proyecto carga solo lo que necesita, no todo el catalogo

## Arquitectura General

```
┌─────────────────────────────────────────────────────────┐
│  NGM HUB WEB (Browser)                                  │
│                                                         │
│  OCR Tool ──> Plan Configurator ──> Build Manifest JSON │
│  (extrae       (ajusta vectores,     (contrato entre    │
│   geometria     fixtures, tipos)      web y Revit)      │
│   de planos)                                            │
└──────────────────────┬──────────────────────────────────┘
                       │ JSON file o API
                       v
┌─────────────────────────────────────────────────────────┐
│  pyRevit Build Engine (dentro de Revit)                  │
│                                                         │
│  Lee manifest ──> Crea niveles, grids                    │
│               ──> Carga familias del vault               │
│               ──> Define tipos de muro/piso desde code   │
│               ──> Traza muros por vectores               │
│               ──> Inserta fixtures en puntos             │
│               ──> Crea vistas, sheets, estilos           │
│               ──> SaveAs proyecto.rvt                    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       v
┌─────────────────────────────────────────────────────────┐
│  Export Suite                                            │
│                                                         │
│  Modelo ──> JSON cantidades ──> NGM API ──> Estimador    │
└─────────────────────────────────────────────────────────┘
```

## Que vive en codigo (este repo) vs que vive en Revit

| En GitHub (este repo) | En Revit (.rvt) |
|---|---|
| Definiciones de tipos de muro (capas, materiales, grosores) | Instancias de muros colocados |
| Catalogo de familias (.rfa en vault) | Familias ya cargadas en el proyecto |
| Configs de vistas (escala, view template, filtros) | Las vistas renderizadas |
| Layouts de sheets (que vista va donde) | Los sheets armados |
| Estilos graficos (colores, line weights, patterns) | Los overrides aplicados |
| Nomenclatura y numeracion | Los nombres asignados |
| Build manifest (vectores, puntos, fixtures) | La geometria construida |
| Material map (Revit name <-> NGM code) | Los materiales asignados |

## Market & Standards

- **Market**: US construction (American market)
- **Unit system**: US Imperial (ft, in, yd, sq ft, cu ft, lbs)
- **Revit project units**: Feet and fractional inches (Revit default US)
- **JSON values**: Decimal feet (e.g. `9.186` for 9'-2 1/4"). Revit API uses feet internally so NO conversion needed
- **Language**: ALL visible text in English - menu names, button labels, type names, view names, sheet titles, family names, parameter names, room names, annotations
- **Code comments**: English or Spanish (internal, not visible to end user)

### Unit Reference
| Imperial | Decimal ft | Use for |
|---|---|---|
| 1" | 0.0833 ft | Finish layers |
| 4" | 0.3333 ft | Block walls |
| 6" | 0.5 ft | Block/concrete walls |
| 8" | 0.6667 ft | Block/concrete walls |
| 9'-2 1/4" | 9.1875 ft | Room heights |
| 10'-0" | 10.0 ft | Standard ceiling |
| 12" (1') | 1.0 ft | Columns, slabs |

## Ecosistema NGM
- **NGM HUB WEB** (`C:\Users\germa\Desktop\NGM HUB WEB`) - Frontend web
- **NGM_API** (`C:\Users\germa\Desktop\NGM_API`) - Backend FastAPI + Supabase
- **NGM_REVIT** (este repo) - BIM as Code

## Stack
- **pyRevit** - Framework que carga scripts Python dentro de Revit
- **IronPython 2.7** - Interprete de pyRevit (NO es CPython, NO tiene pip)
- **Revit API** - Accesible via `clr` y namespaces de Autodesk (.NET)
- **.NET interop** - HTTP via `System.Net.WebClient` (no `requests`)
- **Revit 2024+** - Version minima target

## Estructura del Proyecto
```
NGM_REVIT/
  CLAUDE.md

  # ── Definiciones (la inteligencia del template, en codigo) ──
  definitions/
    wall_types.json          # Todos los tipos de muro: capas, materiales, grosores
    floor_types.json         # Tipos de losa/piso
    roof_types.json          # Tipos de techo
    view_templates.json      # Configs de vistas: escala, filtros, overrides
    sheet_layouts.json       # Layouts de sheets: titleblock, que vistas, posiciones
    graphic_styles.json      # Line weights, colors, patterns, filters
    naming_conventions.json  # Nomenclatura estandar NGM para todo
    shared_parameters.json   # Parametros compartidos NGM

  # ── Configs por tipo de proyecto ──
  templates/
    residential.json         # Que cargar para proyecto residencial
    commercial.json
    industrial.json

  # ── Vault de familias ──
  families/
    structural/              # .rfa: columnas, vigas, cimentacion
    architectural/           # .rfa: puertas, ventanas, mobiliario
    mep/                     # .rfa: instalaciones
    annotations/             # .rfa: etiquetas, titleblocks, tags
    specialty/               # .rfa: fixtures especificos por tipo de proyecto

  # ── Mapeo de materiales ──
  materials/
    material_map.json        # Revit material <-> NGM catalogo

  # ── Build manifests (generados por web o manuales) ──
  manifests/
    _schema.json             # Schema del build manifest
    examples/                # Manifests de ejemplo para testing

  # ── Archivos base de Revit (minimos) ──
  base/
    base.rvt                 # Archivo blank (~3MB), solo unidades metricas

  # ── Salida ──
  exports/                   # JSONs generados por Export Suite

  # ── Extension pyRevit ──
  NGM.extension/
    NGM.tab/
      Build.panel/                    # Construir modelo desde manifest
        BuildFromManifest.pushbutton/
          script.py                   # Lee manifest -> construye modelo completo
        LoadDefinitions.pushbutton/
          script.py                   # Aplica definitions al doc activo
      Exportar.panel/                 # Extraccion de datos
        ExportarMateriales.pushbutton/
          script.py
        ExportarCantidades.pushbutton/
          script.py
        ExportarEspacios.pushbutton/
          script.py
      Planos.panel/                   # Drawing package
        GenerarSheets.pushbutton/
          script.py
        ExportPDF.pushbutton/
          script.py
      Herramientas.panel/             # Helpers
        PurgeModel.pushbutton/
          script.py
        RenameViews.pushbutton/
          script.py
        ModelQA.pushbutton/
          script.py
      Sync.panel/                     # Comunicacion con NGM API
        SyncMaterials.pushbutton/
          script.py
        PushToEstimator.pushbutton/
          script.py
```

## Build Manifest Schema

El build manifest es el contrato central entre la web (o edicion manual) y pyRevit.
Schema completo en `manifests/_schema.json`. Estructura:

```json
{
  "meta": {
    "project_name": "Casa Robles 45",
    "project_type": "residential",
    "created_by": "ocr_tool | web_configurator | manual",
    "version": "1.0"
  },
  "levels": [
    {"name": "Foundation", "elevation_ft": -2.0},
    {"name": "Ground Floor", "elevation_ft": 0.0},
    {"name": "Second Floor", "elevation_ft": 9.1875}
  ],
  "grids": [
    {"name": "A", "start": [0, 0], "end": [0, 15]},
    {"name": "1", "start": [0, 0], "end": [20, 0]}
  ],
  "walls": [
    {
      "type": "Ext - CMU 6\" + Plaster",
      "level": "Ground Floor",
      "start": [0, 0],
      "end": [26.25, 0],
      "height_ft": 9.1875,
      "structural": false
    }
  ],
  "fixtures": [
    {
      "family": "NGM_Door_Single",
      "type": "3'-0\" x 7'-0\"",
      "point": [6.56, 0, 0],
      "level": "Ground Floor",
      "host_wall_index": 0,
      "rotation_deg": 0
    }
  ],
  "floors": [
    {
      "type": "Slab - Concrete 5\"",
      "level": "Ground Floor",
      "boundary": [[0,0], [26.25,0], [26.25,32.81], [0,32.81]]
    }
  ],
  "rooms": [
    {
      "name": "Living Room",
      "number": "01",
      "point": [5, 4],
      "level": "Ground Floor",
      "floor_finish": "Ceramic Tile",
      "wall_finish": "Plaster + Paint",
      "ceiling_finish": "Plaster + Paint"
    }
  ],
  "columns": [
    {
      "family": "NGM_Column_Rectangular",
      "type": "12x12 in",
      "point": [0, 0],
      "base_level": "Foundation",
      "top_level": "Roof"
    }
  ],
  "beams": [
    {
      "family": "NGM_Beam_Rectangular",
      "type": "8x16 in",
      "start": [0, 0, 9.1875],
      "end": [26.25, 0, 9.1875],
      "level": "Second Floor"
    }
  ],
  "views": "use_template_defaults",
  "sheets": "use_template_defaults"
}
```

`views` y `sheets` pueden ser `"use_template_defaults"` (usa lo definido en `templates/residential.json`) o un array explicito de vistas/sheets custom.

## Revit API - Capacidades Clave

### Lo que SI se puede via codigo
- **Crear tipos de muro**: duplicar tipo base, modificar CompoundStructure (capas, materiales, grosores)
- **Trazar muros por vector**: `Wall.Create(doc, Line, wallTypeId, levelId, height, offset, flip, structural)`
- **Insertar familia en punto**: `doc.Create.NewFamilyInstance(XYZ, FamilySymbol, Level, StructuralType)`
- **Familia line-based por vector**: `doc.Create.NewFamilyInstance(Line, FamilySymbol, Level, StructuralType)` - se adapta a la longitud
- **Cargar familia desde disco**: `doc.LoadFamily(path)` - lee .rfa del vault
- **Crear piso por boundary**: `doc.Create.NewFloor(CurveArray, FloorType, Level, structural)`
- **Crear vistas**: `ViewPlan.Create()`, `ViewSection.CreateSection()`, `View3D.CreateIsometric()`
- **Crear sheets**: `ViewSheet.Create(doc, titleblockId)` + `Viewport.Create()` para colocar vistas
- **Graphic overrides**: `OverrideGraphicSettings` - colores, patrones, line weights por elemento o filtro
- **View templates**: duplicar vista, configurar como template, aplicar a otras vistas
- **Niveles y grids**: `Level.Create()`, `Grid.Create()`
- **Shared parameters**: `doc.ParameterBindings`, `DefinitionFile`
- **HTTP a NGM API**: `System.Net.WebClient` para sync bidireccional

### Lo que NO se puede
- Crear archivos .rvt o .rfa desde cero (necesita un base.rvt minimo abierto)
- OCR/Vision (se hace externo, resultado llega como JSON)
- Multi-threading real (IronPython limitacion)
- Operaciones de 10k+ elementos pueden congelar UI (usar progress bar + TransactionGroup)

## Reglas de Codigo

### Scripts pyRevit (IronPython 2.7)
- Cada script en su carpeta `.pushbutton/` con nombre `script.py`
- Imports de Revit API al inicio:
  ```python
  import clr
  clr.AddReference('RevitAPI')
  clr.AddReference('RevitAPIUI')
  from Autodesk.Revit.DB import *
  from pyrevit import revit, DB, script
  ```
- `doc = revit.doc` para documento activo
- `output = script.get_output()` para consola pyRevit
- Transacciones: `with revit.Transaction('nombre'):`

### Restricciones IronPython 2.7
- NO f-strings -> usar `.format()` o `%`
- NO pathlib -> usar `os.path`
- NO `json.dumps(ensure_ascii=False)` con caracteres especiales
- NO `with open()` sin encoding -> siempre `codecs.open(path, 'w', 'utf-8')`
- NO tiene: dataclasses, type hints, async/await, pip, numpy, PIL
- SI tiene: os, sys, json, codecs, datetime, collections, math, re
- HTTP: `System.Net.WebClient` via clr

### JSON Export Format
```json
{
  "export_info": {
    "revit_file": "project_name.rvt",
    "export_date": "2026-02-11T10:30:00",
    "export_type": "materials|quantities|spaces",
    "units": "imperial"
  },
  "items": [...]
}
```

### Revit -> NGM Estimator Mapping
- Revit `Category` -> NGM concept group
- Revit `Family + Type` -> NGM concept name
- Revit `Material` -> NGM material (via material_map.json)
- Revit area/volume/count -> NGM quantity (sq ft, cu yd, lf, pcs, lbs)

## Relevant Revit Categories
- `OST_Walls` - Walls
- `OST_Floors` - Floors / Slabs
- `OST_Roofs` - Roofs
- `OST_StructuralColumns` - Columns
- `OST_StructuralFraming` - Beams / Framing
- `OST_StructuralFoundation` - Foundations
- `OST_Doors` - Doors
- `OST_Windows` - Windows
- `OST_Rooms` - Rooms
- `OST_Stairs` - Stairs
- `OST_Rebar` - Rebar

## NGM API Endpoints
- `GET /accounts` - Accounts/line items
- `GET /estimator/concepts/{project_id}` - Estimator concepts
- `POST /estimator/concepts` - Create concept
- Prod: `https://ngm-fastapi.onrender.com`
- Staging: `https://ngm-api-staging.onrender.com`

## Como Instalar
1. Instalar pyRevit: https://github.com/pyrevitlabs/pyRevit/releases
2. Clonar este repo: `git clone https://github.com/GerardRojas/NGM_REVIT.git`
3. En Revit -> pyRevit -> Settings -> Custom Extension Directories
4. Agregar ruta al repo clonado
5. Reload pyRevit -> aparece tab "NGM"
6. Para actualizar: `git pull` y reload pyRevit

## Roadmap
1. **Fase 1 - Export Suite**: Extraccion de datos del modelo (HECHO)
2. **Fase 2 - Definitions**: Wall types, floor types, view templates, styles en JSON
3. **Fase 3 - Build Engine**: Script que lee manifest y construye modelo
4. **Fase 4 - Vault**: Libreria de familias estandarizadas
5. **Fase 5 - Drawing Package**: Sheets, views, PDF export automatizado
6. **Fase 6 - Web Integration**: OCR tool + Plan Configurator en NGM HUB
7. **Fase 7 - AI Agents**: Conexion con Andrew/Daneel para validacion BIM-vs-gastos
