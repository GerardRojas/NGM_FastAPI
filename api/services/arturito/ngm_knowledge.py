"""
═══════════════════════════════════════════════════════════════════════════════
 NGM HUB - Knowledge Base for Arturito
═══════════════════════════════════════════════════════════════════════════════
 Contains comprehensive information about each module in NGM HUB.
 This is injected into the assistant instructions to answer user questions.
═══════════════════════════════════════════════════════════════════════════════
"""

# ─────────────────────────────────────────────────────────────────────────────
# MODULE DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

NGM_MODULES = {
    "expenses": {
        "name": "Expenses Engine",
        "url": "expenses.html",
        "description": "Sistema central para gestionar gastos de proyectos de construcción.",
        "permissions": {
            "view": "expenses:view",
            "edit": "expenses:edit",
            "delete": "expenses:delete",
            "approve": "expenses:approve",
        },
        "features": [
            {
                "name": "Ver gastos por factura (Bill)",
                "how": "En la tabla principal, los gastos están agrupados por Bill (factura). Cada fila es un Bill que puede expandirse para ver sus líneas de detalle. Haz clic en la flecha (▸) a la izquierda de cada fila para expandir.",
                "keywords": ["factura", "bill", "agrupados", "expandir", "detalle"],
            },
            {
                "name": "Ver facturas individuales",
                "how": "Cada fila en la tabla principal representa una factura (Bill). Puedes ver el número de factura, vendor, fecha, monto total, proyecto y estado de autorización.",
                "keywords": ["facturas", "bills", "lista"],
            },
            {
                "name": "Escanear recibo con IA",
                "how": "Haz clic en el botón 'Scan Receipt' en la barra de herramientas. Sube una imagen del recibo y la IA extraerá automáticamente: vendor, fecha, monto, proyecto y notas.",
                "keywords": ["escanear", "recibo", "scan", "receipt", "ocr", "ia"],
            },
            {
                "name": "Agregar gasto manualmente",
                "how": "Haz clic en 'Add Expense' en la barra de herramientas. Completa el formulario con: Vendor, Proyecto, Cuenta, Monto, Fecha, Método de pago y notas opcionales.",
                "keywords": ["agregar", "nuevo", "add", "crear", "expense"],
            },
            {
                "name": "Filtrar gastos",
                "how": "Usa los filtros en la barra de herramientas: por Proyecto (dropdown), por fecha (From/To), o busca texto en el campo de búsqueda global.",
                "keywords": ["filtrar", "buscar", "search", "proyecto", "fecha"],
            },
            {
                "name": "Autorizar gastos",
                "how": "Los gastos tienen un estado de autorización (Auth Status). Los usuarios con permiso pueden cambiar el estado haciendo clic en el checkbox de autorización en cada gasto.",
                "keywords": ["autorizar", "aprobar", "auth", "status", "permiso"],
            },
            {
                "name": "Editar gastos",
                "how": "Haz clic en 'Edit Expenses' para entrar en modo edición. Podrás modificar campos directamente en la tabla. Guarda con 'Save Changes' o cancela con 'Cancel'.",
                "keywords": ["editar", "modificar", "edit", "cambiar"],
            },
            {
                "name": "Ver notas de un gasto",
                "how": "Las notas se muestran en la columna 'Notes' de cada línea de gasto. Al expandir un Bill, puedes ver las notas de cada línea individual.",
                "keywords": ["notas", "notes", "comentarios", "memo"],
            },
        ],
        "common_questions": [
            {
                "q": "¿Dónde veo los gastos agrupados por factura?",
                "a": "En Expenses Engine, cada fila de la tabla principal es un Bill (factura). Haz clic en la flecha ▸ para expandir y ver las líneas individuales de ese Bill.",
            },
            {
                "q": "¿Cómo escaneo un recibo?",
                "a": "En Expenses Engine, haz clic en 'Scan Receipt'. Sube la imagen y la IA extraerá automáticamente los datos del recibo.",
            },
            {
                "q": "¿Cómo filtro por proyecto?",
                "a": "Usa el dropdown 'Project' en la barra de herramientas de Expenses para seleccionar un proyecto específico.",
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
                "how": "Haz clic en 'New Task' en la barra de herramientas. Completa: título, descripción, proyecto, asignado, fecha límite y prioridad.",
                "keywords": ["crear", "nueva", "tarea", "task", "agregar"],
            },
            {
                "name": "Asignar tareas",
                "how": "Al crear o editar una tarea, usa el 'People Picker' para asignar usuarios. Puedes asignar múltiples personas.",
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
                "q": "¿Cómo creo una tarea?",
                "a": "En Pipeline Manager, haz clic en 'New Task', completa el formulario y guarda.",
            },
            {
                "q": "¿Cómo veo solo mis tareas?",
                "a": "Usa el filtro 'Assigned to me' en la barra de herramientas del Pipeline.",
            },
        ],
    },

    "projects": {
        "name": "Projects",
        "url": "projects.html",
        "description": "Gestión centralizada de proyectos de construcción.",
        "permissions": {
            "view": "projects:view",
            "edit": "projects:edit",
            "delete": "projects:delete",
        },
        "features": [
            {
                "name": "Ver lista de proyectos",
                "how": "La tabla muestra todos los proyectos con: nombre, compañía, ciudad, cliente y estado.",
                "keywords": ["lista", "proyectos", "ver", "todos"],
            },
            {
                "name": "Agregar proyecto",
                "how": "Haz clic en 'Add Project'. Completa: nombre, compañía, cliente, dirección, ciudad y estado.",
                "keywords": ["agregar", "nuevo", "crear", "add", "project"],
            },
            {
                "name": "Buscar proyectos",
                "how": "Usa el campo de búsqueda en la barra superior para filtrar por nombre, ciudad o cliente.",
                "keywords": ["buscar", "search", "filtrar"],
            },
        ],
        "common_questions": [
            {
                "q": "¿Cómo agrego un nuevo proyecto?",
                "a": "En Projects, haz clic en 'Add Project', completa el formulario y guarda.",
            },
        ],
    },

    "vendors": {
        "name": "Vendors",
        "url": "vendors.html",
        "description": "Gestión de proveedores y subcontratistas.",
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
        "description": "Chart of Accounts - Catálogo de cuentas contables.",
        "permissions": {
            "view": "accounts:view",
            "edit": "accounts:edit",
            "delete": "accounts:delete",
        },
        "features": [
            {
                "name": "Ver cuentas",
                "how": "Lista todas las cuentas con número, nombre y categoría. Ordena por número, categoría o A-Z.",
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
        "description": "Gestión de usuarios, roles y permisos del sistema.",
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
                "q": "¿Cómo agrego un nuevo usuario?",
                "a": "En Team Management, haz clic en 'Add User', completa el formulario con email, nombre, rol y permisos.",
            },
            {
                "q": "¿Cómo cambio los permisos de un rol?",
                "a": "En Team Management, haz clic en 'Manage Roles'. Se abrirá la matriz de permisos donde puedes editar qué puede hacer cada rol.",
            },
        ],
    },

    "messages": {
        "name": "Messages",
        "url": "messages.html",
        "description": "Sistema de mensajería interna tipo chat empresarial.",
        "permissions": {
            "view": "messages:view",
        },
        "features": [
            {
                "name": "Canales de proyecto",
                "how": "Cada proyecto tiene su canal automático. Accede desde la sección 'Projects' en el sidebar de mensajes.",
                "keywords": ["canales", "channels", "proyecto", "project"],
            },
            {
                "name": "Mensajes directos",
                "how": "Sección 'Direct Messages' para conversaciones privadas con otros usuarios.",
                "keywords": ["directo", "privado", "dm", "direct"],
            },
            {
                "name": "Mencionar usuarios",
                "how": "Escribe @ seguido del nombre para mencionar a alguien. Recibirán una notificación.",
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
                "name": "Crear estimación",
                "how": "Haz clic en 'New Estimate'. Define proyecto, categorías y conceptos con cantidades y precios unitarios.",
                "keywords": ["crear", "nueva", "estimación", "estimate", "presupuesto"],
            },
            {
                "name": "Importar desde Revit",
                "how": "Botón 'Import from Revit' para importar quantidades desde un archivo Revit.",
                "keywords": ["revit", "importar", "import", "cantidades"],
            },
            {
                "name": "Exportar",
                "how": "Botón 'Export' para descargar la estimación en formato PDF o Excel.",
                "keywords": ["exportar", "export", "pdf", "excel", "descargar"],
            },
            {
                "name": "Guardar como template",
                "how": "Botón 'Save as Template' para guardar la estructura como plantilla reutilizable.",
                "keywords": ["template", "plantilla", "guardar", "reutilizar"],
            },
        ],
    },

    "budgets": {
        "name": "Budgets",
        "url": "budgets.html",
        "description": "Gestión de presupuestos por proyecto importados desde QuickBooks.",
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
                "how": "Botón 'Import from CSV' para cargar presupuestos desde archivo CSV de QuickBooks.",
                "keywords": ["importar", "csv", "import", "quickbooks"],
            },
        ],
    },

    "reporting": {
        "name": "Reporting",
        "url": "reporting.html",
        "description": "Centro de reportes y análisis financiero.",
        "permissions": {
            "view": "reporting:view",
        },
        "features": [
            {
                "name": "Budget vs Actuals",
                "how": "Reporte que compara presupuesto vs gastos reales por proyecto. Disponible en reporting.html o pídele a Arturito: 'BVA de [proyecto]'.",
                "keywords": ["bva", "budget", "actuals", "comparar", "reporte"],
            },
        ],
    },

    "dashboard": {
        "name": "Dashboard",
        "url": "dashboard.html",
        "description": "Panel principal con accesos rápidos y resumen de actividad.",
        "permissions": {
            "view": "dashboard:view",
        },
        "features": [
            {
                "name": "Menciones",
                "how": "Sección que muestra mensajes donde te han mencionado (@tunombre).",
                "keywords": ["menciones", "mentions", "@"],
            },
            {
                "name": "Accesos rápidos",
                "how": "Tarjetas de acceso directo a cada módulo del sistema.",
                "keywords": ["accesos", "módulos", "cards"],
            },
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# AVAILABLE ACTIONS (Commands the bot can execute)
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# ROLES THAT CAN HELP WITH SPECIFIC ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

HELPER_ROLES = {
    "expenses:edit": ["CEO", "COO", "Bookkeeper", "Accountant"],
    "expenses:approve": ["CEO", "COO"],
    "pipeline:edit": ["CEO", "COO", "Project Manager", "Superintendent"],
    "projects:edit": ["CEO", "COO", "Project Manager"],
    "team:edit": ["CEO", "COO", "HR Manager"],
    "estimator:edit": ["CEO", "COO", "Estimator", "Project Manager"],
}


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE KNOWLEDGE PROMPT FOR ASSISTANT
# ─────────────────────────────────────────────────────────────────────────────

def get_ngm_hub_knowledge() -> str:
    """
    Generate a comprehensive knowledge prompt about NGM HUB
    to inject into the assistant instructions.
    """
    sections = []

    # Header
    sections.append("""
═══════════════════════════════════════════════════════════════════════════════
 NGM HUB - KNOWLEDGE BASE
═══════════════════════════════════════════════════════════════════════════════
NGM HUB es una plataforma web empresarial para gestión de proyectos de construcción.
Módulos disponibles:
""")

    # Modules summary
    for module_id, module in NGM_MODULES.items():
        sections.append(f"• {module['name']} ({module['url']}): {module['description']}")

    sections.append("\n")

    # Detailed module info
    for module_id, module in NGM_MODULES.items():
        sections.append(f"\n### {module['name'].upper()}")
        sections.append(f"URL: {module['url']}")
        sections.append(f"Descripción: {module['description']}")

        if module.get("features"):
            sections.append("\nFuncionalidades:")
            for feature in module["features"]:
                sections.append(f"  • {feature['name']}: {feature['how']}")

        if module.get("common_questions"):
            sections.append("\nPreguntas frecuentes:")
            for qa in module["common_questions"]:
                sections.append(f"  P: {qa['q']}")
                sections.append(f"  R: {qa['a']}")

    # Actions
    sections.append("""

═══════════════════════════════════════════════════════════════════════════════
 ACCIONES DISPONIBLES
═══════════════════════════════════════════════════════════════════════════════
Puedes ejecutar las siguientes acciones en el navegador del usuario.
IMPORTANTE: Antes de ejecutar una acción, verifica los permisos del usuario.

Cuando detectes una intención de acción, responde con JSON estructurado:
{
  "action": "action_id",
  "params": {...}
}

Acciones disponibles:
""")

    for action_id, action in NGM_ACTIONS.items():
        sections.append(f"• {action_id}: {action['description']} (requiere: {action['permission']})")

    # Permission handling
    sections.append("""

═══════════════════════════════════════════════════════════════════════════════
 MANEJO DE PERMISOS
═══════════════════════════════════════════════════════════════════════════════
Si el usuario NO tiene permiso para una acción:
1. Explica amablemente que no tiene acceso
2. Sugiere quién puede ayudarle (roles con ese permiso)
3. Ofrece enviar un mensaje a esa persona

Ejemplo de respuesta sin permiso:
"No tienes acceso para crear gastos. El Bookkeeper o el COO pueden ayudarte.
¿Quieres que le envíe un mensaje para solicitarlo?"

═══════════════════════════════════════════════════════════════════════════════
 REPORTE DE BUGS
═══════════════════════════════════════════════════════════════════════════════
Si el usuario reporta un problema o bug:
1. Pide detalles: qué intentaba hacer, qué pasó, qué esperaba
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
