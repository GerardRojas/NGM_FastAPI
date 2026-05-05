# Documentación Técnica de Endpoints

Este documento fue generado desde el código fuente activo de FastAPI en `api/main.py` y routers incluidos.
Cobertura: endpoints públicos y endpoints ocultos en OpenAPI definidos directamente en `main.py`.

Total endpoints detectados: **428**

## Convenciones
- **Módulo**: tag del router (si existe) o nombre del archivo del router.
- **Tipo de servicio**: clasificación funcional heurística (consulta, creación, integración, etc.).
- **Necesita**: entradas esperadas (path/query/body/form/file/dependencies).
- **Guarda/Modifica**: persistencia inferida desde operaciones directas en el handler (si delega a servicios puede quedar como no evidente).
- **Regresa**: forma de respuesta inferida por `return`, `response_model` y códigos HTTP detectables.

## Resumen Global
| Módulo | Tipo de servicio | Método | Endpoint |
|---|---|---|---|
| accounts | Consulta | GET | `/accounts` |
| accounts | Creación/Comando | POST | `/accounts` |
| accounts | Eliminación | DELETE | `/accounts/{account_id}` |
| accounts | Consulta | GET | `/accounts/{account_id}` |
| accounts | Actualización | PATCH | `/accounts/{account_id}` |
| ADU Calculator | Proceso/Automatización | POST | `/adu-calculator/analyze-screenshot` |
| ADU Calculator | Proceso/Automatización | POST | `/adu-calculator/calculate` |
| ADU Calculator | Consulta | GET | `/adu-calculator/pricing-config` |
| ADU Calculator | Actualización | PUT | `/adu-calculator/pricing-config` |
| Analytics | Observabilidad | GET | `/analytics/budget-health` |
| Analytics | Consulta | GET | `/analytics/executive/kpis` |
| Analytics | Consulta | GET | `/analytics/expense-intelligence` |
| Analytics | Observabilidad | GET | `/analytics/health/all` |
| Analytics | Consulta | GET | `/analytics/project-scorecard` |
| Analytics | Consulta | GET | `/analytics/projects/{project_id}/budget-vs-actual` |
| Analytics | Consulta | GET | `/analytics/projects/{project_id}/cost-trends` |
| Analytics | Consulta | GET | `/analytics/projects/{project_id}/expense-timeline` |
| Analytics | Observabilidad | GET | `/analytics/projects/{project_id}/health` |
| Analytics | Consulta | GET | `/analytics/vendors/summary` |
| Analytics | Consulta | GET | `/analytics/vendors/{vendor_id}/scorecard` |
| andrew | Proceso/Automatización | POST | `/andrew/follow-up-check` |
| andrew | Consulta | GET | `/andrew/mismatch-config` |
| andrew | Actualización | PUT | `/andrew/mismatch-config` |
| andrew | Consulta | GET | `/andrew/pending-receipts-status` |
| andrew | Acción/Comando | POST | `/andrew/reconcile-bill` |
| art | Acción/Comando | POST | `/art/clear-thread` |
| art | Acción/Comando | POST | `/art/delegate-task` |
| art | Consulta | GET | `/art/failed-commands` |
| art | Consulta | GET | `/art/failed-commands/stats` |
| art | Observabilidad | GET | `/art/health` |
| art | Proceso/Automatización | POST | `/art/interpret-filter` |
| art | Proceso/Automatización | POST | `/art/interpret-intent` |
| art | Acción/Comando | POST | `/art/message` |
| art | Consulta | GET | `/art/permissions` |
| art | Actualización | PATCH | `/art/permissions` |
| art | Acción/Comando | POST | `/art/permissions/reset` |
| art | Acción/Comando | POST | `/art/personality` |
| art | Consulta | GET | `/art/personality/{space_id}` |
| art | Proceso/Automatización | GET | `/art/search-accounts` |
| art | Acción/Comando | POST | `/art/slash` |
| art | Acción/Comando | POST | `/art/web-chat` |
| art | Integración externa | POST | `/art/webhook` |
| auth | Autenticación | POST | `/auth/create_user` |
| auth | Autenticación | POST | `/auth/login` |
| auth | Autenticación | GET | `/auth/me` |
| bills | Consulta | GET | `/bills` |
| bills | Creación/Comando | POST | `/bills` |
| bills | Eliminación | DELETE | `/bills/{bill_id}` |
| bills | Consulta | GET | `/bills/{bill_id}` |
| bills | Actualización | PATCH | `/bills/{bill_id}` |
| bills | Acción/Comando | POST | `/bills/{bill_id}/close` |
| bills | Consulta | GET | `/bills/{bill_id}/expenses` |
| bills | Acción/Comando | POST | `/bills/{bill_id}/mark-split` |
| bills | Acción/Comando | POST | `/bills/{bill_id}/reopen` |
| budget-alerts | Actualización | PUT | `/budget-alerts/acknowledge/{alert_id}` |
| budget-alerts | Consulta | GET | `/budget-alerts/acknowledge/{alert_id}/history` |
| budget-alerts | Acción/Comando | POST | `/budget-alerts/acknowledge/{alert_id}/reopen` |
| budget-alerts | Proceso/Automatización | POST | `/budget-alerts/check` |
| budget-alerts | Consulta | GET | `/budget-alerts/history` |
| budget-alerts | Actualización | PUT | `/budget-alerts/history/{alert_id}/read` |
| budget-alerts | Consulta | GET | `/budget-alerts/notifications` |
| budget-alerts | Actualización | PUT | `/budget-alerts/notifications/read-all` |
| budget-alerts | Actualización | PUT | `/budget-alerts/notifications/{notification_id}/read` |
| budget-alerts | Consulta | GET | `/budget-alerts/pending` |
| budget-alerts | Consulta | GET | `/budget-alerts/pending/count` |
| budget-alerts | Consulta | GET | `/budget-alerts/permissions` |
| budget-alerts | Consulta | GET | `/budget-alerts/recipients` |
| budget-alerts | Acción/Comando | POST | `/budget-alerts/recipients` |
| budget-alerts | Eliminación | DELETE | `/budget-alerts/recipients/{recipient_id}` |
| budget-alerts | Actualización | PUT | `/budget-alerts/recipients/{recipient_id}` |
| budget-alerts | Consulta | GET | `/budget-alerts/settings` |
| budget-alerts | Actualización | PUT | `/budget-alerts/settings` |
| budget-alerts | Consulta | GET | `/budget-alerts/summary` |
| budgets | Consulta | GET | `/budgets` |
| budgets | Eliminación | DELETE | `/budgets/batch/{batch_id}` |
| budgets | Proceso/Automatización | POST | `/budgets/import` |
| categorization-ml | Acción/Comando | POST | `/categorization-ml/predict` |
| categorization-ml | Acción/Comando | POST | `/categorization-ml/predict-batch` |
| categorization-ml | Consulta | GET | `/categorization-ml/status` |
| categorization-ml | Acción/Comando | POST | `/categorization-ml/train` |
| Comments | Consulta | GET | `/comments/` |
| Comments | Creación/Comando | POST | `/comments/` |
| Comments | Consulta | GET | `/comments/counts` |
| Comments | Eliminación | DELETE | `/comments/{comment_id}` |
| Comments | Actualización | PATCH | `/comments/{comment_id}` |
| Comments | Actualización | PATCH | `/comments/{comment_id}/resolve` |
| companies | Consulta | GET | `/companies` |
| companies | Creación/Comando | POST | `/companies` |
| companies | Eliminación | DELETE | `/companies/{company_id}` |
| companies | Consulta | GET | `/companies/{company_id}` |
| companies | Actualización | PATCH | `/companies/{company_id}` |
| concepts | Consulta | GET | `/concepts` |
| concepts | Creación/Comando | POST | `/concepts` |
| concepts | Eliminación | DELETE | `/concepts/{concept_id}` |
| concepts | Consulta | GET | `/concepts/{concept_id}` |
| concepts | Actualización | PATCH | `/concepts/{concept_id}` |
| concepts | Consulta | GET | `/concepts/{concept_id}/materials` |
| concepts | Acción/Comando | POST | `/concepts/{concept_id}/materials` |
| concepts | Proceso/Automatización | PUT | `/concepts/{concept_id}/materials/sync` |
| concepts | Eliminación | DELETE | `/concepts/{concept_id}/materials/{material_entry_id}` |
| concepts | Actualización | PATCH | `/concepts/{concept_id}/materials/{material_entry_id}` |
| core | Consulta | GET | `/` |
| core | Observabilidad | GET | `/debug/memory` |
| core | Consulta | GET | `/departments` |
| core | Observabilidad | GET | `/health` |
| daneel | Consulta | GET | `/daneel/auto-auth/config` |
| daneel | Actualización | PUT | `/daneel/auto-auth/config` |
| daneel | Acción/Comando | POST | `/daneel/auto-auth/digest` |
| daneel | Proceso/Automatización | POST | `/daneel/auto-auth/follow-up-check` |
| daneel | Consulta | GET | `/daneel/auto-auth/pending-info` |
| daneel | Consulta | GET | `/daneel/auto-auth/pending-info-status` |
| daneel | Consulta | GET | `/daneel/auto-auth/project-summary` |
| daneel | Eliminación | DELETE | `/daneel/auto-auth/reports` |
| daneel | Consulta | GET | `/daneel/auto-auth/reports` |
| daneel | Consulta | GET | `/daneel/auto-auth/reports/{report_id}` |
| daneel | Acción/Comando | POST | `/daneel/auto-auth/reprocess` |
| daneel | Proceso/Automatización | POST | `/daneel/auto-auth/run` |
| daneel | Proceso/Automatización | POST | `/daneel/auto-auth/run-backlog` |
| daneel | Consulta | GET | `/daneel/auto-auth/status` |
| debug | Observabilidad | GET | `/debug/supabase-ping` |
| estimator | Consulta | GET | `/estimator/base-structure` |
| estimator | Consulta | GET | `/estimator/buckets/init` |
| estimator | Consulta | GET | `/estimator/buckets/status` |
| estimator | Consulta | GET | `/estimator/estimates` |
| estimator | Acción/Comando | POST | `/estimator/estimates` |
| estimator | Eliminación | DELETE | `/estimator/estimates/{estimate_id}` |
| estimator | Consulta | GET | `/estimator/estimates/{estimate_id}` |
| estimator | Acción/Comando | POST | `/estimator/save` |
| estimator | Consulta | GET | `/estimator/templates` |
| estimator | Acción/Comando | POST | `/estimator/templates` |
| estimator | Eliminación | DELETE | `/estimator/templates/{template_id}` |
| estimator | Consulta | GET | `/estimator/templates/{template_id}` |
| estimator | Consulta | GET | `/estimator/templates/{template_id}/meta` |
| Expenses | Consulta | GET | `/expenses` |
| Expenses | Creación/Comando | POST | `/expenses` |
| Expenses | Consulta | GET | `/expenses/all` |
| Expenses | Acción/Comando | POST | `/expenses/auto-categorize` |
| Expenses | Actualización | PATCH | `/expenses/batch` |
| Expenses | Acción/Comando | POST | `/expenses/batch` |
| Expenses | Acción/Comando | POST | `/expenses/categorization-correction` |
| Expenses | Proceso/Automatización | POST | `/expenses/check-receipt-type` |
| Expenses | Consulta | GET | `/expenses/dismissed-duplicates` |
| Expenses | Acción/Comando | POST | `/expenses/dismissed-duplicates` |
| Expenses | Eliminación | DELETE | `/expenses/dismissed-duplicates/{dismissal_id}` |
| Expenses | Consulta | GET | `/expenses/duplicate-scan` |
| Expenses | Proceso/Automatización | GET | `/expenses/export` |
| Expenses | Consulta | GET | `/expenses/meta` |
| Expenses | Consulta | GET | `/expenses/metrics/categorization-errors` |
| Expenses | Acción/Comando | POST | `/expenses/parse-receipt` |
| Expenses | Consulta | GET | `/expenses/pending-authorization/count` |
| Expenses | Consulta | GET | `/expenses/pending-authorization/summary` |
| Expenses | Consulta | GET | `/expenses/summary/by-project` |
| Expenses | Consulta | GET | `/expenses/summary/by-txn-type` |
| Expenses | Eliminación | DELETE | `/expenses/{expense_id}` |
| Expenses | Consulta | GET | `/expenses/{expense_id}` |
| Expenses | Actualización | PATCH | `/expenses/{expense_id}` |
| Expenses | Actualización | PUT | `/expenses/{expense_id}` |
| Expenses | Consulta | GET | `/expenses/{expense_id}/audit-trail` |
| Expenses | Acción/Comando | POST | `/expenses/{expense_id}/soft-delete` |
| Expenses | Actualización | PATCH | `/expenses/{expense_id}/status` |
| Expenses | Consulta | GET | `/expenses/{expense_id}/status-history` |
| hari | Consulta | GET | `/hari/config` |
| hari | Acción/Comando | POST | `/hari/config` |
| hari | Proceso/Automatización | POST | `/hari/follow-up/run` |
| hari | Consulta | GET | `/hari/stats` |
| hari | Consulta | GET | `/hari/tasks` |
| hari | Acción/Comando | POST | `/hari/tasks/{task_id}/action` |
| invoice-links | Acción/Comando | POST | `/invoice-links/create` |
| invoice-links | Consulta | GET | `/invoice-links/list` |
| invoice-links | Acción/Comando | POST | `/invoice-links/mark-paid` |
| invoice-links | Consulta | GET | `/invoice-links/verify` |
| material-categories | Consulta | GET | `/material-categories` |
| material-categories | Creación/Comando | POST | `/material-categories` |
| material-categories | Consulta | GET | `/material-categories/tree` |
| material-categories | Eliminación | DELETE | `/material-categories/{category_id}` |
| material-categories | Consulta | GET | `/material-categories/{category_id}` |
| material-categories | Actualización | PATCH | `/material-categories/{category_id}` |
| material-classes | Consulta | GET | `/material-classes` |
| material-classes | Creación/Comando | POST | `/material-classes` |
| material-classes | Eliminación | DELETE | `/material-classes/{class_id}` |
| material-classes | Consulta | GET | `/material-classes/{class_id}` |
| material-classes | Actualización | PATCH | `/material-classes/{class_id}` |
| materials | Consulta | GET | `/materials` |
| materials | Creación/Comando | POST | `/materials` |
| materials | Acción/Comando | POST | `/materials/bulk` |
| materials | Eliminación | DELETE | `/materials/{material_id}` |
| materials | Consulta | GET | `/materials/{material_id}` |
| materials | Actualización | PATCH | `/materials/{material_id}` |
| messages | Consulta | GET | `/messages` |
| messages | Creación/Comando | POST | `/messages` |
| messages | Acción/Comando | POST | `/messages/channel/clear` |
| messages | Consulta | GET | `/messages/channels` |
| messages | Acción/Comando | POST | `/messages/channels` |
| messages | Acción/Comando | POST | `/messages/mark-read` |
| messages | Consulta | GET | `/messages/mentions` |
| messages | Actualización | PATCH | `/messages/mentions/{message_id}/read` |
| messages | Proceso/Automatización | GET | `/messages/search` |
| messages | Consulta | GET | `/messages/unread-counts` |
| messages | Actualización | PATCH | `/messages/{message_id}/delete` |
| messages | Acción/Comando | POST | `/messages/{message_id}/reactions` |
| messages | Consulta | GET | `/messages/{message_id}/thread` |
| messages | Acción/Comando | POST | `/messages/{message_id}/thread` |
| NGM Board Items | Consulta | GET | `/board-items/` |
| NGM Board Items | Creación/Comando | POST | `/board-items/` |
| NGM Board Items | Consulta | GET | `/board-items/all` |
| NGM Board Items | Acción/Comando | POST | `/board-items/bulk-move` |
| NGM Board Items | Acción/Comando | POST | `/board-items/migrate-from-legacy` |
| NGM Board Items | Eliminación | DELETE | `/board-items/{item_id}` |
| NGM Board Items | Consulta | GET | `/board-items/{item_id}` |
| NGM Board Items | Actualización | PATCH | `/board-items/{item_id}` |
| NGM Board Items | Consulta | GET | `/board-items/{item_id}/history` |
| NGM Board Items | Consulta | GET | `/board-items/{table_id}/data` |
| NGM Board Items | Actualización | PUT | `/board-items/{table_id}/data` |
| NGM Board State | Consulta | GET | `/process-manager/history/{state_key}` |
| NGM Board State | Creación/Comando | POST | `/process-manager/history/{state_key}/restore/{history_id}` |
| NGM Board State | Consulta | GET | `/process-manager/state` |
| NGM Board State | Consulta | GET | `/process-manager/state/{state_key}` |
| NGM Board State | Actualización | PUT | `/process-manager/state/{state_key}` |
| notifications | Consulta | GET | `/notifications/status` |
| notifications | Acción/Comando | POST | `/notifications/test` |
| notifications | Eliminación | DELETE | `/notifications/token` |
| notifications | Acción/Comando | POST | `/notifications/token` |
| ocr-metrics | Consulta | GET | `/ocr-metrics/recent` |
| ocr-metrics | Consulta | GET | `/ocr-metrics/summary` |
| payment_methods | Consulta | GET | `/payment-methods` |
| payment_methods | Creación/Comando | POST | `/payment-methods` |
| payment_methods | Eliminación | DELETE | `/payment-methods/{payment_method_id}` |
| payment_methods | Consulta | GET | `/payment-methods/{payment_method_id}` |
| payment_methods | Actualización | PATCH | `/payment-methods/{payment_method_id}` |
| Pending Receipts | Consulta | GET | `/pending-receipts/agent-config` |
| Pending Receipts | Actualización | PATCH | `/pending-receipts/agent-config` |
| Pending Receipts | Eliminación | DELETE | `/pending-receipts/agent-metrics` |
| Pending Receipts | Consulta | GET | `/pending-receipts/agent-stats` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/agents/{agent_id}/reset-stats` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/bill-categories/confirm` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/bill-review/edit-categories` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/bill-review/send-to-review` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/bill-review/void-items` |
| Pending Receipts | Proceso/Automatización | POST | `/pending-receipts/check-stale` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/process-from-vault` |
| Pending Receipts | Consulta | GET | `/pending-receipts/project/{project_id}` |
| Pending Receipts | Consulta | GET | `/pending-receipts/unprocessed/count` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/upload` |
| Pending Receipts | Consulta | GET | `/pending-receipts/vault-batch-status` |
| Pending Receipts | Eliminación | DELETE | `/pending-receipts/{receipt_id}` |
| Pending Receipts | Consulta | GET | `/pending-receipts/{receipt_id}` |
| Pending Receipts | Actualización | PATCH | `/pending-receipts/{receipt_id}` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/agent-process` |
| Pending Receipts | Proceso/Automatización | POST | `/pending-receipts/{receipt_id}/check-action` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/create-expense` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/duplicate-action` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/link` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/process` |
| Pending Receipts | Acción/Comando | POST | `/pending-receipts/{receipt_id}/receipt-action` |
| percentage-items | Consulta | GET | `/percentage-items` |
| percentage-items | Creación/Comando | POST | `/percentage-items` |
| percentage-items | Consulta | GET | `/percentage-items/standard` |
| percentage-items | Eliminación | DELETE | `/percentage-items/{item_id}` |
| percentage-items | Consulta | GET | `/percentage-items/{item_id}` |
| percentage-items | Actualización | PATCH | `/percentage-items/{item_id}` |
| permissions | Acción/Comando | POST | `/permissions/batch-update` |
| permissions | Proceso/Automatización | GET | `/permissions/check` |
| permissions | Consulta | GET | `/permissions/modules` |
| permissions | Consulta | GET | `/permissions/role/{rol_id}` |
| permissions | Consulta | GET | `/permissions/roles` |
| permissions | Consulta | GET | `/permissions/user/{user_id}` |
| pipeline | Consulta | GET | `/pipeline/attachments/bucket-info` |
| pipeline | Consulta | GET | `/pipeline/attachments/init` |
| pipeline | Consulta | GET | `/pipeline/automations/overrides` |
| pipeline | Acción/Comando | POST | `/pipeline/automations/overrides` |
| pipeline | Eliminación | DELETE | `/pipeline/automations/overrides/{override_id}` |
| pipeline | Proceso/Automatización | POST | `/pipeline/automations/run` |
| pipeline | Consulta | GET | `/pipeline/automations/settings` |
| pipeline | Actualización | PUT | `/pipeline/automations/settings/{automation_type}` |
| pipeline | Consulta | GET | `/pipeline/automations/status` |
| pipeline | Consulta | GET | `/pipeline/automations/tasks` |
| pipeline | Consulta | GET | `/pipeline/companies` |
| pipeline | Consulta | GET | `/pipeline/dependencies` |
| pipeline | Consulta | GET | `/pipeline/grouped` |
| pipeline | Consulta | GET | `/pipeline/my-work/team-overview` |
| pipeline | Consulta | GET | `/pipeline/my-work/{user_id}` |
| pipeline | Consulta | GET | `/pipeline/projects` |
| pipeline | Consulta | GET | `/pipeline/task-departments` |
| pipeline | Consulta | GET | `/pipeline/task-priorities` |
| pipeline | Consulta | GET | `/pipeline/task-types` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks` |
| pipeline | Consulta | GET | `/pipeline/tasks/my-tasks/{user_id}` |
| pipeline | Consulta | GET | `/pipeline/tasks/pending-reviews/{user_id}` |
| pipeline | Eliminación | DELETE | `/pipeline/tasks/{task_id}` |
| pipeline | Actualización | PATCH | `/pipeline/tasks/{task_id}` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks/{task_id}/approve` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks/{task_id}/convert-to-coordination` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks/{task_id}/reject` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks/{task_id}/send-to-review` |
| pipeline | Acción/Comando | POST | `/pipeline/tasks/{task_id}/start` |
| pipeline | Consulta | GET | `/pipeline/tasks/{task_id}/workflow-history` |
| pipeline | Consulta | GET | `/pipeline/users` |
| pipeline | Actualización | PUT | `/pipeline/workload/capacity/{user_id}` |
| pipeline | Consulta | GET | `/pipeline/workload/next-available/{user_id}` |
| pipeline | Proceso/Automatización | POST | `/pipeline/workload/recalculate/{user_id}` |
| pipeline | Acción/Comando | POST | `/pipeline/workload/schedule-task` |
| pipeline | Consulta | GET | `/pipeline/workload/team` |
| pipeline | Consulta | GET | `/pipeline/workload/user/{user_id}` |
| Process Manager | Consulta | GET | `/processes` |
| Process Manager | Consulta | GET | `/processes/categories/list` |
| Process Manager | Acción/Comando | POST | `/processes/drafts` |
| Process Manager | Eliminación | DELETE | `/processes/drafts/{process_id}` |
| Process Manager | Actualización | PATCH | `/processes/drafts/{process_id}` |
| Process Manager | Consulta | GET | `/processes/step-types/list` |
| Process Manager | Consulta | GET | `/processes/triggers/list` |
| Process Manager | Consulta | GET | `/processes/{process_id}` |
| Projects | Consulta | GET | `/projects` |
| Projects | Creación/Comando | POST | `/projects/` |
| Projects | Consulta | GET | `/projects/meta` |
| Projects | Eliminación | DELETE | `/projects/{project_id}` |
| Projects | Consulta | GET | `/projects/{project_id}` |
| Projects | Actualización | PATCH | `/projects/{project_id}` |
| QBO Integration | Integración externa | GET | `/qbo/accounts` |
| QBO Integration | Integración externa | POST | `/qbo/accounts/sync/{realm_id}` |
| QBO Integration | Autenticación | GET | `/qbo/auth` |
| QBO Integration | Integración externa | POST | `/qbo/bills/extract-doc-numbers/{realm_id}` |
| QBO Integration | Integración externa | GET | `/qbo/budgets/mapping` |
| QBO Integration | Integración externa | POST | `/qbo/budgets/mapping` |
| QBO Integration | Integración externa | POST | `/qbo/budgets/mapping/auto-match` |
| QBO Integration | Integración externa | DELETE | `/qbo/budgets/mapping/{qbo_budget_id}` |
| QBO Integration | Integración externa | PUT | `/qbo/budgets/mapping/{qbo_budget_id}` |
| QBO Integration | Integración externa | GET | `/qbo/budgets/preview/{realm_id}` |
| QBO Integration | Integración externa | POST | `/qbo/budgets/sync-mapped/{realm_id}` |
| QBO Integration | Integración externa | POST | `/qbo/budgets/sync/{realm_id}` |
| QBO Integration | Integración externa | GET | `/qbo/callback` |
| QBO Integration | Integración externa | POST | `/qbo/disconnect/{realm_id}` |
| QBO Integration | Integración externa | GET | `/qbo/expenses` |
| QBO Integration | Integración externa | DELETE | `/qbo/expenses/clear` |
| QBO Integration | Integración externa | POST | `/qbo/expenses/import` |
| QBO Integration | Integración externa | GET | `/qbo/expenses/stats` |
| QBO Integration | Integración externa | GET | `/qbo/mapping` |
| QBO Integration | Integración externa | POST | `/qbo/mapping` |
| QBO Integration | Integración externa | POST | `/qbo/mapping/auto-match` |
| QBO Integration | Integración externa | DELETE | `/qbo/mapping/{qbo_customer_id}` |
| QBO Integration | Integración externa | PUT | `/qbo/mapping/{qbo_customer_id}` |
| QBO Integration | Integración externa | GET | `/qbo/pnl` |
| QBO Integration | Integración externa | GET | `/qbo/revenue` |
| QBO Integration | Integración externa | POST | `/qbo/revenue/sync/{realm_id}` |
| QBO Integration | Integración externa | GET | `/qbo/status` |
| QBO Integration | Integración externa | POST | `/qbo/sync/{realm_id}` |
| Reconciliations | Consulta | GET | `/expenses/reconciliations` |
| Reconciliations | Acción/Comando | POST | `/expenses/reconciliations` |
| Reconciliations | Eliminación | DELETE | `/expenses/reconciliations/by-manual/{manual_expense_id}` |
| Reconciliations | Integración externa | DELETE | `/expenses/reconciliations/by-qbo/{qbo_expense_id}` |
| Reconciliations | Consulta | GET | `/expenses/reconciliations/summary` |
| Reconciliations | Eliminación | DELETE | `/expenses/reconciliations/{reconciliation_id}` |
| Reporting | Acción/Comando | POST | `/reports/pnl` |
| revit | Consulta | GET | `/revit/definitions` |
| revit | Consulta | GET | `/revit/definitions/{def_type}` |
| revit | Consulta | GET | `/revit/manifest-schema` |
| revit | Consulta | GET | `/revit/manifests` |
| revit | Acción/Comando | POST | `/revit/manifests` |
| revit | Eliminación | DELETE | `/revit/manifests/{manifest_id}` |
| revit | Consulta | GET | `/revit/manifests/{manifest_id}` |
| revit | Actualización | PATCH | `/revit/manifests/{manifest_id}` |
| revit | Consulta | GET | `/revit/materials-map` |
| revit | Consulta | GET | `/revit/registry` |
| revit | Consulta | GET | `/revit/templates` |
| revit | Consulta | GET | `/revit/templates/{project_type}` |
| Revit OCR | Proceso/Automatización | POST | `/revit/ocr/analyze` |
| schema | Consulta | GET | `/schema` |
| stripe | Integración externa | POST | `/stripe/create-checkout-session` |
| stripe | Integración externa | POST | `/stripe/create-plan-checkout` |
| stripe | Integración externa | GET | `/stripe/session-status/{session_id}` |
| Takeoff | Consulta | GET | `/takeoff/measurements` |
| Takeoff | Acción/Comando | POST | `/takeoff/measurements` |
| Takeoff | Eliminación | DELETE | `/takeoff/measurements/{meas_id}` |
| Takeoff | Actualización | PATCH | `/takeoff/measurements/{meas_id}` |
| Takeoff | Consulta | GET | `/takeoff/plans` |
| Takeoff | Acción/Comando | POST | `/takeoff/plans` |
| Takeoff | Eliminación | DELETE | `/takeoff/plans/{plan_id}` |
| Takeoff | Consulta | GET | `/takeoff/plans/{plan_id}` |
| Takeoff | Actualización | PATCH | `/takeoff/plans/{plan_id}/pages/{page_number}/calibration` |
| team | Consulta | GET | `/team/meta` |
| team | Consulta | GET | `/team/rols` |
| team | Acción/Comando | POST | `/team/rols` |
| team | Eliminación | DELETE | `/team/rols/{rol_id}` |
| team | Actualización | PATCH | `/team/rols/{rol_id}` |
| team | Consulta | GET | `/team/users` |
| team | Autenticación | POST | `/team/users` |
| team | Eliminación | DELETE | `/team/users/{user_id}` |
| team | Actualización | PATCH | `/team/users/{user_id}` |
| Timeline | Eliminación | DELETE | `/timeline/dependencies/{dependency_id}` |
| Timeline | Eliminación | DELETE | `/timeline/milestones/{milestone_id}` |
| Timeline | Actualización | PATCH | `/timeline/milestones/{milestone_id}` |
| Timeline | Eliminación | DELETE | `/timeline/phases/{phase_id}` |
| Timeline | Actualización | PATCH | `/timeline/phases/{phase_id}` |
| Timeline | Consulta | GET | `/timeline/projects/{project_id}/dependencies` |
| Timeline | Acción/Comando | POST | `/timeline/projects/{project_id}/dependencies` |
| Timeline | Proceso/Automatización | POST | `/timeline/projects/{project_id}/import` |
| Timeline | Consulta | GET | `/timeline/projects/{project_id}/milestones` |
| Timeline | Acción/Comando | POST | `/timeline/projects/{project_id}/milestones` |
| Timeline | Consulta | GET | `/timeline/projects/{project_id}/phases` |
| Timeline | Acción/Comando | POST | `/timeline/projects/{project_id}/phases` |
| Timeline | Actualización | PATCH | `/timeline/projects/{project_id}/reorder` |
| Timeline | Consulta | GET | `/timeline/projects/{project_id}/summary` |
| units | Consulta | GET | `/units` |
| units | Creación/Comando | POST | `/units` |
| units | Eliminación | DELETE | `/units/{unit_id}` |
| units | Consulta | GET | `/units/{unit_id}` |
| Vault | Consulta | GET | `/vault/breadcrumb/{folder_id}` |
| Vault | Creación/Comando | POST | `/vault/duplicate/{file_id}` |
| Vault | Consulta | GET | `/vault/duplicates` |
| Vault | Consulta | GET | `/vault/files` |
| Vault | Eliminación | DELETE | `/vault/files/{file_id}` |
| Vault | Consulta | GET | `/vault/files/{file_id}` |
| Vault | Actualización | PATCH | `/vault/files/{file_id}` |
| Vault | Consulta | GET | `/vault/files/{file_id}/download` |
| Vault | Creación/Comando | POST | `/vault/files/{file_id}/restore/{version_id}` |
| Vault | Consulta | GET | `/vault/files/{file_id}/versions` |
| Vault | Acción/Comando | POST | `/vault/files/{file_id}/versions` |
| Vault | Acción/Comando | POST | `/vault/folders` |
| Vault | Consulta | GET | `/vault/receipt-status` |
| Vault | Proceso/Automatización | POST | `/vault/search` |
| Vault | Consulta | GET | `/vault/tree` |
| Vault | Acción/Comando | POST | `/vault/upload` |
| Vault | Acción/Comando | POST | `/vault/upload-chunk` |
| Vault | Acción/Comando | POST | `/vault/upload-complete` |
| vendors | Consulta | GET | `/vendors` |
| vendors | Creación/Comando | POST | `/vendors` |
| vendors | Eliminación | DELETE | `/vendors/{vendor_id}` |
| vendors | Consulta | GET | `/vendors/{vendor_id}` |
| vendors | Actualización | PATCH | `/vendors/{vendor_id}` |

## Módulo: accounts
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/accounts` | Consulta | Lista todas las cuentas ordenadas por nombre | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/accounts.py::list_accounts` |
| POST | `/accounts` | Creación/Comando | Crea una nueva cuenta | body `account` (modelo `AccountCreate`) | tabla `accounts` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/accounts.py::create_account` |
| DELETE | `/accounts/{account_id}` | Eliminación | Elimina una cuenta | path `account_id` (str) | tabla `accounts` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/accounts.py::delete_account` |
| GET | `/accounts/{account_id}` | Consulta | Obtiene una cuenta específica por ID | path `account_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/accounts.py::get_account` |
| PATCH | `/accounts/{account_id}` | Actualización | Actualiza una cuenta existente (actualización parcial) | path `account_id` (str); body `account` (modelo `AccountUpdate`) | tabla `accounts` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/accounts.py::update_account` |

## Módulo: ADU Calculator
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/adu-calculator/analyze-screenshot` | Proceso/Automatización | Analyze a floor plan screenshot using GPT-4o Vision. | archivo multipart `file`; form-data `floor_label` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`, `warning`; status usados en errores/respuestas: 400, 500 | `api/routers/adu_calculator.py::analyze_screenshot` |
| POST | `/adu-calculator/calculate` | Proceso/Automatización | Calculate estimated ADU construction cost. | body `payload` (modelo `ADUCalculateRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/adu_calculator.py::calculate_cost` |
| GET | `/adu-calculator/pricing-config` | Consulta | Load the pricing configuration from the database. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/adu_calculator.py::get_pricing_config` |
| PUT | `/adu-calculator/pricing-config` | Actualización | Save the pricing configuration to the database. | body `payload` (modelo `PricingConfigUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/adu_calculator.py::update_pricing_config` |

## Módulo: Analytics
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/analytics/budget-health` | Observabilidad | Budget health analysis across projects: per-project budget vs actual, | query `project_id` (Optional[str]); query `year` (Optional[int]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_account`, `projects`, `summary`, `utilization_distribution`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::budget_health` |
| GET | `/analytics/executive/kpis` | Consulta | Multi-project KPI aggregation for the executive dashboard. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `active_projects`, `avg_margin_pct`, `daneel_auth_rate`, `monthly_spend_total`, `projects`, `top_vendors`, `total_budget`, `total_spend`; status usados en errores/respuestas: 403, 500 | `api/routers/analytics.py::executive_kpis` |
| GET | `/analytics/expense-intelligence` | Consulta | Expense intelligence dashboard: monthly spend trends, category/vendor/ | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_category`, `by_day_of_week`, `by_payment_method`, `by_vendor`, `category_trends`, `mom_delta`, `monthly_spend`, `top_anomalies`, `totals`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::expense_intelligence` |
| GET | `/analytics/health/all` | Observabilidad | Aggregated health snapshot across ALL active projects. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `active_projects_count`, `authorized_amount`, `authorized_count`, `budget_total`, `by_category`, `daneel`, `monthly_spend`, `pending_auth_amount`, `pending_auth_count`, `pending_receipts_count`, `project_id`, `project_name` | `api/routers/analytics.py::health_all` |
| GET | `/analytics/project-scorecard` | Consulta | Cross-project scorecard: budget health, timeline progress, milestone | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `projects`, `summary`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::project_scorecard` |
| GET | `/analytics/projects/{project_id}/budget-vs-actual` | Consulta | Budget variance analysis by account with projection and EAC. | path `project_id` (str); query `year` (Optional[int]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `budget_year`, `by_account`, `cumulative_actual`, `estimated_at_completion`, `estimated_variance`, `project_id`, `projection`, `status`, `total_actual`, `total_budget`, `total_variance`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::budget_vs_actual` |
| GET | `/analytics/projects/{project_id}/cost-trends` | Consulta | Monthly spend series with cumulative totals, suitable for | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `monthly`, `project_id` | `api/routers/analytics.py::cost_trends` |
| GET | `/analytics/projects/{project_id}/expense-timeline` | Consulta | Returns individual expense records enriched with AccountCategory, | path `project_id` (str); query `year` (Optional[int]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `budget_by_category`, `date_range`, `expenses`, `project_id`, `total_budget`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::expense_timeline` |
| GET | `/analytics/projects/{project_id}/health` | Observabilidad | Aggregated project health snapshot: budget, expenses, receipts, | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `authorized_amount`, `authorized_count`, `budget_total`, `by_category`, `daneel`, `monthly_spend`, `pending_auth_amount`, `pending_auth_count`, `pending_receipts_count`, `project_id`, `project_name`, `remaining` | `api/routers/analytics.py::project_health` |
| GET | `/analytics/vendors/summary` | Consulta | Enriched vendor listing with spend metrics, transaction counts, | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `new_vs_recurring`, `pareto`, `total_spend_all_vendors`, `vendors`; status usados en errores/respuestas: 500 | `api/routers/analytics.py::vendors_summary` |
| GET | `/analytics/vendors/{vendor_id}/scorecard` | Consulta | Detailed vendor analytics scorecard: spend trends, project breakdown, | path `vendor_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `active_projects`, `avg_txn_amount`, `by_category`, `by_project`, `concentration`, `first_txn`, `monthly_spend`, `total_amount`, `txn_count`, `vendor_id`, `vendor_name`; status usados en errores/respuestas: 404, 500 | `api/routers/analytics.py::vendor_scorecard` |

## Módulo: andrew
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/andrew/follow-up-check` | Proceso/Automatización | Check for receipts awaiting human response and send follow-ups. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `ok`, `receipts_checked`, `stats`; status usados en errores/respuestas: 500 | `api/routers/andrew_mismatch.py::run_follow_up_check` |
| GET | `/andrew/mismatch-config` | Consulta | Get Andrew mismatch reconciliation config. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/andrew_mismatch.py::get_mismatch_config` |
| PUT | `/andrew/mismatch-config` | Actualización | Update Andrew mismatch reconciliation config. | body `payload` (modelo `MismatchConfigUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`, `updated_keys`; status usados en errores/respuestas: 500 | `api/routers/andrew_mismatch.py::update_mismatch_config` |
| GET | `/andrew/pending-receipts-status` | Consulta | Get summary of receipts currently awaiting human response. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `overdue`, `receipts`, `total_awaiting`; status usados en errores/respuestas: 500 | `api/routers/andrew_mismatch.py::get_pending_receipts_status` |
| POST | `/andrew/reconcile-bill` | Acción/Comando | Trigger Andrew's mismatch reconciliation protocol for a specific bill. | query `bill_id` (str); query `project_id` (str); query `source` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/andrew_mismatch.py::reconcile_bill` |

## Módulo: art
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/art/clear-thread` | Acción/Comando | Limpia el thread de conversación y crea uno nuevo. | dato `session_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `success`, `thread_id`; status usados en errores/respuestas: 500 | `api/routers/arturito.py::clear_conversation` |
| POST | `/art/delegate-task` | Acción/Comando | Envía una solicitud de tarea a otro equipo. | body `request` (modelo `DelegationRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `request`, `success`, `team`, `text`; status usados en errores/respuestas: 404 | `api/routers/arturito.py::delegate_task` |
| GET | `/art/failed-commands` | Consulta | Get failed copilot commands for analytics. | query `page` (int); query `page_size` (int); query `current_page` (Optional[str]); query `error_reason` (Optional[str]); query `days_back` (int); query `user_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 403, 500 | `api/routers/arturito.py::get_failed_commands_endpoint` |
| GET | `/art/failed-commands/stats` | Consulta | Get aggregated statistics about failed commands. | query `days_back` (int); query `user_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 403, 500 | `api/routers/arturito.py::get_failed_commands_stats_endpoint` |
| GET | `/art/health` | Observabilidad | Health check para verificar que el bot está activo | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `assistants_api`, `status`, `timestamp` | `api/routers/arturito.py::health_check` |
| POST | `/art/interpret-filter` | Proceso/Automatización | Use GPT to interpret a natural language expense filter command. | body `request` (modelo `FilterInterpretRequest`) | No evidente en este handler (lectura o delegación a servicio). | modelo `FilterInterpretResponse` | `api/routers/arturito.py::interpret_filter_command` |
| POST | `/art/interpret-intent` | Proceso/Automatización | GPT-first intent interpreter. Classifies user messages based on | body `request` (modelo `IntentRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | modelo `IntentResponse` | `api/routers/arturito.py::interpret_intent` |
| POST | `/art/message` | Acción/Comando | Endpoint principal que recibe mensajes desde Google Chat. | body `message` (modelo `ChatMessage`) | No evidente en este handler (lectura o delegación a servicio). | modelo `BotResponse` | `api/routers/arturito.py::receive_message` |
| GET | `/art/permissions` | Consulta | Obtiene la configuración actual de permisos de Arturito. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_category`, `permissions` | `api/routers/arturito.py::get_permissions` |
| PATCH | `/art/permissions` | Actualización | Actualiza un permiso específico de Arturito. | body `update` (modelo `PermissionUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `enabled`, `intent`, `message`, `success`; status usados en errores/respuestas: 403, 404 | `api/routers/arturito.py::update_permission` |
| POST | `/art/permissions/reset` | Acción/Comando | Resetea todos los permisos a sus valores por defecto. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `success`; status usados en errores/respuestas: 403 | `api/routers/arturito.py::reset_permissions` |
| POST | `/art/personality` | Acción/Comando | Endpoint para cambiar la personalidad del bot | dato `level` (int); dato `space_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400 | `api/routers/arturito.py::set_personality` |
| GET | `/art/personality/{space_id}` | Consulta | Obtiene la configuración de personalidad actual | path `space_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `emoji`, `level`, `space_id`, `title` | `api/routers/arturito.py::get_personality` |
| GET | `/art/search-accounts` | Proceso/Automatización | Search for accounts using hybrid fuzzy + semantic matching. | query `query` (str); query `limit` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `matches`, `method` | `api/routers/arturito.py::search_accounts` |
| POST | `/art/slash` | Acción/Comando | Endpoint para slash commands de Google Chat | body `command` (modelo `SlashCommand`) | No evidente en este handler (lectura o delegación a servicio). | modelo `BotResponse` | `api/routers/arturito.py::receive_slash_command` |
| POST | `/art/web-chat` | Acción/Comando | Endpoint para el chat web de NGM HUB usando OpenAI Assistants API. | body `message` (modelo `WebChatMessage`) | No evidente en este handler (lectura o delegación a servicio). | modelo `BotResponse` | `api/routers/arturito.py::web_chat` |
| POST | `/art/webhook` | Integración externa | Webhook compatible con el formato de Google Chat. | body `payload` (modelo `Dict[str, Any]`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `text` | `api/routers/arturito.py::google_chat_webhook` |

## Módulo: auth
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/auth/create_user` | Autenticación | Create user | body `payload` (modelo `CreateUserRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `user`; status usados en errores/respuestas: 400, 500 | `api/auth.py::create_user` |
| POST | `/auth/login` | Autenticación | Login | body `payload` (modelo `LoginRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `access_token`, `message`, `token_type`, `user`; status usados en errores/respuestas: 401, 500 | `api/auth.py::login` |
| GET | `/auth/me` | Autenticación | Me | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `user_id`, `user_name`, `user_role`; status usados en errores/respuestas: 401 | `api/auth.py::me` |

## Módulo: bills
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/bills` | Consulta | Lista todos los bills ordenados por fecha de creación (más recientes primero) | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/bills.py::list_bills` |
| POST | `/bills` | Creación/Comando | Crea un nuevo bill | body `bill` (modelo `BillCreate`) | tabla `bills` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/bills.py::create_bill` |
| DELETE | `/bills/{bill_id}` | Eliminación | Elimina un bill | path `bill_id` (str) | tabla `bills` (delete) | JSON con llaves típicas: `affected_expenses`, `info`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/bills.py::delete_bill` |
| GET | `/bills/{bill_id}` | Consulta | Obtiene un bill específico por ID | path `bill_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/bills.py::get_bill` |
| PATCH | `/bills/{bill_id}` | Actualización | Actualiza un bill existente (actualización parcial) | path `bill_id` (str); body `bill` (modelo `BillUpdate`) | tabla `bills` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/bills.py::update_bill` |
| POST | `/bills/{bill_id}/close` | Acción/Comando | Marca un bill como cerrado (closed) | path `bill_id` (str) | tabla `bills` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/bills.py::close_bill` |
| GET | `/bills/{bill_id}/expenses` | Consulta | Obtiene todos los gastos asociados a un bill específico. | path `bill_id` (str); query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `bill_id`, `count`, `expenses`; status usados en errores/respuestas: 500 | `api/routers/bills.py::get_bill_expenses` |
| POST | `/bills/{bill_id}/mark-split` | Acción/Comando | Marca un bill como split (gastos distribuidos en múltiples proyectos) | path `bill_id` (str); body `split_projects` (modelo `List[int]`) | tabla `bills` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/bills.py::mark_bill_split` |
| POST | `/bills/{bill_id}/reopen` | Acción/Comando | Reabre un bill (cambia status a open) | path `bill_id` (str) | tabla `bills` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/bills.py::reopen_bill` |

## Módulo: budget-alerts
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| PUT | `/budget-alerts/acknowledge/{alert_id}` | Actualización | Acknowledge a budget alert with a note. | path `alert_id` (str); body `request` (modelo `AcknowledgeAlertRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `budget_alert_actions` (insert) | JSON con llaves típicas: `acknowledged_by`, `alert_id`, `message`, `new_status`, `note`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/budget_alerts.py::acknowledge_alert` |
| GET | `/budget-alerts/acknowledge/{alert_id}/history` | Consulta | Get the action history for a specific alert. | path `alert_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_alert_action_history` |
| POST | `/budget-alerts/acknowledge/{alert_id}/reopen` | Acción/Comando | Reopen a previously acknowledged/resolved alert. | path `alert_id` (str); dato `note` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `budget_alert_actions` (insert) | JSON con llaves típicas: `alert_id`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/budget_alerts.py::reopen_alert` |
| POST | `/budget-alerts/check` | Proceso/Automatización | Manually trigger a budget check. | body `background_tasks` (modelo `BackgroundTasks`); dato `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `project_id`, `scope`, `status`, `triggered_by`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::trigger_budget_check` |
| GET | `/budget-alerts/history` | Consulta | Get history of sent alerts. | query `project_id` (Optional[str]); query `alert_type` (Optional[str]); query `limit` (int); query `offset` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `total`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_alert_history` |
| PUT | `/budget-alerts/history/{alert_id}/read` | Actualización | Mark an alert as read. | path `alert_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::mark_alert_read` |
| GET | `/budget-alerts/notifications` | Consulta | Get dashboard notifications for the current user. | query `unread_only` (bool); query `limit` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_dashboard_notifications` |
| PUT | `/budget-alerts/notifications/read-all` | Actualización | Mark all notifications as read for the current user. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::mark_all_notifications_read` |
| PUT | `/budget-alerts/notifications/{notification_id}/read` | Actualización | Mark a dashboard notification as read. | path `notification_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::mark_notification_read` |
| GET | `/budget-alerts/pending` | Consulta | Get alerts pending acknowledgment. | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_pending_acknowledgments` |
| GET | `/budget-alerts/pending/count` | Consulta | Get count of alerts pending acknowledgment. | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_pending_acknowledgment_count` |
| GET | `/budget-alerts/permissions` | Consulta | Get acknowledgment permissions for the current user. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `allowed_alert_types`, `can_acknowledge`, `can_dismiss`, `can_resolve`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_acknowledgment_permissions` |
| GET | `/budget-alerts/recipients` | Consulta | Get all recipients for budget alerts. | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_alert_recipients` |
| POST | `/budget-alerts/recipients` | Acción/Comando | Add a user as a budget alert recipient. | body `recipient` (modelo `AlertRecipientCreate`); dato `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/budget_alerts.py::add_alert_recipient` |
| DELETE | `/budget-alerts/recipients/{recipient_id}` | Eliminación | Remove a user from budget alert recipients. | path `recipient_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::remove_alert_recipient` |
| PUT | `/budget-alerts/recipients/{recipient_id}` | Actualización | Update a recipient's notification preferences. | path `recipient_id` (str); body `update` (modelo `AlertRecipientUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/budget_alerts.py::update_alert_recipient` |
| GET | `/budget-alerts/settings` | Consulta | Get alert settings (global or project-specific). | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `scope`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_alert_settings` |
| PUT | `/budget-alerts/settings` | Actualización | Update alert settings (global or project-specific). | body `settings` (modelo `AlertSettingsUpdate`); dato `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::update_alert_settings` |
| GET | `/budget-alerts/summary` | Consulta | Get a summary of budget alert status. | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `alerts_last_7_days`, `by_type`, `unread_count`; status usados en errores/respuestas: 500 | `api/routers/budget_alerts.py::get_budget_alert_summary` |

## Módulo: budgets
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/budgets` | Consulta | Get budgets, optionally filtered by project and/or year | query `project` (Optional[str]); query `year` (Optional[int]); query `active_only` (bool); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/budgets.py::get_budgets` |
| DELETE | `/budgets/batch/{batch_id}` | Eliminación | Delete all budgets from a specific import batch | path `batch_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `budgets_qbo` (delete) | JSON con llaves típicas: `count`, `message`; status usados en errores/respuestas: 500 | `api/routers/budgets.py::delete_batch` |
| POST | `/budgets/import` | Proceso/Automatización | Import budgets from CSV data | body `request` (modelo `BudgetImportRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `budgets_qbo` (insert) | JSON con llaves típicas: `batch_id`, `count`, `message`, `project_id`; status usados en errores/respuestas: 400, 500 | `api/routers/budgets.py::import_budgets` |

## Módulo: categorization-ml
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/categorization-ml/predict` | Acción/Comando | Test a single description against the ML model. | dato `payload` (dict) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `model_status`, `prediction`, `success`; status usados en errores/respuestas: 400 | `api/routers/categorization_ml.py::predict_category` |
| POST | `/categorization-ml/predict-batch` | Acción/Comando | Test multiple descriptions against the ML model. | dato `payload` (dict) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `classified`, `hit_rate`, `message`, `model_status`, `predictions`, `success`, `total`; status usados en errores/respuestas: 400 | `api/routers/categorization_ml.py::predict_batch` |
| GET | `/categorization-ml/status` | Consulta | Get current model status: trained, stale, training size, features, etc. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `success` | `api/routers/categorization_ml.py::model_status` |
| POST | `/categorization-ml/train` | Acción/Comando | Force retrain the ML categorization model from current expense data. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `success`; status usados en errores/respuestas: 500 | `api/routers/categorization_ml.py::train_model` |

## Módulo: Comments
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/comments/` | Consulta | List comments for a specific cell or row. | query `module` (str); query `record_id` (str); query `column_key` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `comments`; status usados en errores/respuestas: 500 | `api/routers/comments.py::list_comments` |
| POST | `/comments/` | Creación/Comando | Create a new comment. Returns the created row with user info. | body `payload` (modelo `CommentCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `cell_comments` (insert) | status usados en errores/respuestas: 500 | `api/routers/comments.py::create_comment` |
| GET | `/comments/counts` | Consulta | Get comment counts per record_id + column_key for batch rendering. | query `module` (str); query `record_ids` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `counts`; status usados en errores/respuestas: 500 | `api/routers/comments.py::comment_counts` |
| DELETE | `/comments/{comment_id}` | Eliminación | Delete a comment. Only the creator can delete. | path `comment_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `cell_comments` (delete) | JSON con llaves típicas: `deleted_id`, `ok`; status usados en errores/respuestas: 403, 404, 500 | `api/routers/comments.py::delete_comment` |
| PATCH | `/comments/{comment_id}` | Actualización | Update a comment. Only the creator can edit. | path `comment_id` (str); body `payload` (modelo `CommentUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `cell_comments` (update) | status usados en errores/respuestas: 403, 404, 500 | `api/routers/comments.py::update_comment` |
| PATCH | `/comments/{comment_id}/resolve` | Actualización | Toggle resolved state on a comment. | path `comment_id` (str); body `payload` (modelo `CommentResolve`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `cell_comments` (update) | status usados en errores/respuestas: 404, 500 | `api/routers/comments.py::resolve_comment` |

## Módulo: companies
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/companies` | Consulta | Lista todas las companies ordenadas por nombre, con busqueda opcional. | query `q` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/companies.py::list_companies` |
| POST | `/companies` | Creación/Comando | Crea una nueva company. | body `company` (modelo `CompanyCreate`) | tabla `companies` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/companies.py::create_company` |
| DELETE | `/companies/{company_id}` | Eliminación | Elimina una company. | path `company_id` (str) | tabla `companies` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/companies.py::delete_company` |
| GET | `/companies/{company_id}` | Consulta | Obtiene una company especifica por ID. | path `company_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/companies.py::get_company` |
| PATCH | `/companies/{company_id}` | Actualización | Actualiza una company existente (actualizacion parcial). | path `company_id` (str); body `company` (modelo `CompanyUpdate`) | tabla `companies` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/companies.py::update_company` |

## Módulo: concepts
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/concepts` | Consulta | Lista conceptos con paginacion y filtros. | query `page` (int); query `page_size` (int); query `search` (Optional[str]); query `category_id` (Optional[str]); query `class_id` (Optional[str]); query `is_template` (Optional[bool]); query `include_inactive` (bool) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `pagination`; status usados en errores/respuestas: 500 | `api/routers/concepts.py::list_concepts` |
| POST | `/concepts` | Creación/Comando | Crea un nuevo concepto. | body `concept` (modelo `ConceptCreate`) | tabla `concepts` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/concepts.py::create_concept` |
| DELETE | `/concepts/{concept_id}` | Eliminación | Elimina un concepto (soft delete si tiene materiales). | path `concept_id` (str) | tabla `concepts` (delete/update) | JSON con llaves típicas: `message`, `soft_delete`; status usados en errores/respuestas: 404, 500 | `api/routers/concepts.py::delete_concept` |
| GET | `/concepts/{concept_id}` | Consulta | Obtiene un concepto especifico con sus materiales. | path `concept_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `base_cost`, `builder`, `calculated_cost`, `category_id`, `category_name`, `class_id`, `class_name`, `code`, `cost_breakdown`, `created_at`, `full_description`, `id`; status usados en errores/respuestas: 404, 500 | `api/routers/concepts.py::get_concept` |
| PATCH | `/concepts/{concept_id}` | Actualización | Actualiza un concepto existente. | path `concept_id` (str); body `concept` (modelo `ConceptUpdate`) | tabla `concepts` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/concepts.py::update_concept` |
| GET | `/concepts/{concept_id}/materials` | Consulta | Lista los materiales de un concepto. | path `concept_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`, `total_cost`; status usados en errores/respuestas: 404, 500 | `api/routers/concepts.py::get_concept_materials` |
| POST | `/concepts/{concept_id}/materials` | Acción/Comando | Agrega un material a un concepto. | path `concept_id` (str); body `material` (modelo `ConceptMaterialAdd`) | tabla `concept_materials` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/concepts.py::add_material_to_concept` |
| PUT | `/concepts/{concept_id}/materials/sync` | Proceso/Automatización | Reemplaza todos los materiales de un concepto atomicamente. | path `concept_id` (str); body `payload` (modelo `ConceptMaterialSync`) | tabla `concept_materials` (delete/insert) | JSON con llaves típicas: `errors`, `inserted`, `message`; status usados en errores/respuestas: 404, 500 | `api/routers/concepts.py::sync_concept_materials` |
| DELETE | `/concepts/{concept_id}/materials/{material_entry_id}` | Eliminación | Elimina un material de un concepto. | path `concept_id` (str); path `material_entry_id` (str) | tabla `concept_materials` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/concepts.py::remove_material_from_concept` |
| PATCH | `/concepts/{concept_id}/materials/{material_entry_id}` | Actualización | Actualiza un material en un concepto. | path `concept_id` (str); path `material_entry_id` (str); body `material` (modelo `ConceptMaterialUpdate`) | tabla `concept_materials` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/concepts.py::update_concept_material` |

## Módulo: core
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/` | Consulta | Root (oculto en OpenAPI) | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message` | `api/main.py::root` |
| GET | `/debug/memory` | Observabilidad | Return current memory usage and cache sizes for leak monitoring. (oculto en OpenAPI) | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `cache_sizes`, `gc_counts`, `rss_mb`, `vms_mb` | `api/main.py::debug_memory` |
| GET | `/departments` | Consulta | Get list of all departments. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `departments` | `api/main.py::get_departments` |
| GET | `/health` | Observabilidad | Health (oculto en OpenAPI) | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `database`, `status` | `api/main.py::health` |

## Módulo: daneel
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/daneel/auto-auth/config` | Consulta | Get all auto-auth configuration values. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::get_auto_auth_config` |
| PUT | `/daneel/auto-auth/config` | Actualización | Update auto-auth configuration values. | body `payload` (modelo `AutoAuthConfigUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`, `updated_keys`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::update_auto_auth_config` |
| POST | `/daneel/auto-auth/digest` | Acción/Comando | Flush the digest queue: consolidate un-sent auth results into one | query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::run_digest` |
| POST | `/daneel/auto-auth/follow-up-check` | Proceso/Automatización | Check for pending info items awaiting human response and send follow-ups. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `items_checked`, `message`, `ok`, `stats`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::run_follow_up_check` |
| GET | `/daneel/auto-auth/pending-info` | Consulta | List expenses currently waiting for missing info. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::list_pending_info` |
| GET | `/daneel/auto-auth/pending-info-status` | Consulta | Get summary of expenses currently awaiting missing info. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `items`, `overdue`, `stale`, `total`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::get_pending_info_status` |
| GET | `/daneel/auto-auth/project-summary` | Consulta | Per-project expense summary for Daneel dashboard. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::get_project_summary` |
| DELETE | `/daneel/auto-auth/reports` | Eliminación | Delete all auth reports. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `ok`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::delete_auth_reports` |
| GET | `/daneel/auto-auth/reports` | Consulta | List recent auth reports with decisions (newest first). | query `limit` (int); query `offset` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::list_auth_reports` |
| GET | `/daneel/auto-auth/reports/{report_id}` | Consulta | Get a single auth report with full decision detail. | path `report_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `created_at`, `decisions`, `project_id`, `project_name`, `report_id`, `report_type`, `summary`; status usados en errores/respuestas: 404, 500 | `api/routers/daneel_auto_auth.py::get_auth_report` |
| POST | `/daneel/auto-auth/reprocess` | Acción/Comando | Re-check expenses that were waiting for missing info. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::reprocess_pending` |
| POST | `/daneel/auto-auth/run` | Proceso/Automatización | Manually trigger a full auto-authorization run. | query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `authorized`, `background`, `expenses_processed`, `message`, `status`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::run_auto_auth` |
| POST | `/daneel/auto-auth/run-backlog` | Proceso/Automatización | One-time run: process ALL pending expenses regardless of creation date. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `authorized`, `background`, `expenses_processed`, `message`, `mode`, `status`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::run_auto_auth_backlog` |
| GET | `/daneel/auto-auth/status` | Consulta | Get auto-auth status: config, last run, pending info count, | query `days` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `authorized_last_30d`, `config`, `enabled`, `last_run`, `pending_info_count`, `period_days`, `rates`, `total_processed`; status usados en errores/respuestas: 500 | `api/routers/daneel_auto_auth.py::get_auto_auth_status` |

## Módulo: debug
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/debug/supabase-ping` | Observabilidad | Supabase ping | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `rows_found`, `sample`; status usados en errores/respuestas: 500 | `api/routers/debug_supabase.py::supabase_ping` |

## Módulo: estimator
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/estimator/base-structure` | Consulta | Legacy endpoint: Load base structure from local file. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/estimator.py::get_base_structure` |
| GET | `/estimator/buckets/init` | Consulta | Initialize estimates and templates buckets. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `buckets`, `success` | `api/routers/estimator.py::init_buckets` |
| GET | `/estimator/buckets/status` | Consulta | Get status of estimator buckets | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/estimator.py::get_buckets_status` |
| GET | `/estimator/estimates` | Consulta | List all saved estimates from the estimates bucket. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `estimates`; status usados en errores/respuestas: 500 | `api/routers/estimator.py::list_estimates` |
| POST | `/estimator/estimates` | Acción/Comando | Save an estimate to the estimates bucket. | body `request` (modelo `EstimateSaveRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `estimate_id`, `message`, `success`; status usados en errores/respuestas: 500 | `api/routers/estimator.py::save_estimate` |
| DELETE | `/estimator/estimates/{estimate_id}` | Eliminación | Delete an estimate and all its files. | path `estimate_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `estimate_id`, `files_deleted`, `success`; status usados en errores/respuestas: 404, 500 | `api/routers/estimator.py::delete_estimate` |
| GET | `/estimator/estimates/{estimate_id}` | Consulta | Load a specific estimate by ID. | path `estimate_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/estimator.py::get_estimate` |
| POST | `/estimator/save` | Acción/Comando | Legacy endpoint: Save estimate to local file. | body `payload` (modelo `Dict[str, Any]`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `path`, `status`; status usados en errores/respuestas: 400, 500 | `api/routers/estimator.py::save_estimate_legacy` |
| GET | `/estimator/templates` | Consulta | List all saved templates from the templates bucket. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `templates`; status usados en errores/respuestas: 500 | `api/routers/estimator.py::list_templates` |
| POST | `/estimator/templates` | Acción/Comando | Save a template to the templates bucket. | body `request` (modelo `TemplateSaveRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `success`, `template_id`; status usados en errores/respuestas: 500 | `api/routers/estimator.py::save_template` |
| DELETE | `/estimator/templates/{template_id}` | Eliminación | Delete a template and all its files. | path `template_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `files_deleted`, `success`, `template_id`; status usados en errores/respuestas: 404, 500 | `api/routers/estimator.py::delete_template` |
| GET | `/estimator/templates/{template_id}` | Consulta | Load a specific template by ID. | path `template_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/estimator.py::get_template` |
| GET | `/estimator/templates/{template_id}/meta` | Consulta | Get template metadata (name, description, created_at). | path `template_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `description`, `name`, `template_id` | `api/routers/estimator.py::get_template_meta` |

## Módulo: Expenses
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/expenses` | Consulta | Lista todos los gastos, opcionalmente filtrados por project. | query `project` (Optional[str]); query `limit` (Optional[int]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::list_expenses` |
| POST | `/expenses` | Creación/Comando | Crea un nuevo gasto | body `payload` (modelo `ExpenseCreate`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expenses_manual_COGS` (insert) | JSON con llaves típicas: `expense`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::create_expense` |
| GET | `/expenses/all` | Consulta | Lista todos los gastos de todos los proyectos. | query `limit` (Optional[int]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::list_all_expenses` |
| POST | `/expenses/auto-categorize` | Acción/Comando | Auto-categorizes expenses based on construction stage and description. | body `payload` (modelo `AutoCategorizeRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `categorizations`, `metrics`, `success`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::auto_categorize_expenses` |
| PATCH | `/expenses/batch` | Actualización | Actualiza múltiples gastos en una sola operación. | body `payload` (modelo `ExpenseBatchUpdate`); dato `user_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expense_status_log` (insert); tabla `expenses_manual_COGS` (update) | JSON con llaves típicas: `failed`, `message`, `summary`, `updated`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::update_expenses_batch` |
| POST | `/expenses/batch` | Acción/Comando | Crea múltiples gastos en una sola operación (bulk insert). | body `payload` (modelo `ExpenseBatchCreate`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expenses_manual_COGS` (insert) | JSON con llaves típicas: `created`, `failed`, `message`, `summary`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::create_expenses_batch` |
| POST | `/expenses/categorization-correction` | Acción/Comando | Save a user correction to auto-categorization for feedback loop. | body `payload` (modelo `CategorizationCorrectionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `categorization_corrections` (insert) | JSON con llaves típicas: `correction_id`, `message`, `success`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::save_categorization_correction` |
| POST | `/expenses/check-receipt-type` | Proceso/Automatización | Quick check: is this file a text-based PDF (fast-mode compatible) | archivo multipart `file`; autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `char_count`, `file_type`, `has_text`; status usados en errores/respuestas: 400 | `api/routers/expenses.py::check_receipt_type` |
| GET | `/expenses/dismissed-duplicates` | Consulta | Obtiene todos los pares de duplicados descartados por un usuario. | query `user_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::get_dismissed_duplicates` |
| POST | `/expenses/dismissed-duplicates` | Acción/Comando | Marca un par de expenses como "no es duplicado". | body `payload` (modelo `DismissDuplicateRequest`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `dismissed_expense_duplicates` (upsert) | JSON con llaves típicas: `duplicate_ids`, `message`, `user_id`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::dismiss_duplicate_pair` |
| DELETE | `/expenses/dismissed-duplicates/{dismissal_id}` | Eliminación | Elimina un dismissal para reactivar la alerta de duplicado. | path `dismissal_id` (str); dato `user_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `dismissed_expense_duplicates` (delete) | JSON con llaves típicas: `dismissal_id`, `message`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::reactivate_duplicate_alert` |
| GET | `/expenses/duplicate-scan` | Consulta | Lightweight duplicate scan for a project. | query `project` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `duplicate_ids`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::scan_duplicates` |
| GET | `/expenses/export` | Proceso/Automatización | Exporta los gastos filtrados como archivo CSV o Excel (.xlsx). | body `format` (modelo `ExportFormat`); query `project` (Optional[str]); query `vendor_id` (Optional[str]); query `txn_type` (Optional[str]); query `account_id` (Optional[str]); query `payment_type` (Optional[str]); query `status` (Optional[str]); query `date_from` (Optional[str]); query `date_to` (Optional[str]); query `search` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::export_expenses` |
| GET | `/expenses/meta` | Consulta | Devuelve catálogos necesarios para la UI de expenses: | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/expenses.py::get_expenses_meta` |
| GET | `/expenses/metrics/categorization-errors` | Consulta | Get metrics on categorization errors based on expenses flagged for review. | query `start_date` (Optional[str]); query `end_date` (Optional[str]); query `user_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_reason`, `date_range`, `error_rate_percent`, `flagged_for_review`, `total_reviewed`, `trend_by_month`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::get_categorization_error_metrics` |
| POST | `/expenses/parse-receipt` | Acción/Comando | Parsea un recibo/factura usando OpenAI Vision API. | archivo multipart `file`; form-data `model` (str); form-data `correction_context` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`, `execution_time_seconds`, `extraction_method`, `model_used`, `success`; status usados en errores/respuestas: 400, 500 | `api/routers/expenses.py::parse_receipt` |
| GET | `/expenses/pending-authorization/count` | Consulta | Get count of expenses pending authorization for a user. | query `user_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `can_authorize`, `count`, `message`, `role`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::get_pending_authorization_count` |
| GET | `/expenses/pending-authorization/summary` | Consulta | Get summary of expenses pending authorization for dashboard display. | query `user_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_project`, `can_authorize`, `role`, `total_amount`, `total_count`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::get_pending_authorization_summary` |
| GET | `/expenses/summary/by-project` | Consulta | Obtiene un resumen de gastos agrupados por proyecto. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::get_expenses_summary_by_project` |
| GET | `/expenses/summary/by-txn-type` | Consulta | Obtiene un resumen de gastos agrupados por tipo de transacción. | query `project` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/expenses.py::get_expenses_summary_by_txn_type` |
| DELETE | `/expenses/{expense_id}` | Eliminación | Elimina un gasto con logging si estaba autorizado. | path `expense_id` (str); dato `user_id` (Optional[str]); dato `delete_reason` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expense_status_log` (insert); tabla `expenses_manual_COGS` (delete) | JSON con llaves típicas: `logged`, `message`, `was_authorized`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/expenses.py::delete_expense` |
| GET | `/expenses/{expense_id}` | Consulta | Obtiene un gasto específico por ID. | path `expense_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::get_expense` |
| PATCH | `/expenses/{expense_id}` | Actualización | Actualiza parcialmente un gasto existente con logging de cambios. | path `expense_id` (str); body `payload` (modelo `ExpenseUpdate`); body `background_tasks` (modelo `BackgroundTasks`); dato `user_id` (Optional[str]); dato `change_reason` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `daneel_pending_info` (update); tabla `expenses_manual_COGS` (update) | JSON con llaves típicas: `changes_logged`, `expense`, `message`, `status_changed`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/expenses.py::patch_expense` |
| PUT | `/expenses/{expense_id}` | Actualización | Actualiza un gasto existente | path `expense_id` (str); body `payload` (modelo `ExpenseUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expenses_manual_COGS` (update) | JSON con llaves típicas: `expense`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/expenses.py::update_expense` |
| GET | `/expenses/{expense_id}/audit-trail` | Consulta | Get complete audit trail for an expense (status changes + field changes). | path `expense_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `audit_trail`, `expense_id`, `total_changes`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::get_expense_audit_trail` |
| POST | `/expenses/{expense_id}/soft-delete` | Acción/Comando | Soft-delete: cambia el status a 'review' para que un manager confirme la eliminacion. | path `expense_id` (str); dato `user_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `daneel_pending_info` (update); tabla `expense_status_log` (insert); tabla `expenses_manual_COGS` (update) | JSON con llaves típicas: `expense_id`, `message`, `new_status`, `old_status`, `status`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::soft_delete_expense` |
| PATCH | `/expenses/{expense_id}/status` | Actualización | Update expense status with audit logging. | path `expense_id` (str); body `payload` (modelo `ExpenseStatusUpdate`); dato `user_id` (str); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expense_status_log` (insert); tabla `expenses_manual_COGS` (update) | JSON con llaves típicas: `expense_id`, `logged`, `message`, `new_status`, `old_status`; status usados en errores/respuestas: 403, 404, 500 | `api/routers/expenses.py::update_expense_status` |
| GET | `/expenses/{expense_id}/status-history` | Consulta | Get status change history for an expense. | path `expense_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `expense_id`, `history`; status usados en errores/respuestas: 404, 500 | `api/routers/expenses.py::get_expense_status_history` |

## Módulo: hari
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/hari/config` | Consulta | Get all Hari configuration values. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/hari.py::get_hari_config` |
| POST | `/hari/config` | Acción/Comando | Update Hari configuration values. | body `payload` (modelo `HariConfigUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`, `updated_keys`; status usados en errores/respuestas: 500 | `api/routers/hari.py::update_hari_config` |
| POST | `/hari/follow-up/run` | Proceso/Automatización | Manually trigger the follow-up engine check. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/hari.py::run_follow_up` |
| GET | `/hari/stats` | Consulta | Get task statistics for the Agent Hub dashboard. | query `days` (int); query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/hari.py::get_stats` |
| GET | `/hari/tasks` | Consulta | List coordinator tasks with optional filters. | query `project_id` (Optional[str]); query `assignee_name` (Optional[str]); query `status` (Optional[str]); query `limit` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `tasks`; status usados en errores/respuestas: 500 | `api/routers/hari.py::list_tasks` |
| POST | `/hari/tasks/{task_id}/action` | Acción/Comando | Perform an action on a task (confirm, cancel, complete, etc.). | path `task_id` (str); body `payload` (modelo `TaskActionRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`, `task`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/hari.py::task_action` |

## Módulo: invoice-links
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/invoice-links/create` | Acción/Comando | Create a payment link. Auth required (staff only). | body `req` (modelo `CreateInvoiceLinkRequest`); body `request` (modelo `Request`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `invoice_links` (insert) | modelo `CreateInvoiceLinkResponse`; status usados en errores/respuestas: 400, 500 | `api/routers/invoice_links.py::create_invoice_link` |
| GET | `/invoice-links/list` | Consulta | List invoice links created by the current user. Auth required. | autenticación/autorización (token o usuario resuelto por dependencia); query `status` (Optional[str]); query `limit` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `links` | `api/routers/invoice_links.py::list_invoice_links` |
| POST | `/invoice-links/mark-paid` | Acción/Comando | Mark a payment link as paid after successful Stripe checkout. | body `req` (modelo `MarkPaidRequest`) | tabla `invoice_links` (update) | JSON con llaves típicas: `message`, `ok`; status usados en errores/respuestas: 400, 404 | `api/routers/invoice_links.py::mark_invoice_link_paid` |
| GET | `/invoice-links/verify` | Consulta | Verify a payment link. Public endpoint (no auth). | autenticación/autorización (token o usuario resuelto por dependencia) | tabla `invoice_links` (update) | modelo `VerifyInvoiceLinkResponse`; status usados en errores/respuestas: 400, 404 | `api/routers/invoice_links.py::verify_invoice_link` |

## Módulo: material-categories
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/material-categories` | Consulta | Lista todas las categorias de materiales. | query `include_inactive` (bool) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/material_categories.py::list_categories` |
| POST | `/material-categories` | Creación/Comando | Crea una nueva categoria. | body `category` (modelo `CategoryCreate`) | tabla `material_categories` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/material_categories.py::create_category` |
| GET | `/material-categories/tree` | Consulta | Retorna categorias en estructura de arbol (con hijos anidados). | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/material_categories.py::get_categories_tree` |
| DELETE | `/material-categories/{category_id}` | Eliminación | Elimina (soft delete) una categoria. | path `category_id` (str) | tabla `material_categories` (delete/update) | JSON con llaves típicas: `message`, `soft_delete`; status usados en errores/respuestas: 404, 500 | `api/routers/material_categories.py::delete_category` |
| GET | `/material-categories/{category_id}` | Consulta | Obtiene una categoria especifica por ID. | path `category_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/material_categories.py::get_category` |
| PATCH | `/material-categories/{category_id}` | Actualización | Actualiza una categoria existente. | path `category_id` (str); body `category` (modelo `CategoryUpdate`) | tabla `material_categories` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/material_categories.py::update_category` |

## Módulo: material-classes
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/material-classes` | Consulta | Lista todas las clases de materiales. | query `include_inactive` (bool) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/material_classes.py::list_classes` |
| POST | `/material-classes` | Creación/Comando | Crea una nueva clase. | body `material_class` (modelo `ClassCreate`) | tabla `material_classes` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/material_classes.py::create_class` |
| DELETE | `/material-classes/{class_id}` | Eliminación | Elimina (soft delete) una clase. | path `class_id` (str) | tabla `material_classes` (delete/update) | JSON con llaves típicas: `message`, `soft_delete`; status usados en errores/respuestas: 404, 500 | `api/routers/material_classes.py::delete_class` |
| GET | `/material-classes/{class_id}` | Consulta | Obtiene una clase especifica por ID. | path `class_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/material_classes.py::get_class` |
| PATCH | `/material-classes/{class_id}` | Actualización | Actualiza una clase existente. | path `class_id` (str); body `material_class` (modelo `ClassUpdate`) | tabla `material_classes` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/material_classes.py::update_class` |

## Módulo: materials
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/materials` | Consulta | Lista materiales con paginacion y filtros. | query `page` (int); query `page_size` (int); query `search` (Optional[str]); query `category_id` (Optional[str]); query `class_id` (Optional[str]); query `vendor_id` (Optional[str]); query `unit_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `pagination`; status usados en errores/respuestas: 500 | `api/routers/materials.py::list_materials` |
| POST | `/materials` | Creación/Comando | Crea un nuevo material. | body `material` (modelo `MaterialCreate`) | tabla `materials` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/materials.py::create_material` |
| POST | `/materials/bulk` | Acción/Comando | Crea multiples materiales en una sola operacion. | body `materials` (modelo `List[MaterialCreate]`) | tabla `materials` (upsert) | JSON con llaves típicas: `count`, `message`; status usados en errores/respuestas: 500 | `api/routers/materials.py::bulk_create_materials` |
| DELETE | `/materials/{material_id}` | Eliminación | Elimina un material. | path `material_id` (str) | tabla `materials` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 409, 500 | `api/routers/materials.py::delete_material` |
| GET | `/materials/{material_id}` | Consulta | Obtiene un material especifico por ID con datos relacionados. | path `material_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ID`, `brand`, `category_id`, `category_name`, `class_id`, `class_name`, `design_package_option`, `full_description`, `image`, `link`, `price`, `price_numeric`; status usados en errores/respuestas: 404, 500 | `api/routers/materials.py::get_material` |
| PATCH | `/materials/{material_id}` | Actualización | Actualiza un material existente (actualizacion parcial). | path `material_id` (str); body `material` (modelo `MaterialUpdate`) | tabla `materials` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/materials.py::update_material` |

## Módulo: messages
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/messages` | Consulta | Get messages for a channel. | query `channel_type` (str); query `channel_id` (Optional[str]); query `project_id` (Optional[str]); query `limit` (int); query `offset` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `messages`; status usados en errores/respuestas: 400, 500 | `api/routers/messages.py::get_messages` |
| POST | `/messages` | Creación/Comando | Create a new message | body `payload` (modelo `MessageCreate`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `message_attachments` (insert); tabla `messages` (insert) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 400, 403, 500 | `api/routers/messages.py::create_message` |
| POST | `/messages/channel/clear` | Acción/Comando | Clear all messages in a channel. CEO and COO only. | body `payload` (modelo `ChannelClearRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `ok`; status usados en errores/respuestas: 400, 403, 500 | `api/routers/messages.py::clear_channel_messages` |
| GET | `/messages/channels` | Consulta | Get all custom and direct message channels for current user | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `channels`; status usados en errores/respuestas: 500 | `api/routers/messages.py::get_channels` |
| POST | `/messages/channels` | Acción/Comando | Create a new custom or direct message channel | body `payload` (modelo `ChannelCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `channel_members` (insert); tabla `channels` (insert) | JSON con llaves típicas: `channel`, `existing`; status usados en errores/respuestas: 400, 500 | `api/routers/messages.py::create_channel` |
| POST | `/messages/mark-read` | Acción/Comando | Mark a channel as read for the current user (upsert last_read_at = now()). | body `payload` (modelo `MarkReadRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `channel_key`, `ok`; status usados en errores/respuestas: 500 | `api/routers/messages.py::mark_channel_read` |
| GET | `/messages/mentions` | Consulta | Get messages where current user was mentioned. | query `unread_only` (bool); query `limit` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `mentions`; status usados en errores/respuestas: 500 | `api/routers/messages.py::get_my_mentions` |
| PATCH | `/messages/mentions/{message_id}/read` | Actualización | Mark a mention as read by message_id | path `message_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`; status usados en errores/respuestas: 500 | `api/routers/messages.py::mark_mention_read` |
| GET | `/messages/search` | Proceso/Automatización | Search messages by content | query `q` (str); query `channel_type` (Optional[str]); query `channel_id` (Optional[str]); query `project_id` (Optional[str]); query `limit` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `messages`; status usados en errores/respuestas: 500 | `api/routers/messages.py::search_messages` |
| GET | `/messages/unread-counts` | Consulta | Get unread message counts for all channels the user has access to. | autenticación/autorización (token o usuario resuelto por dependencia) | RPC `get_unread_counts` | JSON con llaves típicas: `unread_counts` | `api/routers/messages.py::get_unread_counts` |
| PATCH | `/messages/{message_id}/delete` | Actualización | Soft-delete a message. Users can only delete their own messages. | path `message_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`; status usados en errores/respuestas: 403, 404, 500 | `api/routers/messages.py::soft_delete_message` |
| POST | `/messages/{message_id}/reactions` | Acción/Comando | Add or remove a reaction to a message | path `message_id` (str); body `payload` (modelo `ReactionToggle`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `message_reactions` (insert) | JSON con llaves típicas: `reactions`; status usados en errores/respuestas: 500 | `api/routers/messages.py::toggle_reaction` |
| GET | `/messages/{message_id}/thread` | Consulta | Get all replies to a message (thread) | path `message_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `replies`; status usados en errores/respuestas: 500 | `api/routers/messages.py::get_thread_replies` |
| POST | `/messages/{message_id}/thread` | Acción/Comando | Reply to a message in a thread | path `message_id` (str); body `payload` (modelo `ThreadReplyCreate`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `messages` (insert) | JSON con llaves típicas: `reply`; status usados en errores/respuestas: 404, 500 | `api/routers/messages.py::create_thread_reply` |

## Módulo: NGM Board Items
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/board-items/` | Consulta | List items, optionally filtered by folder and/or type. | query `folder_id` (Optional[str]); query `item_type` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `items`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::list_items` |
| POST | `/board-items/` | Creación/Comando | Create a new board, table, or folder. | body `item` (modelo `ItemCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_items` (insert); tabla `ngm_board_table_data` (insert) | status usados en errores/respuestas: 400, 409, 500 | `api/routers/ngm_board_items.py::create_item` |
| GET | `/board-items/all` | Consulta | Get ALL items regardless of folder. Used for initial load. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `items`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::list_all_items` |
| POST | `/board-items/bulk-move` | Acción/Comando | Move multiple items to a folder (or root). | body `req` (modelo `BulkMoveRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_items` (update) | JSON con llaves típicas: `folder_id`, `message`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::bulk_move_items` |
| POST | `/board-items/migrate-from-legacy` | Acción/Comando | One-time migration: reads boards_registry and tables_registry from | autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_items` (insert); tabla `ngm_board_table_data` (upsert) | JSON con llaves típicas: `message`, `migrated`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::migrate_from_legacy` |
| DELETE | `/board-items/{item_id}` | Eliminación | Delete an item. Only the creator can delete. | path `item_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_items` (delete/update) | JSON con llaves típicas: `id`, `message`; status usados en errores/respuestas: 403, 404, 500 | `api/routers/ngm_board_items.py::delete_item` |
| GET | `/board-items/{item_id}` | Consulta | Get a single item by ID. | path `item_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/ngm_board_items.py::get_item` |
| PATCH | `/board-items/{item_id}` | Actualización | Update an item's metadata (name, folder, etc.). | path `item_id` (str); body `update` (modelo `ItemUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_items` (update) | status usados en errores/respuestas: 400, 403, 404, 500 | `api/routers/ngm_board_items.py::update_item` |
| GET | `/board-items/{item_id}/history` | Consulta | Get audit history for an item. | path `item_id` (str); query `limit` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `history`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::get_item_history` |
| GET | `/board-items/{table_id}/data` | Consulta | Get cell data and column headers for a table. | path `table_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `cell_data`, `column_headers`, `table_id`, `updated_at`, `updated_by`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::get_table_data` |
| PUT | `/board-items/{table_id}/data` | Actualización | Update cell data and/or column headers for a table. | path `table_id` (str); body `update` (modelo `TableDataUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `ngm_board_history` (insert); tabla `ngm_board_items` (update); tabla `ngm_board_table_data` (insert/update) | JSON con llaves típicas: `message`, `table_id`, `updated_at`; status usados en errores/respuestas: 500 | `api/routers/ngm_board_items.py::update_table_data` |

## Módulo: NGM Board State
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/process-manager/history/{state_key}` | Consulta | Get history of changes for a specific state key. | path `state_key` (str); query `limit` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `history`, `state_key`; status usados en errores/respuestas: 400, 500 | `api/routers/process_manager.py::get_process_manager_history` |
| POST | `/process-manager/history/{state_key}/restore/{history_id}` | Creación/Comando | Restore a state from a specific history entry. | path `state_key` (str); path `history_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `process_manager_state` (update) | JSON con llaves típicas: `message`, `restored_from`, `state_key`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/process_manager.py::restore_from_history` |
| GET | `/process-manager/state` | Consulta | Get all process manager states. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `states`; status usados en errores/respuestas: 500 | `api/routers/process_manager.py::get_all_process_manager_states` |
| GET | `/process-manager/state/{state_key}` | Consulta | Get a specific state from the process manager shared state. | path `state_key` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `state_data`, `state_key`, `updated_at`, `updated_by`; status usados en errores/respuestas: 400, 500 | `api/routers/process_manager.py::get_process_manager_state` |
| PUT | `/process-manager/state/{state_key}` | Actualización | Update a specific state in the process manager shared state. | path `state_key` (str); body `update` (modelo `ProcessManagerStateUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `process_manager_state` (insert/update) | JSON con llaves típicas: `message`, `state_key`, `updated_at`; status usados en errores/respuestas: 400, 500 | `api/routers/process_manager.py::update_process_manager_state` |

## Módulo: notifications
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/notifications/status` | Consulta | Check if the current user has any active push tokens registered. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `devices`, `has_tokens`, `token_count`; status usados en errores/respuestas: 500 | `api/routers/notifications.py::get_notification_status` |
| POST | `/notifications/test` | Acción/Comando | Send a test push notification to the current user. | body `payload` (modelo `TestNotificationRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `success`, `tokens_count`; status usados en errores/respuestas: 400, 500 | `api/routers/notifications.py::send_test_notification` |
| DELETE | `/notifications/token` | Eliminación | Unregister/deactivate an FCM push token. | body `payload` (modelo `RegisterTokenRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | modelo `TokenResponse`; status usados en errores/respuestas: 500 | `api/routers/notifications.py::unregister_push_token` |
| POST | `/notifications/token` | Acción/Comando | Register or update an FCM push token for the current user. | body `payload` (modelo `RegisterTokenRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | modelo `TokenResponse`; status usados en errores/respuestas: 500 | `api/routers/notifications.py::register_push_token` |

## Módulo: ocr-metrics
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/ocr-metrics/recent` | Consulta | Recent OCR metric entries for detailed inspection. | query `limit` (int); query `agent` (Optional[str]); query `method` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `items` | `api/routers/ocr_metrics.py::get_recent_metrics` |
| GET | `/ocr-metrics/summary` | Consulta | Aggregated OCR metrics summary for the dashboard widget. | query `days` (int); query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `avg_confidence`, `by_agent`, `by_agent_method`, `by_method`, `error_count`, `match_types`, `pdfplumber_count`, `pdfplumber_pct`, `period_days`, `success_rate`, `tax_detected_count`, `total_scans` | `api/routers/ocr_metrics.py::get_ocr_summary` |

## Módulo: payment_methods
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/payment-methods` | Consulta | Lista todos los payment methods ordenados por nombre | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/payment_methods.py::list_payment_methods` |
| POST | `/payment-methods` | Creación/Comando | Crea un nuevo payment method | body `payment_method` (modelo `PaymentMethodCreate`) | tabla `paymet_methods` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/payment_methods.py::create_payment_method` |
| DELETE | `/payment-methods/{payment_method_id}` | Eliminación | Elimina un payment method | path `payment_method_id` (str) | tabla `paymet_methods` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/payment_methods.py::delete_payment_method` |
| GET | `/payment-methods/{payment_method_id}` | Consulta | Obtiene un payment method específico por ID | path `payment_method_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/payment_methods.py::get_payment_method` |
| PATCH | `/payment-methods/{payment_method_id}` | Actualización | Actualiza un payment method existente (actualización parcial) | path `payment_method_id` (str); body `payment_method` (modelo `PaymentMethodUpdate`) | tabla `paymet_methods` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/payment_methods.py::update_payment_method` |

## Módulo: Pending Receipts
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/pending-receipts/agent-config` | Consulta | Return current agent configuration. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `auto_create_expense`, `auto_skip_duplicates`, `min_confidence` | `api/routers/pending_receipts.py::get_agent_config` |
| PATCH | `/pending-receipts/agent-config` | Actualización | Update agent configuration (key-value pairs). | dato `payload` (dict); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `ok`; status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::update_agent_config` |
| DELETE | `/pending-receipts/agent-metrics` | Eliminación | Clear error and duplicate records from Andrew metrics. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `deleted_count`, `message`, `ok`; status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::clear_agent_metrics` |
| GET | `/pending-receipts/agent-stats` | Consulta | Return aggregate stats for the receipt agent. | query `days` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `avg_confidence`, `duplicates_caught`, `error_logs`, `errors`, `expenses_created`, `manual_reviews`, `ocr_failures`, `pending_review`, `period_days`, `success_rate`, `total_processed`; status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::get_agent_stats` |
| POST | `/pending-receipts/agents/{agent_id}/reset-stats` | Acción/Comando | Non-destructive stats reset: stores a timestamp in agent_config. | path `agent_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `agent`, `ok`, `reset_at`; status usados en errores/respuestas: 400, 500 | `api/routers/pending_receipts.py::reset_agent_stats` |
| POST | `/pending-receipts/bill-categories/confirm` | Acción/Comando | Process user confirmation of category changes for bill items. | body `payload` (modelo `EditCategoriesRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expense_status_log` (insert) | JSON con llaves típicas: `errors`, `success`, `updated_count`; status usados en errores/respuestas: 400, 500 | `api/routers/pending_receipts.py::confirm_bill_category_changes` |
| POST | `/pending-receipts/bill-review/edit-categories` | Acción/Comando | Trigger the edit_bill_categories card for a bill from the review card. | body `payload` (modelo `BillReviewActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::bill_review_trigger_edit_categories` |
| POST | `/pending-receipts/bill-review/send-to-review` | Acción/Comando | Bulk set selected expenses to 'review' status via bill review card. | body `payload` (modelo `BillReviewActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expense_status_log` (insert) | JSON con llaves típicas: `errors`, `success`, `updated_count`; status usados en errores/respuestas: 400, 500 | `api/routers/pending_receipts.py::bill_review_send_to_review` |
| POST | `/pending-receipts/bill-review/void-items` | Acción/Comando | Soft-delete selected expenses by setting show_on_reports=false. | body `payload` (modelo `BillReviewActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `errors`, `success`, `voided_count`; status usados en errores/respuestas: 400, 500 | `api/routers/pending_receipts.py::bill_review_void_items` |
| POST | `/pending-receipts/check-stale` | Proceso/Automatización | Trigger stale receipt check. Sends reminders for receipts waiting | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::check_stale_receipts_endpoint` |
| POST | `/pending-receipts/process-from-vault` | Acción/Comando | Bridge vault files to Andrew's receipt processing pipeline. | body `payload` (modelo `VaultBatchRequest`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `pending_receipts` (insert) | JSON con llaves típicas: `batch_id`, `queued`, `results`, `total` | `api/routers/pending_receipts.py::process_from_vault` |
| GET | `/pending-receipts/project/{project_id}` | Consulta | Get pending receipts for a project. | path `project_id` (str); query `status` (Optional[str]); query `unprocessed_only` (bool); query `limit` (int); query `offset` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `counts`, `data`, `pagination`, `success`, `total`; status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::get_project_receipts` |
| GET | `/pending-receipts/unprocessed/count` | Consulta | Get counts of unprocessed receipts across all projects. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_project`, `success`, `total_count`; status usados en errores/respuestas: 500 | `api/routers/pending_receipts.py::get_unprocessed_counts` |
| POST | `/pending-receipts/upload` | Acción/Comando | Upload a receipt file to the pending-expenses bucket. | body `request` (modelo `Request`); archivo multipart `file`; form-data `project_id` (str); form-data `uploaded_by` (str); form-data `message_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `pending_receipts` (insert) | JSON con llaves típicas: `data`, `message`, `success`; status usados en errores/respuestas: 400, 413, 500 | `api/routers/pending_receipts.py::upload_receipt` |
| GET | `/pending-receipts/vault-batch-status` | Consulta | Get processing status for a vault batch. | query `batch_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `batch_id`, `complete`, `receipts`, `statuses`, `total` | `api/routers/pending_receipts.py::vault_batch_status` |
| DELETE | `/pending-receipts/{receipt_id}` | Eliminación | Delete a pending receipt. | path `receipt_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `pending_receipts` (delete) | JSON con llaves típicas: `message`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::delete_receipt` |
| GET | `/pending-receipts/{receipt_id}` | Consulta | Get a single pending receipt by ID | path `receipt_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `success`; status usados en errores/respuestas: 404, 500 | `api/routers/pending_receipts.py::get_receipt` |
| PATCH | `/pending-receipts/{receipt_id}` | Actualización | Update a pending receipt's status or parsed data | path `receipt_id` (str); body `payload` (modelo `PendingReceiptUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `success`; status usados en errores/respuestas: 404, 500 | `api/routers/pending_receipts.py::update_receipt` |
| POST | `/pending-receipts/{receipt_id}/agent-process` | Acción/Comando | Endpoint wrapper for agent processing (provides auth via Depends). | path `receipt_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/pending_receipts.py::agent_process_receipt` |
| POST | `/pending-receipts/{receipt_id}/check-action` | Proceso/Automatización | Handle a user action in the check processing conversation. | path `receipt_id` (str); body `payload` (modelo `CheckActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::check_action` |
| POST | `/pending-receipts/{receipt_id}/create-expense` | Acción/Comando | Create an expense from a pending receipt and link them. | path `receipt_id` (str); body `payload` (modelo `CreateExpenseFromReceiptRequest`); body `background_tasks` (modelo `BackgroundTasks`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `expenses_manual_COGS` (insert) | JSON con llaves típicas: `expense`, `message`, `receipt_id`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::create_expense_from_receipt` |
| POST | `/pending-receipts/{receipt_id}/duplicate-action` | Acción/Comando | Handle a user response in the duplicate confirmation conversation. | path `receipt_id` (str); body `payload` (modelo `DuplicateActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `pending_receipts` (update) | JSON con llaves típicas: `state`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::duplicate_action` |
| POST | `/pending-receipts/{receipt_id}/link` | Acción/Comando | Link an existing pending receipt to an existing expense. | path `receipt_id` (str); body `payload` (modelo `LinkToExpenseRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`, `success`; status usados en errores/respuestas: 400, 404, 409, 500 | `api/routers/pending_receipts.py::link_receipt_to_expense` |
| POST | `/pending-receipts/{receipt_id}/process` | Acción/Comando | Process a pending receipt using the shared OCR service (scan_receipt). | path `receipt_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`, `parsed`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::process_receipt` |
| POST | `/pending-receipts/{receipt_id}/receipt-action` | Acción/Comando | Handle user action in the receipt split conversation. | path `receipt_id` (str); body `payload` (modelo `ReceiptActionRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `categorization_corrections` (insert); tabla `pending_receipts` (update) | JSON con llaves típicas: `clarification_needed`, `error`, `expense_id`, `expense_ids`, `items`, `state`, `still_missing`, `success`, `updated_fields`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pending_receipts.py::receipt_action` |

## Módulo: percentage-items
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/percentage-items` | Consulta | Lista percentage items con filtros opcionales. | query `is_standard` (Optional[bool]); query `applies_to` (Optional[str]); query `include_inactive` (bool) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `total`; status usados en errores/respuestas: 500 | `api/routers/percentage_items.py::list_percentage_items` |
| POST | `/percentage-items` | Creación/Comando | Crea un nuevo percentage item. | body `item` (modelo `PercentageItemCreate`) | tabla `percentage_items` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/percentage_items.py::create_percentage_item` |
| GET | `/percentage-items/standard` | Consulta | Lista solo los items estandar (is_standard=true, is_active=true). | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/percentage_items.py::list_standard_percentage_items` |
| DELETE | `/percentage-items/{item_id}` | Eliminación | Elimina un percentage item. Los standard items se desactivan en vez de eliminarse. | path `item_id` (str) | tabla `percentage_items` (delete/update) | JSON con llaves típicas: `message`, `soft_delete`; status usados en errores/respuestas: 404, 500 | `api/routers/percentage_items.py::delete_percentage_item` |
| GET | `/percentage-items/{item_id}` | Consulta | Obtiene un percentage item especifico. | path `item_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/percentage_items.py::get_percentage_item` |
| PATCH | `/percentage-items/{item_id}` | Actualización | Actualiza un percentage item. | path `item_id` (str); body `item` (modelo `PercentageItemUpdate`) | tabla `percentage_items` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/percentage_items.py::update_percentage_item` |

## Módulo: permissions
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/permissions/batch-update` | Acción/Comando | Actualiza múltiples permisos en batch. | body `data` (modelo `BatchPermissionUpdate`) | tabla `role_permissions` (upsert) | JSON con llaves típicas: `details`, `failed_updates`, `message`, `protected_blocked`, `successful_updates`; status usados en errores/respuestas: 500 | `api/routers/permissions.py::batch_update_permissions` |
| GET | `/permissions/check` | Proceso/Automatización | Verifica si un usuario tiene un permiso específico para un módulo. | query `user_id` (str); query `module_key` (str); query `action` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `action`, `has_permission`, `module_key`, `permissions`, `reason`, `user_id`; status usados en errores/respuestas: 404, 500 | `api/routers/permissions.py::check_permission` |
| GET | `/permissions/modules` | Consulta | Lista todos los módulos únicos disponibles en el sistema | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/permissions.py::list_all_modules` |
| GET | `/permissions/role/{rol_id}` | Consulta | Obtiene todos los permisos para un rol específico | path `rol_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `permissions`, `rol_id`; status usados en errores/respuestas: 500 | `api/routers/permissions.py::get_permissions_by_role` |
| GET | `/permissions/roles` | Consulta | Lista todos los roles disponibles | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/permissions.py::list_all_roles` |
| GET | `/permissions/user/{user_id}` | Consulta | Obtiene los permisos de un usuario basado en su rol. | path `user_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `permissions`, `rol_id`, `user_id`; status usados en errores/respuestas: 404, 500 | `api/routers/permissions.py::get_permissions_by_user` |

## Módulo: pipeline
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/pipeline/attachments/bucket-info` | Consulta | Get information about the task-attachments bucket. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `bucket`, `message`, `success` | `api/routers/pipeline.py::get_attachments_bucket_info` |
| GET | `/pipeline/attachments/init` | Consulta | Initialize the task-attachments bucket. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `bucket`, `message`, `success`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::init_attachments_bucket` |
| GET | `/pipeline/automations/overrides` | Consulta | Get automation owner overrides. | query `automation_type` (Optional[str]); query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `overrides`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_automation_overrides` |
| POST | `/pipeline/automations/overrides` | Acción/Comando | Create or update an automation owner override. | body `payload` (modelo `AutomationOverrideCreate`) | tabla `automation_owner_overrides` (insert/update) | JSON con llaves típicas: `action`, `override`, `success`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::create_or_update_automation_override` |
| DELETE | `/pipeline/automations/overrides/{override_id}` | Eliminación | Delete an automation owner override. | path `override_id` (str) | tabla `automation_owner_overrides` (delete) | JSON con llaves típicas: `message`, `success`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::delete_automation_override` |
| POST | `/pipeline/automations/run` | Proceso/Automatización | Ejecuta las automatizaciones seleccionadas y crea/actualiza tareas. | body `payload` (modelo `AutomationsRunRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `errors`, `success`, `tasks_created`, `tasks_updated`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::run_automations` |
| GET | `/pipeline/automations/settings` | Consulta | Get all automation settings with their current configuration. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `settings`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_automation_settings` |
| PUT | `/pipeline/automations/settings/{automation_type}` | Actualización | Update settings for a specific automation type. | path `automation_type` (str); body `payload` (modelo `AutomationSettingsUpdate`) | tabla `automation_settings` (update) | JSON con llaves típicas: `message`, `settings`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pipeline.py::update_automation_settings` |
| GET | `/pipeline/automations/status` | Consulta | Devuelve el estado actual de las automatizaciones. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `overdue_tasks`, `pending_expenses_auth`, `pending_invoices`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_automations_status` |
| GET | `/pipeline/automations/tasks` | Consulta | Get all automated tasks grouped by automation type (clusters). | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `clusters`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_automation_tasks_grouped` |
| GET | `/pipeline/companies` | Consulta | Devuelve lista de empresas para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_companies` |
| GET | `/pipeline/dependencies` | Consulta | Returns task dependencies for the Operation Manager timeline. | query `project_id` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `dependencies`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_all_dependencies` |
| GET | `/pipeline/grouped` | Consulta | Devuelve las tareas agrupadas por task_status. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `groups`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_grouped` |
| GET | `/pipeline/my-work/team-overview` | Consulta | Devuelve resumen de carga de trabajo de todo el equipo. | query `hours_per_day` (float); query `days_per_week` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `settings`, `team`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_team_workload_overview` |
| GET | `/pipeline/my-work/{user_id}` | Consulta | Devuelve las tareas del usuario para la página My Work con cálculo de workload. | path `user_id` (str); query `hours_per_day` (float); query `days_per_week` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `task_types`, `tasks`, `workload`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_my_work_data` |
| GET | `/pipeline/projects` | Consulta | Devuelve lista de proyectos para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_projects` |
| GET | `/pipeline/task-departments` | Consulta | Devuelve lista de departamentos para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_task_departments` |
| GET | `/pipeline/task-priorities` | Consulta | Devuelve lista de prioridades para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_task_priorities` |
| GET | `/pipeline/task-types` | Consulta | Devuelve lista de tipos de tarea para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_task_types` |
| POST | `/pipeline/tasks` | Acción/Comando | Crea una nueva tarea en el pipeline. | body `payload` (modelo `TaskCreate`) | tabla `tasks` (insert) | JSON con llaves típicas: `message`, `task`; status usados en errores/respuestas: 400, 500 | `api/routers/pipeline.py::create_task` |
| GET | `/pipeline/tasks/my-tasks/{user_id}` | Consulta | Devuelve las tareas asignadas a un usuario para mostrar en su Dashboard. | path `user_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `tasks`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_my_tasks` |
| GET | `/pipeline/tasks/pending-reviews/{user_id}` | Consulta | Get all tasks pending review by a specific manager. | path `user_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `success`, `tasks`, `total` | `api/routers/pipeline.py::get_pending_reviews` |
| DELETE | `/pipeline/tasks/{task_id}` | Eliminación | Elimina una tarea del pipeline. | path `task_id` (str) | tabla `tasks` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::delete_task` |
| PATCH | `/pipeline/tasks/{task_id}` | Actualización | Actualiza campos individuales de una tarea. | path `task_id` (str); body `payload` (modelo `TaskUpdate`) | tabla `tasks` (update) | JSON con llaves típicas: `message`, `task`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pipeline.py::patch_task` |
| POST | `/pipeline/tasks/{task_id}/approve` | Acción/Comando | Manager approves a task: | path `task_id` (str); body `payload` (modelo `TaskApproveRequest`) | tabla `tasks` (update) | JSON con llaves típicas: `message`, `new_status`, `review_task_completed`, `success`, `task`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::approve_task` |
| POST | `/pipeline/tasks/{task_id}/convert-to-coordination` | Acción/Comando | Convert an approved task (Good to Go) to a coordination task. | path `task_id` (str) | tabla `tasks` (insert/update) | JSON con llaves típicas: `coordination_task`, `message`, `original_task_id`, `success`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pipeline.py::convert_to_coordination` |
| POST | `/pipeline/tasks/{task_id}/reject` | Acción/Comando | Manager rejects a task: | path `task_id` (str); body `payload` (modelo `TaskRejectRequest`) | tabla `tasks` (update) | JSON con llaves típicas: `message`, `new_status`, `rejection_count`, `success`, `task`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::reject_task` |
| POST | `/pipeline/tasks/{task_id}/send-to-review` | Acción/Comando | Envía una tarea a revisión: | path `task_id` (str); body `payload` (modelo `SendToReviewRequest`) | tabla `tasks` (update) | JSON con llaves típicas: `elapsed_time`, `new_status`, `reviewer_task_created`, `reviewer_task_id`, `success`, `task`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::send_task_to_review` |
| POST | `/pipeline/tasks/{task_id}/start` | Acción/Comando | Inicia una tarea: cambia el status a "Working on It" y registra time_start. | path `task_id` (str) | tabla `tasks` (update) | JSON con llaves típicas: `new_status`, `status_changed`, `success`, `task`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::start_task` |
| GET | `/pipeline/tasks/{task_id}/workflow-history` | Consulta | Get the complete workflow history/audit log for a task. | path `task_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `history`, `success`, `task`, `task_id`, `total_events`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::get_task_workflow_history` |
| GET | `/pipeline/users` | Consulta | Devuelve lista de usuarios para dropdowns en Pipeline UI. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_pipeline_users` |
| PUT | `/pipeline/workload/capacity/{user_id}` | Actualización | Update user capacity settings. | path `user_id` (str); body `data` (modelo `UserCapacityUpdate`) | tabla `user_capacity_settings` (insert/update) | JSON con llaves típicas: `data`, `message`, `success`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::update_user_capacity` |
| GET | `/pipeline/workload/next-available/{user_id}` | Consulta | Get the next available time slot for a user. | path `user_id` (str); query `estimated_hours` (float) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `available_start_date`, `current_pending_hours`, `days_until_available`, `effective_hours_per_day`, `estimated_end_date`, `estimated_hours_requested`, `user_id`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_next_available_slot` |
| POST | `/pipeline/workload/recalculate/{user_id}` | Proceso/Automatización | Recalculate all scheduled dates for a user's tasks. | path `user_id` (str) | tabla `tasks` (update) | JSON con llaves típicas: `schedule_ends`, `success`, `tasks_scheduled`, `user_id`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::recalculate_user_schedule` |
| POST | `/pipeline/workload/schedule-task` | Acción/Comando | Schedule a task based on owner's workload. | body `data` (modelo `ScheduleTaskRequest`) | tabla `task_dependencies` (insert); tabla `tasks` (update) | JSON con llaves típicas: `blocking_task_id`, `days_until_start`, `dependency_created`, `queue_position`, `scheduled_end_date`, `scheduled_start_date`, `success`, `task_id`, `total_pending_hours_before`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/pipeline.py::schedule_task` |
| GET | `/pipeline/workload/team` | Consulta | Get workload overview for all team members. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `team`; status usados en errores/respuestas: 500 | `api/routers/pipeline.py::get_team_workload` |
| GET | `/pipeline/workload/user/{user_id}` | Consulta | Get detailed workload information for a user including: | path `user_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `capacity`, `current_task`, `task_queue`, `user`, `workload`; status usados en errores/respuestas: 404, 500 | `api/routers/pipeline.py::get_user_workload` |

## Módulo: Process Manager
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/processes` | Consulta | Get all processes (both implemented from code and drafts from database). | query `include_implemented` (bool); query `include_drafts` (bool); query `category` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `draft_count`, `implemented_count`, `processes`, `total` | `api/routers/processes.py::get_all_processes` |
| GET | `/processes/categories/list` | Consulta | Get list of available process categories. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `categories` | `api/routers/processes.py::get_process_categories` |
| POST | `/processes/drafts` | Acción/Comando | Create a new draft process. | body `process` (modelo `ProcessCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `process_drafts` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/processes.py::create_draft_process` |
| DELETE | `/processes/drafts/{process_id}` | Eliminación | Delete a draft process. | path `process_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `process_drafts` (delete) | JSON con llaves típicas: `deleted_id`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/processes.py::delete_draft_process` |
| PATCH | `/processes/drafts/{process_id}` | Actualización | Update a draft process. | path `process_id` (str); body `update` (modelo `ProcessUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `process_drafts` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/processes.py::update_draft_process` |
| GET | `/processes/step-types/list` | Consulta | Get list of available step types. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `step_types` | `api/routers/processes.py::get_step_types` |
| GET | `/processes/triggers/list` | Consulta | Get list of available trigger types. | autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `triggers` | `api/routers/processes.py::get_trigger_types` |
| GET | `/processes/{process_id}` | Consulta | Get a single process by ID. | path `process_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `category`, `created_at`, `description`, `id`, `is_implemented`, `name`, `owner`, `position`, `status`, `steps`, `trigger`, `updated_at`; status usados en errores/respuestas: 404 | `api/routers/processes.py::get_process` |

## Módulo: Projects
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/projects` | Consulta | Devuelve lista de proyectos desde 'projects', | query `limit` (int) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/projects.py::list_projects` |
| POST | `/projects/` | Creación/Comando | Create project | body `payload` (modelo `ProjectCreate`) | tabla `projects` (insert) | JSON con llaves típicas: `message`, `project`; status usados en errores/respuestas: 400, 500 | `api/routers/projects.py::create_project` |
| GET | `/projects/meta` | Consulta | Devuelve catálogos básicos para la UI de Projects: | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `clients`, `companies`, `statuses`; status usados en errores/respuestas: 500 | `api/routers/projects.py::get_projects_meta` |
| DELETE | `/projects/{project_id}` | Eliminación | Elimina un proyecto por su UUID. | path `project_id` (str) | tabla `projects` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/projects.py::delete_project` |
| GET | `/projects/{project_id}` | Consulta | Devuelve un proyecto por ID (project_id), | path `project_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 404, 500 | `api/routers/projects.py::get_project` |
| PATCH | `/projects/{project_id}` | Actualización | Actualiza un proyecto existente. | path `project_id` (str); body `payload` (modelo `ProjectUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`, `project`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/projects.py::update_project` |

## Módulo: QBO Integration
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/qbo/accounts` | Integración externa | Obtiene cuentas de QBO sincronizadas. | query `account_type` (Optional[str]); query `is_cogs` (Optional[bool]); query `is_income` (Optional[bool]); query `is_expense` (Optional[bool]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_qbo_accounts` |
| POST | `/qbo/accounts/sync/{realm_id}` | Integración externa | Sincroniza todas las cuentas desde QBO a la tabla qbo_accounts. | path `realm_id` (str) | tabla `qbo_accounts` (upsert) | JSON con llaves típicas: `cogs_accounts`, `expense_accounts`, `income_accounts`, `message`, `realm_id`, `total_synced`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::sync_qbo_accounts` |
| GET | `/qbo/auth` | Autenticación | Returns the QuickBooks OAuth2 authorization URL. | query `state` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `authorization_url`, `message`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::qbo_auth` |
| POST | `/qbo/bills/extract-doc-numbers/{realm_id}` | Integración externa | One-time endpoint: fetches all Bills from QBO API and extracts DocNumber | path `realm_id` (str); dato `project` (Optional[str]); dato `qbo_customer_id` (Optional[str]) | tabla `qbo_bill_doc_mapping` (upsert) | JSON con llaves típicas: `bills`, `bills_for_project`, `message`, `total_bills_in_qbo`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::extract_bill_doc_numbers` |
| GET | `/qbo/budgets/mapping` | Integración externa | Get all QBO budget -> NGM project mappings. | query `unmapped_only` (Optional[bool]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_budget_mappings` |
| POST | `/qbo/budgets/mapping` | Integración externa | Create a new QBO budget -> NGM project mapping. | body `payload` (modelo `BudgetMappingCreate`) | tabla `qbo_budget_mapping` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/qbo.py::create_budget_mapping` |
| POST | `/qbo/budgets/mapping/auto-match` | Integración externa | Attempt to automatically match QBO budgets to NGM projects by name similarity. | dato `realm_id` (str) | tabla `qbo_budget_mapping` (insert) | JSON con llaves típicas: `matched`, `message`, `new_mappings_created`, `total_budgets`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::auto_match_budgets` |
| DELETE | `/qbo/budgets/mapping/{qbo_budget_id}` | Integración externa | Delete a budget mapping. | path `qbo_budget_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::delete_budget_mapping` |
| PUT | `/qbo/budgets/mapping/{qbo_budget_id}` | Integración externa | Update an existing budget mapping. | path `qbo_budget_id` (str); body `payload` (modelo `BudgetMappingUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/qbo.py::update_budget_mapping` |
| GET | `/qbo/budgets/preview/{realm_id}` | Integración externa | Preview available budgets from QuickBooks without importing. | path `realm_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`, `mapped_count`, `message`, `unmapped_count`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::preview_qbo_budgets` |
| POST | `/qbo/budgets/sync-mapped/{realm_id}` | Integración externa | Sync budgets from QBO using the established mappings. | path `realm_id` (str) | tabla `budgets_qbo` (insert) | JSON con llaves típicas: `budgets_synced`, `message`, `projects_updated`, `total_imported`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::sync_mapped_budgets` |
| POST | `/qbo/budgets/sync/{realm_id}` | Integración externa | Syncs budgets from QuickBooks to the local database. | path `realm_id` (str); dato `project_id` (str); dato `fiscal_year` (Optional[str]) | tabla `budgets_qbo` (insert) | JSON con llaves típicas: `accounts_processed`, `message`, `project_id`, `qbo_budgets_found`, `realm_id`, `total_imported`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::qbo_sync_budgets` |
| GET | `/qbo/callback` | Integración externa | OAuth2 callback endpoint. | query `code` (str); query `realmId` (str); query `state` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/qbo.py::qbo_callback` |
| POST | `/qbo/disconnect/{realm_id}` | Integración externa | Disconnects a QuickBooks company by removing its tokens. | path `realm_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::qbo_disconnect` |
| GET | `/qbo/expenses` | Integración externa | Obtiene gastos de QBO. | query `project` (Optional[str]); query `qbo_customer_id` (Optional[str]); query `is_cogs` (Optional[bool]); query `bucket` (Optional[str]); query `limit` (Optional[int]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_qbo_expenses` |
| DELETE | `/qbo/expenses/clear` | Integración externa | Elimina gastos de QBO. | dato `qbo_customer_id` (Optional[str]) | tabla `qbo_expenses` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::clear_qbo_expenses` |
| POST | `/qbo/expenses/import` | Integración externa | Importa gastos de QBO a la base de datos. | body `payload` (modelo `QBOImportPayload`) | tabla `qbo_expenses` (delete); tabla `qbo_project_mapping` (insert) | JSON con llaves típicas: `message`, `new_mappings_created`, `total_processed`, `unique_projects`; status usados en errores/respuestas: 400, 500 | `api/routers/qbo.py::import_qbo_expenses` |
| GET | `/qbo/expenses/stats` | Integración externa | Obtiene estadísticas de los gastos QBO importados. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `by_bucket`, `cogs_expenses`, `non_cogs_expenses`, `total_expenses`, `unique_qbo_projects`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_qbo_stats` |
| GET | `/qbo/mapping` | Integración externa | Obtiene todos los mapeos QBO customer -> NGM project. | query `unmapped_only` (Optional[bool]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_project_mappings` |
| POST | `/qbo/mapping` | Integración externa | Crea un nuevo mapeo QBO customer -> NGM project. | body `payload` (modelo `ProjectMappingCreate`) | tabla `qbo_project_mapping` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/qbo.py::create_project_mapping` |
| POST | `/qbo/mapping/auto-match` | Integración externa | Intenta hacer match automático entre QBO customers y NGM projects | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `matched`, `message`, `remaining_unmapped`, `total_unmapped`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::auto_match_projects` |
| DELETE | `/qbo/mapping/{qbo_customer_id}` | Integración externa | Elimina un mapeo. | path `qbo_customer_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::delete_project_mapping` |
| PUT | `/qbo/mapping/{qbo_customer_id}` | Integración externa | Actualiza un mapeo existente. | path `qbo_customer_id` (str); body `payload` (modelo `ProjectMappingUpdate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/qbo.py::update_project_mapping` |
| GET | `/qbo/pnl` | Integración externa | Obtiene datos para el reporte P&L de un proyecto. | query `project` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `cogs`, `gross_margin`, `gross_profit`, `message`, `net_income`, `net_margin`, `operating_expenses`, `project_id`, `revenue`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_pnl_data` |
| GET | `/qbo/revenue` | Integración externa | Obtiene ingresos (revenue) de QBO. | query `project` (Optional[str]); query `qbo_customer_id` (Optional[str]); query `limit` (Optional[int]) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::get_qbo_revenue` |
| POST | `/qbo/revenue/sync/{realm_id}` | Integración externa | Sincroniza ingresos (Invoices y SalesReceipts) desde QBO. | path `realm_id` (str); dato `start_date` (Optional[str]); dato `end_date` (Optional[str]) | tabla `qbo_expenses` (upsert) | JSON con llaves típicas: `message`, `realm_id`, `total_imported`, `transactions`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::sync_qbo_revenue` |
| GET | `/qbo/status` | Integración externa | Returns the current QuickBooks connection status. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/qbo.py::qbo_status` |
| POST | `/qbo/sync/{realm_id}` | Integración externa | Syncs expenses from QuickBooks to the local database. | path `realm_id` (str); dato `start_date` (Optional[str]); dato `end_date` (Optional[str]); dato `replace_all` (Optional[bool]) | tabla `qbo_expenses` (delete/upsert); tabla `qbo_project_mapping` (insert) | JSON con llaves típicas: `message`, `new_project_mappings`, `realm_id`, `total_imported`, `total_raw_lines`, `transactions`; status usados en errores/respuestas: 500 | `api/routers/qbo.py::qbo_sync_expenses` |

## Módulo: Reconciliations
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/expenses/reconciliations` | Consulta | Obtiene todas las reconciliaciones de un proyecto. | query `project` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 500 | `api/routers/reconciliations.py::get_reconciliations` |
| POST | `/expenses/reconciliations` | Acción/Comando | Crea múltiples reconciliaciones. | body `payload` (modelo `ReconciliationCreate`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `count`, `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/reconciliations.py::create_reconciliations` |
| DELETE | `/expenses/reconciliations/by-manual/{manual_expense_id}` | Eliminación | Elimina la reconciliación de un gasto manual específico | path `manual_expense_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 500 | `api/routers/reconciliations.py::delete_reconciliation_by_manual` |
| DELETE | `/expenses/reconciliations/by-qbo/{qbo_expense_id}` | Integración externa | Elimina todas las reconciliaciones de una factura QBO específica | path `qbo_expense_id` (str); dato `project` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 400, 500 | `api/routers/reconciliations.py::delete_reconciliations_by_qbo` |
| GET | `/expenses/reconciliations/summary` | Consulta | Obtiene un resumen de reconciliaciones para un proyecto: | query `project` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `reconciliation_count`, `total_manual_reconciled`, `total_matched_amount`, `total_qbo_reconciled`; status usados en errores/respuestas: 400, 500 | `api/routers/reconciliations.py::get_reconciliation_summary` |
| DELETE | `/expenses/reconciliations/{reconciliation_id}` | Eliminación | Elimina una reconciliación específica por su ID | path `reconciliation_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/reconciliations.py::delete_reconciliation` |

## Módulo: Reporting
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/reports/pnl` | Acción/Comando | Generate a P&L COGS PDF report and save it to the project's | body `body` (modelo `PnlReportRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `accounts_count`, `ok`, `pdf_url`, `project_name`, `total_cogs`; status usados en errores/respuestas: 404, 500, 503 | `api/routers/reporting.py::generate_pnl_report` |

## Módulo: revit
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/revit/definitions` | Consulta | List all available definition types with metadata. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/revit.py::list_definitions` |
| GET | `/revit/definitions/{def_type}` | Consulta | Get a specific definition file content. | path `def_type` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400 | `api/routers/revit.py::get_definition` |
| GET | `/revit/manifest-schema` | Consulta | Get the build manifest JSON schema. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/revit.py::get_manifest_schema` |
| GET | `/revit/manifests` | Consulta | List saved manifests, optionally filtered by project. | query `project_id` (Optional[str]); query `limit` (int) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/revit.py::list_manifests` |
| POST | `/revit/manifests` | Acción/Comando | Save a generated build manifest to DB. | body `body` (modelo `ManifestCreate`) | tabla `build_manifests` (insert) | status usados en errores/respuestas: 500 | `api/routers/revit.py::create_manifest` |
| DELETE | `/revit/manifests/{manifest_id}` | Eliminación | Delete a saved manifest. | path `manifest_id` (str) | tabla `build_manifests` (delete) | JSON con llaves típicas: `deleted`; status usados en errores/respuestas: 500 | `api/routers/revit.py::delete_manifest` |
| GET | `/revit/manifests/{manifest_id}` | Consulta | Get a specific manifest by ID. | path `manifest_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/revit.py::get_manifest` |
| PATCH | `/revit/manifests/{manifest_id}` | Actualización | Update a saved manifest. | path `manifest_id` (str); body `body` (modelo `ManifestUpdate`) | tabla `build_manifests` (update) | status usados en errores/respuestas: 400, 404, 500 | `api/routers/revit.py::update_manifest` |
| GET | `/revit/materials-map` | Consulta | Get the Revit <-> NGM material mapping. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/revit.py::get_materials_map` |
| GET | `/revit/registry` | Consulta | Full ecosystem registry: scripts, families, definitions index. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/revit.py::get_registry` |
| GET | `/revit/templates` | Consulta | List available project templates. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | Respuesta JSON/HTTP según lógica del handler. | `api/routers/revit.py::list_templates` |
| GET | `/revit/templates/{project_type}` | Consulta | Get a specific project template. | path `project_type` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400 | `api/routers/revit.py::get_template` |

## Módulo: Revit OCR
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/revit/ocr/analyze` | Proceso/Automatización | Unified floor plan analysis. Single GPT-4o Vision call that returns | archivo multipart `file`; form-data `layers` (str); form-data `context` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`, `warning`; status usados en errores/respuestas: 400, 500 | `api/routers/revit_ocr.py::analyze_floorplan` |

## Módulo: schema
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/schema` | Consulta | Endpoint básico para consultar el schema: | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/schema.py::get_schema` |

## Módulo: stripe
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| POST | `/stripe/create-checkout-session` | Integración externa | Create a Stripe Checkout Session (redirect mode). | body `req` (modelo `CheckoutRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `session_id`, `url`; status usados en errores/respuestas: 400, 500, 502 | `api/routers/stripe_payments.py::create_checkout_session` |
| POST | `/stripe/create-plan-checkout` | Integración externa | Create a Stripe Checkout Session for a pricing plan purchase. | body `req` (modelo `PlanCheckoutRequest`) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `session_id`, `url`; status usados en errores/respuestas: 400, 500, 502 | `api/routers/stripe_payments.py::create_plan_checkout` |
| GET | `/stripe/session-status/{session_id}` | Integración externa | Check the payment status of a Checkout Session. | path `session_id` (str) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `amount_total`, `customer_email`, `invoice_id`, `status`; status usados en errores/respuestas: 500, 502 | `api/routers/stripe_payments.py::get_session_status` |

## Módulo: Takeoff
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/takeoff/measurements` | Consulta | List measurements for a plan, optionally filtered by page. | query `plan_id` (str); query `page_number` (Optional[int]); dependency `user` | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/takeoff.py::list_measurements` |
| POST | `/takeoff/measurements` | Acción/Comando | Create a measurement annotation. | body `body` (modelo `MeasurementCreate`); dependency `user` | tabla `takeoff_measurements` (insert) | status usados en errores/respuestas: 500 | `api/routers/takeoff.py::create_measurement` |
| DELETE | `/takeoff/measurements/{meas_id}` | Eliminación | Delete a measurement. | path `meas_id` (str); dependency `user` | tabla `takeoff_measurements` (delete) | JSON con llaves típicas: `success`; status usados en errores/respuestas: 500 | `api/routers/takeoff.py::delete_measurement` |
| PATCH | `/takeoff/measurements/{meas_id}` | Actualización | Update measurement label or value. | path `meas_id` (str); body `body` (modelo `MeasurementUpdate`); dependency `user` | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 404, 500 | `api/routers/takeoff.py::update_measurement` |
| GET | `/takeoff/plans` | Consulta | List all plans, optionally filtered by project. | query `project_id` (Optional[str]); dependency `user` | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/takeoff.py::list_plans` |
| POST | `/takeoff/plans` | Acción/Comando | Create a plan record with its pages. | body `body` (modelo `PlanCreate`); dependency `user` | tabla `takeoff_plan_pages` (insert); tabla `takeoff_plans` (insert) | status usados en errores/respuestas: 500 | `api/routers/takeoff.py::create_plan` |
| DELETE | `/takeoff/plans/{plan_id}` | Eliminación | Delete a plan and all its pages/measurements (cascades). | path `plan_id` (str); dependency `user` | tabla `takeoff_plans` (delete); bucket `takeoff-plans` (remove) | JSON con llaves típicas: `success`; status usados en errores/respuestas: 500 | `api/routers/takeoff.py::delete_plan` |
| GET | `/takeoff/plans/{plan_id}` | Consulta | Get a single plan with pages and measurements. | path `plan_id` (str); dependency `user` | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/takeoff.py::get_plan` |
| PATCH | `/takeoff/plans/{plan_id}/pages/{page_number}/calibration` | Actualización | Save or update calibration data for a plan page. | path `plan_id` (str); path `page_number` (int); body `body` (modelo `CalibrationUpdate`); dependency `user` | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/takeoff.py::update_calibration` |

## Módulo: team
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/team/meta` | Consulta | Para poblar dropdowns en el frontend. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `departments`, `roles`, `seniorities`, `statuses`; status usados en errores/respuestas: 500 | `api/routers/team.py::team_meta` |
| GET | `/team/rols` | Consulta | Lista roles para administración. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/team.py::list_roles` |
| POST | `/team/rols` | Acción/Comando | Crea un nuevo rol. | body `payload` (modelo `RoleCreate`) | tabla `rols` (insert) | JSON con llaves típicas: `id`, `name`; status usados en errores/respuestas: 409, 422, 500 | `api/routers/team.py::create_role` |
| DELETE | `/team/rols/{rol_id}` | Eliminación | Borra un rol. Si hay users apuntando a este rol, la FK puede impedir el borrado. | path `rol_id` (str) | tabla `rols` (delete) | JSON con llaves típicas: `deleted_role_id`, `ok`; status usados en errores/respuestas: 404, 500 | `api/routers/team.py::delete_role` |
| PATCH | `/team/rols/{rol_id}` | Actualización | Renombra un rol existente. | path `rol_id` (str); body `payload` (modelo `RoleUpdate`) | tabla `rols` (update) | JSON con llaves típicas: `id`, `name`; status usados en errores/respuestas: 404, 409, 422, 500 | `api/routers/team.py::update_role` |
| GET | `/team/users` | Consulta | List team users | query `q` (Optional[str]) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/team.py::list_team_users` |
| POST | `/team/users` | Autenticación | Create user | body `payload` (modelo `UserCreate`) | tabla `users` (insert) | status usados en errores/respuestas: 500 | `api/routers/team.py::create_user` |
| DELETE | `/team/users/{user_id}` | Eliminación | Delete user | path `user_id` (str) | tabla `users` (delete) | JSON con llaves típicas: `deleted_user_id`, `ok`; status usados en errores/respuestas: 404, 500 | `api/routers/team.py::delete_user` |
| PATCH | `/team/users/{user_id}` | Actualización | Update user | path `user_id` (str); body `payload` (modelo `UserUpdate`) | tabla `users` (update) | status usados en errores/respuestas: 404, 500 | `api/routers/team.py::update_user` |

## Módulo: Timeline
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| DELETE | `/timeline/dependencies/{dependency_id}` | Eliminación | Delete a phase dependency. | path `dependency_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/timeline.py::delete_dependency` |
| DELETE | `/timeline/milestones/{milestone_id}` | Eliminación | Delete a milestone. | path `milestone_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/timeline.py::delete_milestone` |
| PATCH | `/timeline/milestones/{milestone_id}` | Actualización | Update milestone fields (only provided fields are changed). | path `milestone_id` (str); body `payload` (modelo `MilestoneUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/timeline.py::update_milestone` |
| DELETE | `/timeline/phases/{phase_id}` | Eliminación | Delete a phase (milestones referencing it will have phase_id set to NULL). | path `phase_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `message`; status usados en errores/respuestas: 404, 500 | `api/routers/timeline.py::delete_phase` |
| PATCH | `/timeline/phases/{phase_id}` | Actualización | Update phase fields (only provided fields are changed). | path `phase_id` (str); body `payload` (modelo `PhaseUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/timeline.py::update_phase` |
| GET | `/timeline/projects/{project_id}/dependencies` | Consulta | List all phase dependencies for a project. | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/timeline.py::list_dependencies` |
| POST | `/timeline/projects/{project_id}/dependencies` | Acción/Comando | Create a phase dependency with cycle detection. | path `project_id` (str); body `payload` (modelo `DependencyCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `phase_dependencies` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 409, 500 | `api/routers/timeline.py::create_dependency` |
| POST | `/timeline/projects/{project_id}/import` | Proceso/Automatización | Import phases and milestones from a Microsoft Project export file. | path `project_id` (str); archivo multipart `file`; query `mode` (str); autenticación/autorización (token o usuario resuelto por dependencia) | tabla `phase_dependencies` (delete); tabla `project_milestones` (delete/insert); tabla `project_phases` (delete/insert/update) | JSON con llaves típicas: `message`, `milestones_imported`, `phases_imported`; status usados en errores/respuestas: 400, 500 | `api/routers/timeline.py::import_timeline` |
| GET | `/timeline/projects/{project_id}/milestones` | Consulta | List all milestones for a project, ordered by due_date. | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/timeline.py::list_milestones` |
| POST | `/timeline/projects/{project_id}/milestones` | Acción/Comando | Create a new milestone for a project. | path `project_id` (str); body `payload` (modelo `MilestoneCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 500 | `api/routers/timeline.py::create_milestone` |
| GET | `/timeline/projects/{project_id}/phases` | Consulta | List all phases for a project, ordered by sort_order then phase_order. | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/timeline.py::list_phases` |
| POST | `/timeline/projects/{project_id}/phases` | Acción/Comando | Create a new phase for a project. | path `project_id` (str); body `payload` (modelo `PhaseCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 500 | `api/routers/timeline.py::create_phase` |
| PATCH | `/timeline/projects/{project_id}/reorder` | Actualización | Batch update sort_order and parent_phase_id for phases. | path `project_id` (str); body `payload` (modelo `ReorderPayload`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 500 | `api/routers/timeline.py::reorder_phases` |
| GET | `/timeline/projects/{project_id}/summary` | Consulta | Lightweight summary for the Projects tab: | path `project_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `completed_milestones`, `completed_phases`, `in_progress_phases`, `overall_progress_pct`, `overdue_milestones`, `phases`, `total_milestones`, `total_phases`, `upcoming_milestones`; status usados en errores/respuestas: 500 | `api/routers/timeline.py::project_timeline_summary` |

## Módulo: units
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/units` | Consulta | Lista todas las unidades de medida. | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/units.py::list_units` |
| POST | `/units` | Creación/Comando | Crea una nueva unidad de medida. | body `unit` (modelo `UnitCreate`) | tabla `units` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/units.py::create_unit` |
| DELETE | `/units/{unit_id}` | Eliminación | Elimina una unidad de medida. | path `unit_id` (str) | tabla `units` (delete) | JSON con llaves típicas: `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/units.py::delete_unit` |
| GET | `/units/{unit_id}` | Consulta | Obtiene una unidad especifica por ID. | path `unit_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/units.py::get_unit` |

## Módulo: Vault
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/vault/breadcrumb/{folder_id}` | Consulta | Get breadcrumb path from root to folder_id (inclusive). | path `folder_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_get_breadcrumb` |
| POST | `/vault/duplicate/{file_id}` | Creación/Comando | Duplicate a file. | path `file_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 500 | `api/routers/vault.py::api_duplicate_file` |
| GET | `/vault/duplicates` | Consulta | Find files with the same hash. | query `file_hash` (str); query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_detect_duplicates` |
| GET | `/vault/files` | Consulta | List files/folders in a parent folder (paginated). | query `parent_id` (Optional[str]); query `project_id` (Optional[str]); query `limit` (int); query `offset` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_list_files` |
| DELETE | `/vault/files/{file_id}` | Eliminación | Soft-delete a file or folder (recursive for folders). | path `file_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `deleted`, `file`; status usados en errores/respuestas: 404, 500 | `api/routers/vault.py::api_delete_file` |
| GET | `/vault/files/{file_id}` | Consulta | Get single file/folder metadata. | path `file_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/vault.py::api_get_file` |
| PATCH | `/vault/files/{file_id}` | Actualización | Rename or move a file/folder. | path `file_id` (str); body `body` (modelo `FileUpdate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 500 | `api/routers/vault.py::api_update_file` |
| GET | `/vault/files/{file_id}/download` | Consulta | Get download URL for a file (optionally a specific version). | path `file_id` (str); query `version_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `url`; status usados en errores/respuestas: 404, 500 | `api/routers/vault.py::api_download_file` |
| POST | `/vault/files/{file_id}/restore/{version_id}` | Creación/Comando | Restore an old version as the current version. | path `file_id` (str); path `version_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/vault.py::api_restore_version` |
| GET | `/vault/files/{file_id}/versions` | Consulta | Get version history for a file. | path `file_id` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_list_versions` |
| POST | `/vault/files/{file_id}/versions` | Acción/Comando | Upload a new version of an existing file. | body `request` (modelo `Request`); path `file_id` (str); archivo multipart `file`; form-data `comment` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/vault.py::api_create_version` |
| POST | `/vault/folders` | Acción/Comando | Create a new virtual folder. | body `body` (modelo `FolderCreate`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_create_folder` |
| GET | `/vault/receipt-status` | Consulta | Check which file hashes correspond to processed receipts. | query `hashes` (str); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_receipt_status` |
| POST | `/vault/search` | Proceso/Automatización | Search files by name, type, date range, project. | body `body` (modelo `SearchRequest`); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_search_files` |
| GET | `/vault/tree` | Consulta | Get folder tree for a project (or global vault). | query `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_get_folder_tree` |
| POST | `/vault/upload` | Acción/Comando | Upload a single file (up to ~50MB via this endpoint). | body `request` (modelo `Request`); archivo multipart `file`; form-data `parent_id` (Optional[str]); form-data `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 413, 500 | `api/routers/vault.py::api_upload_file` |
| POST | `/vault/upload-chunk` | Acción/Comando | Store a single chunk for a chunked upload. | archivo multipart `file`; form-data `upload_id` (str); form-data `chunk_index` (int); form-data `total_chunks` (int); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 500 | `api/routers/vault.py::api_upload_chunk` |
| POST | `/vault/upload-complete` | Acción/Comando | Assemble uploaded chunks into a complete file. | form-data `upload_id` (str); form-data `filename` (str); form-data `total_chunks` (int); form-data `content_type` (str); form-data `parent_id` (Optional[str]); form-data `project_id` (Optional[str]); autenticación/autorización (token o usuario resuelto por dependencia) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 400, 500 | `api/routers/vault.py::api_upload_complete` |

## Módulo: vendors
| Método | Endpoint | Tipo de servicio | Objetivo | Necesita | Guarda/Modifica | Regresa | Fuente |
|---|---|---|---|---|---|---|---|
| GET | `/vendors` | Consulta | Lista todos los vendors ordenados por nombre | Sin parámetros de entrada. | No evidente en este handler (lectura o delegación a servicio). | JSON con llaves típicas: `data`; status usados en errores/respuestas: 500 | `api/routers/vendors.py::list_vendors` |
| POST | `/vendors` | Creación/Comando | Crea un nuevo vendor | body `vendor` (modelo `VendorCreate`) | tabla `Vendors` (insert) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 500 | `api/routers/vendors.py::create_vendor` |
| DELETE | `/vendors/{vendor_id}` | Eliminación | Elimina un vendor | path `vendor_id` (str) | tabla `Vendors` (delete) | JSON con llaves típicas: `affected_expenses`, `message`, `warning`; status usados en errores/respuestas: 404, 500 | `api/routers/vendors.py::delete_vendor` |
| GET | `/vendors/{vendor_id}` | Consulta | Obtiene un vendor específico por ID | path `vendor_id` (str) | No evidente en este handler (lectura o delegación a servicio). | status usados en errores/respuestas: 404, 500 | `api/routers/vendors.py::get_vendor` |
| PATCH | `/vendors/{vendor_id}` | Actualización | Actualiza un vendor existente (actualización parcial) | path `vendor_id` (str); body `vendor` (modelo `VendorUpdate`) | tabla `Vendors` (update) | JSON con llaves típicas: `data`, `message`; status usados en errores/respuestas: 400, 404, 500 | `api/routers/vendors.py::update_vendor` |
