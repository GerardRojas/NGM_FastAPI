"""
===============================================================================
 NGM HUB - Knowledge Base for Arturito
===============================================================================
 Contains comprehensive information about each module in NGM HUB.
 This is injected into the assistant instructions to answer user questions.
===============================================================================
"""

# -----------------------------------------------------------------------------
# MODULE DEFINITIONS
# -----------------------------------------------------------------------------

NGM_MODULES = {
    "expenses": {
        "name": "Expenses Engine",
        "url": "expenses.html",
        "description": "Sistema central para gestionar gastos de proyectos de construccion.",
        "permissions": {
            "view": "expenses:view",
            "edit": "expenses:edit",
            "delete": "expenses:delete",
            "approve": "expenses:approve",
        },
        "features": [
            {
                "name": "Ver gastos por factura (Bill)",
                "how": "En la tabla principal, los gastos estan agrupados por Bill (factura). Cada fila es un Bill que puede expandirse para ver sus lineas de detalle. Haz clic en la flecha a la izquierda de cada fila para expandir.",
                "keywords": ["factura", "bill", "agrupados", "expandir", "detalle"],
            },
            {
                "name": "Ver facturas individuales",
                "how": "Cada fila en la tabla principal representa una factura (Bill). Puedes ver el numero de factura, vendor, fecha, monto total, proyecto y estado de autorizacion.",
                "keywords": ["facturas", "bills", "lista"],
            },
            {
                "name": "Escanear recibo con IA",
                "how": "Haz clic en el boton 'Scan Receipt' en la barra de herramientas. Sube una imagen del recibo y la IA extraera automaticamente: vendor, fecha, monto, proyecto y notas.",
                "keywords": ["escanear", "recibo", "scan", "receipt", "ocr", "ia"],
            },
            {
                "name": "Agregar gasto manualmente",
                "how": "Haz clic en 'Add Expense' en la barra de herramientas. Completa el formulario con: Vendor, Proyecto, Cuenta, Monto, Fecha, Metodo de pago y notas opcionales.",
                "keywords": ["agregar", "nuevo", "add", "crear", "expense"],
            },
            {
                "name": "Filtrar gastos",
                "how": "Usa los filtros en la barra de herramientas: por Proyecto (dropdown), por fecha (From/To), o busca texto en el campo de busqueda global.",
                "keywords": ["filtrar", "buscar", "search", "proyecto", "fecha"],
            },
            {
                "name": "Autorizar gastos",
                "how": "Los gastos tienen un estado de autorizacion (Auth Status). Los usuarios con permiso pueden cambiar el estado haciendo clic en el checkbox de autorizacion en cada gasto.",
                "keywords": ["autorizar", "aprobar", "auth", "status", "permiso"],
            },
            {
                "name": "Editar gastos",
                "how": "Haz clic en 'Edit Expenses' para entrar en modo edicion. Podras modificar campos directamente en la tabla. Guarda con 'Save Changes' o cancela con 'Cancel'.",
                "keywords": ["editar", "modificar", "edit", "cambiar"],
            },
            {
                "name": "Ver notas de un gasto",
                "how": "Las notas se muestran en la columna 'Notes' de cada linea de gasto. Al expandir un Bill, puedes ver las notas de cada linea individual.",
                "keywords": ["notas", "notes", "comentarios", "memo"],
            },
        ],
        "common_questions": [
            {
                "q": "Donde veo los gastos agrupados por factura?",
                "a": "En Expenses Engine, cada fila de la tabla principal es un Bill (factura). Haz clic en la flecha para expandir y ver las lineas individuales de ese Bill.",
            },
            {
                "q": "Como escaneo un recibo?",
                "a": "En Expenses Engine, haz clic en 'Scan Receipt'. Sube la imagen y la IA extraera automaticamente los datos del recibo.",
            },
            {
                "q": "Como filtro por proyecto?",
                "a": "Usa el dropdown 'Project' en la barra de herramientas de Expenses para seleccionar un proyecto especifico.",
            },
        ],
    },

    "pipeline": {
        "name": "Pipeline Manager",
        "url": "pipeline.html",
        "description": "Gestor de tareas estilo Kanban para seguimiento de proyectos y workflows.",
        "permissions": {
            "view": "pipeline:view",
            "edit": "pipeline:edit",
            "delete": "pipeline:delete",
        },
        "features": [
            {
                "name": "Vista Kanban",
                "how": "Las tareas se organizan en columnas por estado: To Do, In Progress, Review, Done. Arrastra tareas entre columnas para cambiar su estado.",
                "keywords": ["kanban", "columnas", "estados", "drag", "arrastrar"],
            },
            {
                "name": "Crear tarea",
                "how": "Haz clic en 'New Task' en la barra de herramientas. Completa: titulo, descripcion, proyecto, asignado, fecha limite y prioridad.",
                "keywords": ["crear", "nueva", "tarea", "task", "agregar"],
            },
            {
                "name": "Asignar tareas",
                "how": "Al crear o editar una tarea, usa el 'People Picker' para asignar usuarios. Puedes asignar multiples personas.",
                "keywords": ["asignar", "assign", "responsable", "persona"],
            },
            {
                "name": "Filtrar tareas",
                "how": "Usa los filtros: por proyecto, por asignado, por prioridad, o busca por texto.",
                "keywords": ["filtrar", "buscar", "filter"],
            },
            {
                "name": "Ver mis tareas",
                "how": "Usa el filtro 'Assigned to me' para ver solo las tareas que te han asignado.",
                "keywords": ["mis tareas", "asignadas", "my tasks"],
            },
        ],
        "common_questions": [
            {
                "q": "Como creo una tarea?",
                "a": "En Pipeline Manager, haz clic en 'New Task', completa el formulario y guarda.",
            },
            {
                "q": "Como veo solo mis tareas?",
                "a": "Usa el filtro 'Assigned to me' en la barra de herramientas del Pipeline.",
            },
        ],
    },

    "projects": {
        "name": "Projects",
        "url": "projects.html",
        "description": "Gestion centralizada de proyectos de construccion.",
        "permissions": {
            "view": "projects:view",
            "edit": "projects:edit",
            "delete": "projects:delete",
        },
        "features": [
            {
                "name": "Ver lista de proyectos",
                "how": "La tabla muestra todos los proyectos con: nombre, compania, ciudad, cliente y estado.",
                "keywords": ["lista", "proyectos", "ver", "todos"],
            },
            {
                "name": "Agregar proyecto",
                "how": "Haz clic en 'Add Project'. Completa: nombre, compania, cliente, direccion, ciudad y estado.",
                "keywords": ["agregar", "nuevo", "crear", "add", "project"],
            },
            {
                "name": "Buscar proyectos",
                "how": "Usa el campo de busqueda en la barra superior para filtrar por nombre, ciudad o cliente.",
                "keywords": ["buscar", "search", "filtrar"],
            },
        ],
        "common_questions": [
            {
                "q": "Como agrego un nuevo proyecto?",
                "a": "En Projects, haz clic en 'Add Project', completa el formulario y guarda.",
            },
        ],
    },

    "vendors": {
        "name": "Vendors",
        "url": "vendors.html",
        "description": "Gestion de proveedores y subcontratistas.",
        "permissions": {
            "view": "vendors:view",
            "edit": "vendors:edit",
            "delete": "vendors:delete",
        },
        "features": [
            {
                "name": "Lista de vendors",
                "how": "Tabla con todos los vendors registrados en el sistema.",
                "keywords": ["lista", "vendors", "proveedores"],
            },
            {
                "name": "Agregar vendor",
                "how": "Haz clic en 'Add Vendor' para registrar un nuevo proveedor.",
                "keywords": ["agregar", "nuevo", "add", "vendor"],
            },
        ],
    },

    "accounts": {
        "name": "Accounts",
        "url": "accounts.html",
        "description": "Chart of Accounts - Catalogo de cuentas contables.",
        "permissions": {
            "view": "accounts:view",
            "edit": "accounts:edit",
            "delete": "accounts:delete",
        },
        "features": [
            {
                "name": "Ver cuentas",
                "how": "Lista todas las cuentas con numero, nombre y categoria. Ordena por numero, categoria o A-Z.",
                "keywords": ["cuentas", "accounts", "chart"],
            },
            {
                "name": "Agregar cuenta",
                "how": "Haz clic en 'Add Account' para crear una nueva cuenta contable.",
                "keywords": ["agregar", "nueva", "cuenta", "add"],
            },
        ],
    },

    "team": {
        "name": "Team Management",
        "url": "team.html",
        "description": "Gestion de usuarios, roles y permisos del sistema.",
        "permissions": {
            "view": "team:view",
            "edit": "team:edit",
            "delete": "team:delete",
        },
        "features": [
            {
                "name": "Ver usuarios",
                "how": "Panel con tarjetas de todos los usuarios del sistema. Muestra nombre, rol, seniority y estado.",
                "keywords": ["usuarios", "users", "equipo", "team"],
            },
            {
                "name": "Agregar usuario",
                "how": "Haz clic en 'Add User'. Completa: nombre, email, rol, seniority y permisos.",
                "keywords": ["agregar", "nuevo", "usuario", "add", "user"],
            },
            {
                "name": "Gestionar roles",
                "how": "Haz clic en 'Manage Roles' para ver y editar la matriz de permisos por rol.",
                "keywords": ["roles", "permisos", "permissions"],
            },
        ],
        "common_questions": [
            {
                "q": "Como agrego un nuevo usuario?",
                "a": "En Team Management, haz clic en 'Add User', completa el formulario con email, nombre, rol y permisos.",
            },
            {
                "q": "Como cambio los permisos de un rol?",
                "a": "En Team Management, haz clic en 'Manage Roles'. Se abrira la matriz de permisos donde puedes editar que puede hacer cada rol.",
            },
        ],
    },

    "messages": {
        "name": "Messages",
        "url": "messages.html",
        "description": "Sistema de mensajeria interna tipo chat empresarial.",
        "permissions": {
            "view": "messages:view",
        },
        "features": [
            {
                "name": "Canales de proyecto",
                "how": "Cada proyecto tiene su canal automatico. Accede desde la seccion 'Projects' en el sidebar de mensajes.",
                "keywords": ["canales", "channels", "proyecto", "project"],
            },
            {
                "name": "Mensajes directos",
                "how": "Seccion 'Direct Messages' para conversaciones privadas con otros usuarios.",
                "keywords": ["directo", "privado", "dm", "direct"],
            },
            {
                "name": "Mencionar usuarios",
                "how": "Escribe @ seguido del nombre para mencionar a alguien. Recibiran una notificacion.",
                "keywords": ["mencionar", "mention", "@", "notificar"],
            },
            {
                "name": "Crear canal",
                "how": "Haz clic en 'New chat' para crear un nuevo canal personalizado o mensaje directo.",
                "keywords": ["crear", "nuevo", "canal", "chat"],
            },
        ],
    },

    "estimator": {
        "name": "Estimator Suite",
        "url": "estimator.html",
        "description": "Herramienta para crear presupuestos y estimaciones de proyectos.",
        "permissions": {
            "view": "estimator:view",
            "edit": "estimator:edit",
        },
        "features": [
            {
                "name": "Crear estimacion",
                "how": "Haz clic en 'New Estimate'. Define proyecto, categorias y conceptos con cantidades y precios unitarios.",
                "keywords": ["crear", "nueva", "estimacion", "estimate", "presupuesto"],
            },
            {
                "name": "Importar desde Revit",
                "how": "Boton 'Import from Revit' para importar quantidades desde un archivo Revit.",
                "keywords": ["revit", "importar", "import", "cantidades"],
            },
            {
                "name": "Exportar",
                "how": "Boton 'Export' para descargar la estimacion en formato PDF o Excel.",
                "keywords": ["exportar", "export", "pdf", "excel", "descargar"],
            },
            {
                "name": "Guardar como template",
                "how": "Boton 'Save as Template' para guardar la estructura como plantilla reutilizable.",
                "keywords": ["template", "plantilla", "guardar", "reutilizar"],
            },
        ],
    },

    "budgets": {
        "name": "Budgets",
        "url": "budgets.html",
        "description": "Gestion de presupuestos por proyecto importados desde QuickBooks.",
        "permissions": {
            "view": "budgets:view",
            "edit": "budgets:edit",
        },
        "features": [
            {
                "name": "Ver presupuestos",
                "how": "Selecciona un proyecto para ver sus presupuestos activos con desglose por cuenta.",
                "keywords": ["ver", "presupuestos", "budgets", "proyecto"],
            },
            {
                "name": "Importar desde CSV",
                "how": "Boton 'Import from CSV' para cargar presupuestos desde archivo CSV de QuickBooks.",
                "keywords": ["importar", "csv", "import", "quickbooks"],
            },
        ],
    },

    "reporting": {
        "name": "Reporting",
        "url": "reporting.html",
        "description": "Centro de reportes y analisis financiero.",
        "permissions": {
            "view": "reporting:view",
        },
        "features": [
            {
                "name": "Budget vs Actuals",
                "how": "Reporte que compara presupuesto vs gastos reales por proyecto. Disponible en reporting.html o pidele a Arturito: 'BVA de [proyecto]'.",
                "keywords": ["bva", "budget", "actuals", "comparar", "reporte"],
            },
        ],
    },

    "dashboard": {
        "name": "Dashboard",
        "url": "dashboard.html",
        "description": "Panel principal con accesos rapidos y resumen de actividad.",
        "permissions": {
            "view": "dashboard:view",
        },
        "features": [
            {
                "name": "Menciones",
                "how": "Seccion que muestra mensajes donde te han mencionado (@tunombre).",
                "keywords": ["menciones", "mentions", "@"],
            },
            {
                "name": "Accesos rapidos",
                "how": "Tarjetas de acceso directo a cada modulo del sistema.",
                "keywords": ["accesos", "modulos", "cards"],
            },
        ],
    },
}

# -----------------------------------------------------------------------------
# AVAILABLE ACTIONS (Commands the bot can execute)
# -----------------------------------------------------------------------------

NGM_ACTIONS = {
    # Navigation
    "navigate_expenses": {
        "description": "Ir a Expenses Engine",
        "action_type": "navigate",
        "target": "expenses.html",
        "permission": "expenses:view",
        "keywords": ["ir a gastos", "abrir expenses", "go to expenses", "ver gastos"],
    },
    "navigate_pipeline": {
        "description": "Ir a Pipeline Manager",
        "action_type": "navigate",
        "target": "pipeline.html",
        "permission": "pipeline:view",
        "keywords": ["ir a pipeline", "abrir tareas", "go to pipeline", "ver tareas"],
    },
    "navigate_projects": {
        "description": "Ir a Projects",
        "action_type": "navigate",
        "target": "projects.html",
        "permission": "projects:view",
        "keywords": ["ir a proyectos", "abrir projects", "ver proyectos"],
    },
    "navigate_messages": {
        "description": "Ir a Messages",
        "action_type": "navigate",
        "target": "messages.html",
        "permission": "messages:view",
        "keywords": ["ir a mensajes", "abrir chat", "ver mensajes"],
    },
    "navigate_team": {
        "description": "Ir a Team Management",
        "action_type": "navigate",
        "target": "team.html",
        "permission": "team:view",
        "keywords": ["ir a equipo", "ver usuarios", "team"],
    },
    "navigate_vendors": {
        "description": "Ir a Vendors",
        "action_type": "navigate",
        "target": "vendors.html",
        "permission": "vendors:view",
        "keywords": ["ir a vendors", "ver proveedores", "abrir vendors"],
    },
    "navigate_accounts": {
        "description": "Ir a Accounts",
        "action_type": "navigate",
        "target": "accounts.html",
        "permission": "accounts:view",
        "keywords": ["ir a cuentas", "ver accounts", "abrir cuentas"],
    },
    "navigate_budgets": {
        "description": "Ir a Budgets",
        "action_type": "navigate",
        "target": "budgets.html",
        "permission": "budgets:view",
        "keywords": ["ir a presupuestos", "ver budgets", "abrir budgets"],
    },
    "navigate_reporting": {
        "description": "Ir a Reporting",
        "action_type": "navigate",
        "target": "reporting.html",
        "permission": "reporting:view",
        "keywords": ["ir a reportes", "ver reports", "abrir reporting"],
    },

    # Modals / Actions
    "open_add_expense": {
        "description": "Abrir modal para agregar gasto",
        "action_type": "open_modal",
        "target": "add_expense",
        "required_page": "expenses.html",
        "permission": "expenses:edit",
        "keywords": ["agregar gasto", "nuevo gasto", "add expense", "crear gasto"],
    },
    "open_scan_receipt": {
        "description": "Abrir modal para escanear recibo",
        "action_type": "open_modal",
        "target": "scan_receipt",
        "required_page": "expenses.html",
        "permission": "expenses:edit",
        "keywords": ["escanear recibo", "scan receipt", "subir recibo", "foto recibo"],
    },
    "open_new_task": {
        "description": "Abrir modal para crear tarea",
        "action_type": "open_modal",
        "target": "new_task",
        "required_page": "pipeline.html",
        "permission": "pipeline:edit",
        "keywords": ["crear tarea", "nueva tarea", "new task", "agregar tarea"],
    },
    "open_add_project": {
        "description": "Abrir modal para agregar proyecto",
        "action_type": "open_modal",
        "target": "add_project",
        "required_page": "projects.html",
        "permission": "projects:edit",
        "keywords": ["agregar proyecto", "nuevo proyecto", "add project", "crear proyecto"],
    },
    "open_add_user": {
        "description": "Abrir modal para agregar usuario",
        "action_type": "open_modal",
        "target": "add_user",
        "required_page": "team.html",
        "permission": "team:edit",
        "keywords": ["agregar usuario", "nuevo usuario", "add user", "crear usuario"],
    },

    # Special actions
    "send_message_to": {
        "description": "Enviar mensaje a un usuario",
        "action_type": "send_message",
        "permission": "messages:view",
        "requires_entity": "user_name",
        "keywords": ["enviar mensaje", "mandar mensaje", "send message", "escribir a"],
    },
    "report_bug": {
        "description": "Reportar un bug o problema",
        "action_type": "create_task",
        "task_type": "bug",
        "permission": "pipeline:edit",
        "keywords": ["reportar bug", "reportar problema", "report bug", "hay un error", "no funciona"],
    },
}

# -----------------------------------------------------------------------------
# ROLES THAT CAN HELP WITH SPECIFIC ACTIONS
# -----------------------------------------------------------------------------

HELPER_ROLES = {
    "expenses:edit": ["CEO", "COO", "Bookkeeper", "Accountant"],
    "expenses:approve": ["CEO", "COO"],
    "pipeline:edit": ["CEO", "COO", "Project Manager", "Superintendent"],
    "projects:edit": ["CEO", "COO", "Project Manager"],
    "team:edit": ["CEO", "COO", "HR Manager"],
    "estimator:edit": ["CEO", "COO", "Estimator", "Project Manager"],
}


# -----------------------------------------------------------------------------
# COPILOT ACTIONS (Page-specific commands)
# -----------------------------------------------------------------------------
# These are actions that can be executed on the current page without navigation.
# The bot acts as a copilot, controlling the UI based on user commands.

COPILOT_ACTIONS = {
    "expenses.html": {
        "page_name": "Expenses Engine",
        "actions": {
            # Filters
            "filter_by_auth_status": {
                "description": "Filtrar gastos por estado de autorizacion",
                "command": "filterByAuthStatus",
                "params": ["status"],  # pending, authorized, all
                "keywords": [
                    "pendientes de autorizar", "sin autorizar", "pending auth",
                    "autorizados", "authorized", "todos los gastos", "all expenses",
                    "mostrar pendientes", "show pending", "ver pendientes",
                ],
                "examples": [
                    "muestrame solo los gastos pendientes de autorizar",
                    "filtrar por pendientes",
                    "ver gastos autorizados",
                    "mostrar todos los gastos",
                ],
            },
            "filter_by_project": {
                "description": "Filtrar gastos por proyecto",
                "command": "filterByProject",
                "params": ["project_name"],
                "keywords": [
                    "filtrar por proyecto", "gastos de", "expenses from",
                    "mostrar proyecto", "ver proyecto", "solo proyecto",
                ],
                "examples": [
                    "muestrame los gastos de Del Rio",
                    "filtrar por proyecto Arthur Neal",
                    "solo gastos del proyecto X",
                ],
            },
            "filter_by_date": {
                "description": "Filtrar gastos por rango de fechas",
                "command": "filterByDateRange",
                "params": ["start_date", "end_date"],
                "keywords": [
                    "filtrar por fecha", "gastos de", "desde", "hasta",
                    "este mes", "mes pasado", "ultima semana", "this month",
                    "last month", "last week", "year to date",
                ],
                "examples": [
                    "muestrame gastos de este mes",
                    "gastos del mes pasado",
                    "filtrar desde enero hasta marzo",
                ],
            },
            "filter_by_vendor": {
                "description": "Filtrar gastos por vendor",
                "command": "filterByVendor",
                "params": ["vendor_name"],
                "keywords": [
                    "filtrar por vendor", "gastos de vendor", "proveedor",
                    "mostrar vendor", "buscar vendor",
                ],
                "examples": [
                    "muestrame gastos de Home Depot",
                    "filtrar por vendor Lowes",
                ],
            },
            "clear_filters": {
                "description": "Limpiar todos los filtros",
                "command": "clearFilters",
                "params": [],
                "keywords": [
                    "limpiar filtros", "quitar filtros", "clear filters",
                    "mostrar todos", "reset filtros", "sin filtros",
                ],
                "examples": [
                    "quitar todos los filtros",
                    "limpiar filtros",
                    "mostrar todos los gastos",
                ],
            },
            # Sorting
            "sort_by_column": {
                "description": "Ordenar tabla por columna",
                "command": "sortByColumn",
                "params": ["column", "direction"],  # asc, desc
                "keywords": [
                    "ordenar por", "sort by", "de mayor a menor", "de menor a mayor",
                    "mas recientes", "mas antiguos", "por fecha", "por monto",
                ],
                "examples": [
                    "ordenar por fecha mas reciente",
                    "ordenar por monto de mayor a menor",
                    "mostrar los mas recientes primero",
                ],
            },
            # View modes
            "expand_all_bills": {
                "description": "Expandir todas las facturas",
                "command": "expandAllBills",
                "params": [],
                "keywords": [
                    "expandir todo", "expand all", "mostrar detalles",
                    "ver todas las lineas", "abrir todo",
                ],
                "examples": [
                    "expandir todas las facturas",
                    "mostrar todos los detalles",
                ],
            },
            "collapse_all_bills": {
                "description": "Colapsar todas las facturas",
                "command": "collapseAllBills",
                "params": [],
                "keywords": [
                    "colapsar todo", "collapse all", "ocultar detalles",
                    "cerrar todo", "contraer",
                ],
                "examples": [
                    "colapsar todas las facturas",
                    "cerrar todos los detalles",
                ],
            },
            # Search
            "search_text": {
                "description": "Buscar texto en la tabla",
                "command": "searchText",
                "params": ["query"],
                "keywords": [
                    "buscar", "search", "encontrar", "find",
                ],
                "examples": [
                    "buscar lumber",
                    "encontrar gastos con 'plumbing'",
                ],
            },
        },
    },

    "pipeline.html": {
        "page_name": "Pipeline Manager",
        "actions": {
            "filter_by_status": {
                "description": "Filtrar tareas por estado",
                "command": "filterByStatus",
                "params": ["status"],  # todo, in_progress, review, done
                "keywords": [
                    "tareas pendientes", "en progreso", "por revisar", "completadas",
                    "to do", "in progress", "review", "done",
                ],
                "examples": [
                    "muestrame solo tareas pendientes",
                    "filtrar por en progreso",
                    "ver tareas completadas",
                ],
            },
            "filter_by_assignee": {
                "description": "Filtrar tareas por asignado",
                "command": "filterByAssignee",
                "params": ["user_name"],
                "keywords": [
                    "tareas de", "asignadas a", "assigned to", "mis tareas",
                    "my tasks",
                ],
                "examples": [
                    "muestrame mis tareas",
                    "tareas asignadas a German",
                    "filtrar por asignado",
                ],
            },
            "filter_by_priority": {
                "description": "Filtrar tareas por prioridad",
                "command": "filterByPriority",
                "params": ["priority"],  # high, medium, low
                "keywords": [
                    "prioridad alta", "high priority", "urgentes",
                    "prioridad baja", "low priority",
                ],
                "examples": [
                    "muestrame tareas de alta prioridad",
                    "filtrar por urgentes",
                ],
            },
            "filter_by_project": {
                "description": "Filtrar tareas por proyecto",
                "command": "filterByProject",
                "params": ["project_name"],
                "keywords": [
                    "tareas del proyecto", "filtrar por proyecto",
                ],
                "examples": [
                    "muestrame tareas del proyecto Del Rio",
                ],
            },
            "clear_filters": {
                "description": "Limpiar todos los filtros",
                "command": "clearFilters",
                "params": [],
                "keywords": [
                    "limpiar filtros", "quitar filtros", "clear filters",
                    "mostrar todas", "reset",
                ],
                "examples": [
                    "quitar todos los filtros",
                    "mostrar todas las tareas",
                ],
            },
        },
    },

    "projects.html": {
        "page_name": "Projects",
        "actions": {
            "search_project": {
                "description": "Buscar proyecto",
                "command": "searchProject",
                "params": ["query"],
                "keywords": [
                    "buscar proyecto", "search project", "encontrar",
                ],
                "examples": [
                    "buscar proyecto Del Rio",
                    "encontrar Arthur Neal",
                ],
            },
            "filter_by_status": {
                "description": "Filtrar por estado de proyecto",
                "command": "filterByStatus",
                "params": ["status"],  # active, completed, on_hold
                "keywords": [
                    "proyectos activos", "active projects", "completados",
                    "en pausa", "on hold",
                ],
                "examples": [
                    "muestrame solo proyectos activos",
                    "filtrar por completados",
                ],
            },
        },
    },

    "vendors.html": {
        "page_name": "Vendors",
        "actions": {
            "search_vendor": {
                "description": "Buscar vendor",
                "command": "searchVendor",
                "params": ["query"],
                "keywords": [
                    "buscar vendor", "search vendor", "encontrar proveedor",
                ],
                "examples": [
                    "buscar Home Depot",
                    "encontrar vendor X",
                ],
            },
        },
    },

    "team.html": {
        "page_name": "Team Management",
        "actions": {
            "filter_by_role": {
                "description": "Filtrar usuarios por rol",
                "command": "filterByRole",
                "params": ["role"],
                "keywords": [
                    "filtrar por rol", "mostrar solo", "role",
                ],
                "examples": [
                    "muestrame solo Project Managers",
                    "filtrar por rol Bookkeeper",
                ],
            },
            "search_user": {
                "description": "Buscar usuario",
                "command": "searchUser",
                "params": ["query"],
                "keywords": [
                    "buscar usuario", "search user", "encontrar",
                ],
                "examples": [
                    "buscar usuario German",
                ],
            },
        },
    },
}


def find_copilot_action(message: str, current_page: str) -> dict | None:
    """
    Find a copilot action that matches the user's intent on the current page.

    Args:
        message: User's message
        current_page: Current page URL (e.g., 'expenses.html')

    Returns:
        Dict with action details or None if no match
    """
    message_lower = message.lower()

    # Get actions for current page
    page_config = COPILOT_ACTIONS.get(current_page)
    if not page_config:
        return None

    actions = page_config.get("actions", {})

    for action_id, action in actions.items():
        # Check keywords
        for keyword in action.get("keywords", []):
            if keyword.lower() in message_lower:
                return {
                    "action_id": action_id,
                    "command": action["command"],
                    "description": action["description"],
                    "params": action["params"],
                    "page": current_page,
                    "page_name": page_config["page_name"],
                }

    return None


def extract_copilot_params(message: str, action: dict) -> dict:
    """
    Extract parameters from the message for a copilot action.

    This is a simple extraction - for more complex cases,
    the NLU/GPT should handle parameter extraction.
    """
    params = {}
    message_lower = message.lower()

    # Common parameter extractions based on action type
    if "status" in action.get("params", []):
        # Auth status
        if "pendiente" in message_lower or "pending" in message_lower or "sin autorizar" in message_lower:
            params["status"] = "pending"
        elif "autorizado" in message_lower or "authorized" in message_lower:
            params["status"] = "authorized"
        elif "todo" in message_lower or "all" in message_lower:
            params["status"] = "all"
        # Task status
        elif "progreso" in message_lower or "progress" in message_lower:
            params["status"] = "in_progress"
        elif "revisar" in message_lower or "review" in message_lower:
            params["status"] = "review"
        elif "completad" in message_lower or "done" in message_lower:
            params["status"] = "done"
        elif "pendiente" in message_lower or "to do" in message_lower or "todo" in message_lower:
            params["status"] = "todo"

    if "priority" in action.get("params", []):
        if "alta" in message_lower or "high" in message_lower or "urgente" in message_lower:
            params["priority"] = "high"
        elif "media" in message_lower or "medium" in message_lower:
            params["priority"] = "medium"
        elif "baja" in message_lower or "low" in message_lower:
            params["priority"] = "low"

    if "direction" in action.get("params", []):
        if "mayor a menor" in message_lower or "desc" in message_lower or "reciente" in message_lower:
            params["direction"] = "desc"
        else:
            params["direction"] = "asc"

    # For project_name, vendor_name, user_name, query - these would need
    # more sophisticated extraction (entity recognition)
    # For now, we'll let the handler ask for clarification if needed

    return params


# -----------------------------------------------------------------------------
# GENERATE KNOWLEDGE PROMPT FOR ASSISTANT
# -----------------------------------------------------------------------------

def get_ngm_hub_knowledge() -> str:
    """
    Generate a comprehensive knowledge prompt about NGM HUB
    to inject into the assistant instructions.
    """
    sections = []

    # Header
    sections.append("""
===============================================================================
 NGM HUB - KNOWLEDGE BASE
===============================================================================
NGM HUB es una plataforma web empresarial para gestion de proyectos de construccion.
Modulos disponibles:
""")

    # Modules summary
    for module_id, module in NGM_MODULES.items():
        sections.append(f"- {module['name']} ({module['url']}): {module['description']}")

    sections.append("\n")

    # Detailed module info
    for module_id, module in NGM_MODULES.items():
        sections.append(f"\n### {module['name'].upper()}")
        sections.append(f"URL: {module['url']}")
        sections.append(f"Descripcion: {module['description']}")

        if module.get("features"):
            sections.append("\nFuncionalidades:")
            for feature in module["features"]:
                sections.append(f"  - {feature['name']}: {feature['how']}")

        if module.get("common_questions"):
            sections.append("\nPreguntas frecuentes:")
            for qa in module["common_questions"]:
                sections.append(f"  P: {qa['q']}")
                sections.append(f"  R: {qa['a']}")

    # Actions
    sections.append("""

===============================================================================
 ACCIONES DISPONIBLES
===============================================================================
Puedes ejecutar las siguientes acciones en el navegador del usuario.
IMPORTANTE: Antes de ejecutar una accion, verifica los permisos del usuario.

Cuando detectes una intencion de accion, responde con JSON estructurado:
{
  "action": "action_id",
  "params": {...}
}

Acciones disponibles:
""")

    for action_id, action in NGM_ACTIONS.items():
        sections.append(f"- {action_id}: {action['description']} (requiere: {action['permission']})")

    # Permission handling
    sections.append("""

===============================================================================
 MANEJO DE PERMISOS
===============================================================================
Si el usuario NO tiene permiso para una accion:
1. Explica amablemente que no tiene acceso
2. Sugiere quien puede ayudarle (roles con ese permiso)
3. Ofrece enviar un mensaje a esa persona

Ejemplo de respuesta sin permiso:
"No tienes acceso para crear gastos. El Bookkeeper o el COO pueden ayudarte.
Quieres que le envie un mensaje para solicitarlo?"

===============================================================================
 REPORTE DE BUGS
===============================================================================
Si el usuario reporta un problema o bug:
1. Pide detalles: que intentaba hacer, que paso, que esperaba
2. Ofrece crear una tarea de tipo "bug" en Pipeline
3. Asigna la tarea al equipo de desarrollo (si conoces el usuario apropiado)
""")

    return "\n".join(sections)


def find_answer_for_question(question: str) -> str | None:
    """
    Search for a direct answer in common_questions.
    Returns the answer if found, None otherwise.
    """
    question_lower = question.lower()

    for module in NGM_MODULES.values():
        if not module.get("common_questions"):
            continue
        for qa in module["common_questions"]:
            # Check if question matches
            q_words = set(qa["q"].lower().split())
            question_words = set(question_lower.split())
            # If significant overlap
            overlap = len(q_words & question_words)
            if overlap >= 3:
                return qa["a"]

    return None


def find_feature_by_keywords(keywords: list[str]) -> dict | None:
    """
    Find a feature that matches the given keywords.
    """
    for module_id, module in NGM_MODULES.items():
        if not module.get("features"):
            continue
        for feature in module["features"]:
            feature_keywords = set(feature.get("keywords", []))
            for kw in keywords:
                if kw.lower() in feature_keywords:
                    return {
                        "module": module_id,
                        "module_name": module["name"],
                        "url": module["url"],
                        "feature": feature["name"],
                        "how": feature["how"],
                    }
    return None


def find_action_by_intent(message: str) -> dict | None:
    """
    Find an action that matches the user's intent.
    """
    message_lower = message.lower()

    for action_id, action in NGM_ACTIONS.items():
        for keyword in action.get("keywords", []):
            if keyword.lower() in message_lower:
                return {
                    "action_id": action_id,
                    **action,
                }
    return None
