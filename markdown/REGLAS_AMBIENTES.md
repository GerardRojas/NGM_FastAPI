# Reglas de Ambientes y Ramas

Este proyecto manejará 4 niveles de trabajo:

- `main` (producción)
- `stage` (preproducción)
- `dev` (integración de desarrollo)
- `dev-[feature-name-dev]` (desarrollo por feature)

Nota: en este repositorio existe la rama `staging`. Operativamente, `stage` y `staging` representan el mismo ambiente.

## 1) `main` (Producción)

- Propósito: versión estable en producción.
- Estabilidad: máxima; solo código validado y aprobado.
- Origen de cambios permitido: merge desde `stage`/`staging`.
- Commits directos: prohibidos.
- Requisitos mínimos para merge:
- PR aprobada.
- CI en verde (tests, lint, build).
- Validación funcional previa en `stage`.
- Deploy: automático a producción al merge.
- Versionado recomendado: tags `vX.Y.Z` en cada release.
- Hotfix: se permite rama `hotfix/*` desde `main`, pero debe volver a integrarse también en `dev`.

## 2) `stage` (Preproducción)

- Propósito: validar release candidata antes de producción.
- Estabilidad: alta; reflejo de lo que está por salir.
- Origen de cambios permitido: merge desde `dev`.
- Commits directos: no recomendados (idealmente bloqueados).
- Requisitos mínimos para merge:
- PR aprobada.
- CI en verde.
- Pruebas de integración completas.
- Pruebas manuales de regresión (mínimo smoke test).
- Deploy: automático a entorno staging al merge.
- Regla de promoción: solo si `stage` está estable se promueve a `main`.

## 3) `dev` (Integración de Desarrollo)

- Propósito: rama base de integración de trabajo diario.
- Estabilidad: media; puede contener cambios en validación.
- Origen de cambios permitido: merge desde `dev-[feature-name-dev]`.
- Commits directos: permitidos solo para ajustes menores urgentes (idealmente mediante PR).
- Requisitos mínimos para merge:
- PR aprobada por al menos 1 revisor.
- Tests de unidad relevantes en verde.
- Sin conflictos ni degradación funcional conocida.
- Deploy: opcional a ambiente de desarrollo compartido.
- Regla de higiene: mantener `dev` actualizada con cambios de `main` (si hubo hotfixes).

## 4) `dev-[feature-name-dev]` (Feature Branches)

- Propósito: aislar el desarrollo de una funcionalidad o corrección.
- Base de creación: siempre desde `dev`.
- Nomenclatura obligatoria:
- `dev-[feature-name-dev]`
- Ejemplos:
- `dev-login-oauth-dev`
- `dev-fix-cors-dev`
- `dev-bulk-import-dev`
- Alcance: una sola feature/fix por rama.
- Vida útil: corta; se elimina después de merge.
- Merge permitido: solo hacia `dev`.
- Commits recomendados: pequeños, trazables y con mensaje claro.

## 5) Flujo de Trabajo Oficial

1. Crear rama desde `dev`: `dev-[feature-name-dev]`.
2. Desarrollar y abrir PR hacia `dev`.
3. Validar en `dev` (tests + revisión).
4. Promover `dev` hacia `stage`/`staging`.
5. Validar release candidata en `stage`.
6. Promover `stage` hacia `main`.
7. Taggear release en `main`.

## 6) Reglas de Protección Recomendadas

- `main`: protected branch, sin push directo, mínimo 1-2 approvals.
- `stage`/`staging`: protected branch, sin push directo, CI obligatorio.
- `dev`: al menos 1 approval y CI para PRs.
- Eliminar branches feature automáticamente al merge.

## 7) Estrategia de Hotfix

1. Crear `hotfix/[descripcion-corta]` desde `main`.
2. Validar y mergear a `main` con PR.
3. Propagar el mismo cambio a `dev` (y `stage` si aplica) para evitar divergencia.

## 8) Criterios de Calidad Mínimos por PR

- Sin secretos hardcodeados.
- Sin romper contratos de API existentes sin versionado.
- Tests relevantes agregados/actualizados.
- Notas de cambio claras (qué cambia, riesgo, rollback).

