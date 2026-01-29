# services/arturito/nlu.py
# ================================
# Natural Language Understanding para Arturito
# ================================
# Migrado desde Interpretar.gs

import re
import json
import os
from typing import Dict, Any, Optional
from openai import OpenAI
from .persona import get_persona_prompt

# ================================
# Tipos de Intent soportados
# ================================

VALID_INTENTS = [
    "BUDGET_VS_ACTUALS",    # Reporte BVA global
    "CONSULTA_ESPECIFICA",  # Consulta sobre categoría específica del BVA
    "SCOPE_OF_WORK",        # Consultas sobre SOW
    "INFO",                 # Información del sistema / ayuda
    "SET_PERSONALITY",      # Cambiar nivel de sarcasmo
    "SMALL_TALK",           # Conversación general
    "GREETING",             # Saludos
    "NGM_HELP",             # Preguntas sobre cómo usar NGM Hub
    "NGM_ACTION",           # Ejecutar acciones en NGM Hub (navegar, abrir modal)
    "COPILOT",              # Comandos copilot para controlar la pagina actual
    "REPORT_BUG",           # Reportar un bug o problema
    "EXPENSE_REMINDER",     # Recordatorio de gastos pendientes a autorizadores
    "LIST_PROJECTS",        # Listar proyectos activos
    "LIST_VENDORS",         # Listar vendors/proveedores
    "CREATE_VENDOR",        # Crear nuevo vendor/proveedor
    "CREATE_PROJECT",       # Crear nuevo proyecto
    "SEARCH_EXPENSES",      # Buscar gastos por criterios
    "UNKNOWN",              # No clasificado
]


# ================================
# Interpretación Local (regex rápido)
# ================================

def interpret_local(text: str) -> Optional[Dict[str, Any]]:
    """
    Intenta clasificar el mensaje con reglas locales antes de llamar a GPT.
    Retorna None si no hay match → se delega a GPT.
    """
    t = text.strip().lower()

    # Sarcasmo / Personalidad: "sarcasmo 3", "personalidad 5"
    match_sar = re.search(r'\b(sarcasmo|personalidad|personality)\s*(\d)\b', t)
    if match_sar:
        level = int(match_sar.group(2))
        return {
            "intent": "SET_PERSONALITY",
            "entities": {"level": min(5, max(1, level))},
            "confidence": 1.0,
            "source": "local"
        }

    # BVA con proyecto: "bva del rio", "budgetvsactuals arthur neal"
    match_bva = re.match(r'^(bva|budgetvsactuals)\s+(.+)$', t)
    if match_bva:
        return {
            "intent": "BUDGET_VS_ACTUALS",
            "entities": {"project": match_bva.group(2).strip()},
            "confidence": 1.0,
            "source": "local"
        }

    # BVA sin proyecto: "bva", "reporte bva", "generar bva", "dame el bva", etc.
    bva_no_project_patterns = [
        r'^(bva|budgetvsactuals)$',  # Solo "bva"
        r'^(reporte|report|genera|generar|dame|muéstrame|muestrame|quiero)\s+(el\s+)?(bva|budget\s*vs\s*actuals)',
        r'^(bva|budget\s*vs\s*actuals)\s+(report|reporte)?$',
        r'(necesito|quiero|dame)\s+(un\s+)?(reporte\s+)?(bva|budget\s*vs\s*actuals)',
    ]
    for pattern in bva_no_project_patterns:
        if re.search(pattern, t):
            return {
                "intent": "BUDGET_VS_ACTUALS",
                "entities": {"project": None},  # No project specified
                "confidence": 1.0,
                "source": "local"
            }

    # ================================
    # Consulta específica de categoría del BVA
    # ================================
    # Patrones: "cuanto tengo para ventanas en del rio", "cuanto hemos gastado en hvac en thrasher"

    # Patrón 1: "cuanto [tengo/hay/queda] [disponible/de presupuesto] para/de [categoria] en [proyecto]"
    consulta_pattern1 = re.search(
        r'(?:cuánto|cuanto|cuál|cual|qué|que)\s+(?:tengo|hay|queda|tenemos|tienen)?\s*'
        r'(?:disponible|de\s+presupuesto|de\s+budget|gastado|usado)?\s*'
        r'(?:para|de|en)\s+(\w+(?:\s+\w+)?)\s+'
        r'(?:en|del?|para)\s+(.+?)(?:\?|$)',
        t
    )
    if consulta_pattern1:
        category = consulta_pattern1.group(1).strip()
        project = consulta_pattern1.group(2).strip()
        return {
            "intent": "CONSULTA_ESPECIFICA",
            "entities": {"topic": category, "project": project},
            "confidence": 0.95,
            "source": "local"
        }

    # Patrón 2: "[categoria] en [proyecto]" al final de pregunta
    consulta_pattern2 = re.search(
        r'(?:presupuesto|budget|gastado|disponible|balance)\s+'
        r'(?:de|para|en)\s+(\w+(?:\s+\w+)?)\s+'
        r'(?:en|del?|para)\s+(.+?)(?:\?|$)',
        t
    )
    if consulta_pattern2:
        category = consulta_pattern2.group(1).strip()
        project = consulta_pattern2.group(2).strip()
        return {
            "intent": "CONSULTA_ESPECIFICA",
            "entities": {"topic": category, "project": project},
            "confidence": 0.9,
            "source": "local"
        }

    # Patrón 3: "cuanto hemos gastado en [categoria] en [proyecto]"
    consulta_pattern3 = re.search(
        r'(?:cuánto|cuanto)\s+(?:hemos|han|he)\s+(?:gastado|usado|invertido)\s+'
        r'(?:en|para|de)\s+(\w+(?:\s+\w+)?)\s+'
        r'(?:en|del?|para)\s+(.+?)(?:\?|$)',
        t
    )
    if consulta_pattern3:
        category = consulta_pattern3.group(1).strip()
        project = consulta_pattern3.group(2).strip()
        return {
            "intent": "CONSULTA_ESPECIFICA",
            "entities": {"topic": category, "project": project},
            "confidence": 0.95,
            "source": "local"
        }

    # Patrón 4: Consulta de categoría sin proyecto (preguntar después)
    consulta_sin_proyecto = re.search(
        r'(?:cuánto|cuanto|cuál|cual)\s+(?:tengo|hay|queda|tenemos)?\s*'
        r'(?:disponible|de\s+presupuesto|gastado)?\s*'
        r'(?:para|de|en)\s+(\w+(?:\s+\w+)?)(?:\?|$)',
        t
    )
    # Solo si parece una consulta de presupuesto específica (no general)
    if consulta_sin_proyecto:
        category = consulta_sin_proyecto.group(1).strip().lower()
        # Verificar que sea una categoría de construcción, no una pregunta general
        construction_terms = [
            "ventanas", "windows", "hvac", "plomeria", "plumbing", "electricidad",
            "electrical", "framing", "drywall", "pintura", "paint", "piso", "flooring",
            "techo", "roof", "cocina", "kitchen", "baño", "bathroom", "puertas", "doors",
            "concreto", "concrete", "landscaping", "appliances", "insulation", "cabinets"
        ]
        if any(term in category for term in construction_terms):
            return {
                "intent": "CONSULTA_ESPECIFICA",
                "entities": {"topic": consulta_sin_proyecto.group(1).strip(), "project": None},
                "confidence": 0.85,
                "source": "local"
            }

    # Ayuda / Help
    if re.search(r'\b(ayuda|help|qué puedes hacer|what can you do)\b', t):
        return {
            "intent": "INFO",
            "entities": {},
            "confidence": 0.95,
            "source": "local"
        }

    # Saludos simples
    if re.match(r'^(hola|hi|hello|hey|buenos días|buenas tardes|good morning)[\s!.,]*$', t):
        return {
            "intent": "GREETING",
            "entities": {},
            "confidence": 0.9,
            "source": "local"
        }

    # Identidad
    if re.search(r'\b(quién eres|quien eres|who are you|qué eres)\b', t):
        return {
            "intent": "INFO",
            "entities": {"topic": "identity"},
            "confidence": 0.95,
            "source": "local"
        }

    # ================================
    # List Projects
    # ================================

    list_projects_patterns = [
        r'^(lista|listar?|mostrar?|muestra|ver|dame|cuales son)\s+(los\s+)?(proyectos|projects)',
        r'^(proyectos|projects)(\s+activos|\s+disponibles|\s+que\s+tenemos)?$',
        r'(qué|que|cuáles|cuales)\s+(proyectos|projects)\s+(tenemos|hay|existen|estan)',
        r'(dime|dame)\s+(los\s+)?(proyectos|projects)',
        r'(en\s+qué|en\s+que|cuáles|cuales)\s+(proyectos?)\s+(estamos|trabajamos|tenemos)',
    ]

    for pattern in list_projects_patterns:
        if re.search(pattern, t):
            return {
                "intent": "LIST_PROJECTS",
                "entities": {},
                "confidence": 0.95,
                "source": "local"
            }

    # ================================
    # List Vendors
    # ================================

    list_vendors_patterns = [
        r'^(lista|listar?|mostrar?|muestra|ver|dame|cuales son)\s+(los\s+)?(vendors?|proveedores?)',
        r'^(vendors?|proveedores?)(\s+activos|\s+disponibles|\s+que\s+tenemos)?$',
        r'(qué|que|cuáles|cuales)\s+(vendors?|proveedores?)\s+(tenemos|hay|existen|estan)',
        r'(dime|dame)\s+(los\s+)?(vendors?|proveedores?)',
        r'(con\s+qué|con\s+que|cuáles|cuales)\s+(vendors?|proveedores?)\s+(trabajamos|tenemos)',
    ]

    for pattern in list_vendors_patterns:
        if re.search(pattern, t):
            return {
                "intent": "LIST_VENDORS",
                "entities": {},
                "confidence": 0.95,
                "source": "local"
            }

    # ================================
    # Create Vendor
    # ================================

    # Pattern: "agregar vendor X", "crear vendor X", "nuevo vendor X", "añadir proveedor X"
    create_vendor_patterns = [
        r'(agregar?|añadir?|crear?|nuevo?)\s+(un\s+)?(vendor|proveedor)\s+(.+)',
        r'(registrar?|dar\s+de\s+alta)\s+(un\s+)?(vendor|proveedor)\s+(.+)',
    ]

    for pattern in create_vendor_patterns:
        match = re.search(pattern, t)
        if match:
            vendor_name = match.group(4).strip()
            # Clean up common trailing words
            vendor_name = re.sub(r'\s+(por\s+favor|please|pls)$', '', vendor_name, flags=re.IGNORECASE)
            if vendor_name:
                return {
                    "intent": "CREATE_VENDOR",
                    "entities": {"vendor_name": vendor_name},
                    "confidence": 0.95,
                    "source": "local"
                }

    # ================================
    # Create Project
    # ================================

    # Pattern: "agregar proyecto X", "crear proyecto X", "nuevo proyecto X"
    create_project_patterns = [
        r'(agregar?|añadir?|crear?|nuevo?)\s+(un\s+)?(proyecto|project)\s+(.+)',
        r'(registrar?|dar\s+de\s+alta)\s+(un\s+)?(proyecto|project)\s+(.+)',
    ]

    for pattern in create_project_patterns:
        match = re.search(pattern, t)
        if match:
            project_name = match.group(4).strip()
            # Clean up common trailing words
            project_name = re.sub(r'\s+(por\s+favor|please|pls)$', '', project_name, flags=re.IGNORECASE)
            if project_name:
                return {
                    "intent": "CREATE_PROJECT",
                    "entities": {"project_name": project_name},
                    "confidence": 0.95,
                    "source": "local"
                }

    # ================================
    # Search Expenses
    # ================================

    # Detectar si es una búsqueda de gastos
    search_expense_triggers = [
        r'(busca|buscar|encuentra|encontrar|dame|muestra|mostrar)\s+.*(gasto|expense|pago|payment)',
        r'(gasto|expense|pago|payment)\s+.*(de|por|para|a)\s+\$?\d+',
        r'(cuanto|cuánto)\s+(pagamos|gastamos|se\s+pagó|se\s+gasto)\s+',
        r'(hay\s+)?(algún|algun|un)\s+(gasto|expense|pago)',
        r'(gastos?|expenses?|pagos?)\s+(de|a|para|por)\s+',
    ]

    is_expense_search = any(re.search(p, t) for p in search_expense_triggers)

    if is_expense_search:
        entities = {}

        # Extraer monto: $1000, 1000 dlls, 1,000 dollars, etc.
        amount_patterns = [
            r'\$\s*([\d,]+(?:\.\d{2})?)',  # $1000 or $1,000.00
            r'([\d,]+(?:\.\d{2})?)\s*(?:dlls?|dollars?|usd|pesos?)',  # 1000 dlls
            r'(?:de|por)\s*([\d,]+(?:\.\d{2})?)\s*(?:dlls?|dollars?|usd)?',  # de 1000
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(',', '')
                try:
                    entities["amount"] = float(amount_str)
                except ValueError:
                    pass
                break

        # Extraer vendor: "a Xvendor", "de Xvendor", "pagado a Xvendor"
        vendor_patterns = [
            r'(?:a|de|para|pagado\s+a|se\s+le\s+pagó\s+a)\s+([A-Z][A-Za-z0-9\s&\'-]+?)(?:\s+(?:para|por|de|en|\$|$))',
            r'(?:vendor|proveedor)\s+([A-Z][A-Za-z0-9\s&\'-]+?)(?:\s+(?:para|por|de|en|\$|$))',
        ]
        for pattern in vendor_patterns:
            match = re.search(pattern, text.strip())  # Use original case
            if match:
                vendor_name = match.group(1).strip()
                # Clean trailing words
                vendor_name = re.sub(r'\s+(para|por|de|en)$', '', vendor_name, flags=re.IGNORECASE)
                if vendor_name and len(vendor_name) > 1:
                    entities["vendor"] = vendor_name
                break

        # Extraer categoría/cuenta: "para rough framing", "de hvac", "en electrical"
        category_patterns = [
            r'(?:para|de|en|por)\s+(rough\s+framing|framing|hvac|plumbing|electrical|drywall|paint|flooring|roofing|concrete|landscaping|appliances|insulation|cabinets|windows|doors|kitchen|bathroom)',
            r'(?:categoria|category|cuenta|account)\s+([A-Za-z\s]+?)(?:\s+(?:de|en|para|\$|$))',
        ]
        for pattern in category_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                entities["category"] = match.group(1).strip()
                break

        # Extraer proyecto si se menciona
        project_patterns = [
            r'(?:en|del?|para|proyecto)\s+([A-Z][A-Za-z\s]+?)(?:\s+(?:de|para|por|\$|$))',
        ]
        for pattern in project_patterns:
            match = re.search(pattern, text.strip())  # Use original case
            if match:
                proj = match.group(1).strip()
                # Avoid matching common words
                if proj.lower() not in ['rough', 'framing', 'hvac', 'electrical', 'el', 'la', 'los', 'las']:
                    entities["project"] = proj
                break

        if entities:  # Only return if we extracted something useful
            return {
                "intent": "SEARCH_EXPENSES",
                "entities": entities,
                "confidence": 0.9,
                "source": "local",
                "raw_text": text,
            }

    # ================================
    # NGM Hub Help Questions
    # ================================

    # Preguntas sobre dónde/cómo ver algo
    ngm_help_patterns = [
        (r'(dónde|donde|como|cómo)\s+(puedo\s+)?(ver|encontrar|buscar)\s+(.+)', 'view'),
        (r'(dónde|donde)\s+(están?|estan?)\s+(.+)', 'location'),
        (r'(cómo|como)\s+(funciona|uso|utilizo|trabajo con)\s+(.+)', 'howto'),
        (r'(qué|que)\s+(es|significa|hace)\s+(.+?)\s+(en|del?)\s+(ngm|hub|sistema)', 'definition'),
        (r'(explica|explicame|dime)\s+(sobre|acerca|de)\s+(.+)', 'explain'),
    ]

    for pattern, query_type in ngm_help_patterns:
        match = re.search(pattern, t)
        if match:
            # Extract the topic from the match
            topic = match.group(match.lastindex) if match.lastindex else ""
            return {
                "intent": "NGM_HELP",
                "entities": {"query_type": query_type, "topic": topic.strip()},
                "confidence": 0.9,
                "source": "local"
            }

    # Preguntas específicas por módulo
    module_keywords = {
        "expenses": ["gastos", "expenses", "facturas", "invoices", "recibos", "receipts"],
        "pipeline": ["tareas", "tasks", "pipeline", "proyectos activos"],
        "projects": ["proyectos", "projects", "proyecto"],
        "vendors": ["vendors", "proveedores", "vendor"],
        "accounts": ["cuentas", "accounts", "cuenta"],
        "budgets": ["presupuestos", "budgets", "budget"],
        "team": ["equipo", "team", "usuarios", "users"],
    }

    for module, keywords in module_keywords.items():
        for kw in keywords:
            if kw in t and re.search(r'(dónde|donde|cómo|como|qué|que|ver|encontrar)', t):
                return {
                    "intent": "NGM_HELP",
                    "entities": {"module": module, "topic": t},
                    "confidence": 0.85,
                    "source": "local"
                }

    # ================================
    # NGM Hub Actions
    # ================================

    action_patterns = [
        # Expenses actions
        (r'(agregar?|añadir?|crear?|nuevo?)\s+(un\s+)?(gasto|expense)', 'open_add_expense'),
        (r'(escanear?|scanear?|scan)\s+(un\s+)?(recibo|receipt)', 'open_scan_receipt'),
        (r'(subir?|upload)\s+(un\s+)?(recibo|receipt|factura)', 'open_scan_receipt'),
        # Navigation
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(gastos|expenses)', 'navigate_expenses'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(pipeline|tareas)', 'navigate_pipeline'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(proyectos|projects)', 'navigate_projects'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(vendors|proveedores)', 'navigate_vendors'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(cuentas|accounts)', 'navigate_accounts'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(equipo|team)', 'navigate_team'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(presupuestos|budgets)', 'navigate_budgets'),
        (r'(llevar?me|ir|abrir?|navegar?)\s+(a\s+)?(reportes|reports|reporting)', 'navigate_reporting'),
        # Pipeline actions
        (r'(crear?|agregar?|nuevo?)\s+(una?\s+)?(tarea|task)', 'open_add_task'),
    ]

    for pattern, action in action_patterns:
        if re.search(pattern, t):
            return {
                "intent": "NGM_ACTION",
                "entities": {"action": action},
                "confidence": 0.95,
                "source": "local"
            }

    # ================================
    # Copilot Commands (page-specific filters/actions)
    # ================================

    copilot_patterns = [
        # Filtering commands
        (r'(mostrar?me|muestrame|muestra|show|ver)\s+(solo\s+)?(los?\s+)?(gastos?|tareas?|proyectos?|usuarios?).*(pendiente|autorizado|completad|activo|progreso)', 'filter'),
        (r'(filtrar?|filter)\s+(por\s+)?(.+)', 'filter'),
        (r'(solo\s+)(pendientes?|autorizados?|completados?|activos?|en\s+progreso)', 'filter'),
        # Sorting commands
        (r'(ordenar?|sort)\s+(por\s+)?(.+)', 'sort'),
        (r'(de\s+)?(mayor|menor)\s+a\s+(mayor|menor)', 'sort'),
        (r'(mas\s+)?(recientes?|antiguos?|nuevos?)\s+(primero)?', 'sort'),
        # Expand/Collapse
        (r'(expandir?|abrir?|mostrar?)\s+(todo|todos|todas|all|detalles)', 'expand'),
        (r'(colapsar?|cerrar?|ocultar?|contraer)\s+(todo|todos|todas|all|detalles)', 'collapse'),
        # Clear filters
        (r'(limpiar?|quitar?|remover?|reset|clear)\s+(los?\s+)?(filtros?|filters?)', 'clear_filters'),
        # Search
        (r'(buscar?|search|encontrar?|find)\s+(.+)', 'search'),
    ]

    for pattern, command_type in copilot_patterns:
        match = re.search(pattern, t)
        if match:
            return {
                "intent": "COPILOT",
                "entities": {
                    "command_type": command_type,
                    "raw_command": t,
                },
                "confidence": 0.9,
                "source": "local"
            }

    # ================================
    # Bug Reports
    # ================================

    bug_patterns = [
        r'(reportar?|report)\s+(un\s+)?(bug|error|fallo|problema)',
        r'(encontré|encontre|hay)\s+(un\s+)?(bug|error|fallo|problema)',
        r'(algo|esto)\s+(está|esta)\s+(fallando|roto|mal)',
        r'(no\s+funciona|no\s+sirve|está\s+roto|esta\s+roto)',
        r'(tengo\s+un\s+)(problema|issue|error)',
    ]

    for pattern in bug_patterns:
        if re.search(pattern, t):
            return {
                "intent": "REPORT_BUG",
                "entities": {"description": t},
                "confidence": 0.9,
                "source": "local"
            }

    # ================================
    # Expense Authorization Reminders
    # ================================

    expense_reminder_patterns = [
        r'(gastos?|expenses?)\s+(sin\s+)?(autorizar|autorización|autorizacion|pendientes?)',
        r'(muchos?|demasiados?)\s+(gastos?|expenses?)\s+(sin\s+)?(autorizar|pendientes?)',
        r'(recordar?|recordatorio|reminder)\s+(de\s+)?(gastos?|expenses?)',
        r'(enviar?|mandar?)\s+(recordatorio|reminder)\s+(de\s+)?(gastos?|autorizacion)',
        r'(avisar?|notificar?)\s+(a\s+)?(los?\s+)?(autorizadores?|aprobadores?)',
        r'(pendientes?\s+de\s+)(autorizacion|autorización|aprobar)',
        r'(nadie\s+autoriza|sin\s+aprobar)\s+(los?\s+)?(gastos?)?',
        r'(hay\s+)(muchos?\s+)?(gastos?\s+)(esperando|pendientes?|sin\s+autorizar)',
    ]

    for pattern in expense_reminder_patterns:
        if re.search(pattern, t):
            return {
                "intent": "EXPENSE_REMINDER",
                "entities": {"message": t},
                "confidence": 0.95,
                "source": "local"
            }

    return None


# ================================
# Interpretación con GPT
# ================================

NLU_SYSTEM_PROMPT = """Eres un PARSER ESTRICTO. Clasifica el mensaje del usuario en UNO de estos intents:

1) BUDGET_VS_ACTUALS
   - Consultas GENERALES del proyecto: total budget, total actuals, balance global.
   - Se activa cuando NO hay trade/tema específico o piden "Budget vs Actuals", "global", "resumen".
   - Extrae: 'project' si aparece.

2) CONSULTA_ESPECIFICA
   - Pregunta sobre un grupo/categoría/cuenta ESPECÍFICA del BVA (HVAC, framing, windows, plumbing, etc.).
   - Mide métricas: budget, actuals, gastado, disponible, diferencia.
   - Extrae: 'topic' (trade), 'project' (si aparece).
   - REGLA: Si aparece trade + budget/actuals/gastado → SIEMPRE es CONSULTA_ESPECIFICA.

3) SCOPE_OF_WORK
   - Consultas sobre el alcance de obra (SOW).
   - Señales: incluye/excluye, NIC/by owner, qué contempla, qué dice el SOW.
   - Extrae: 'project', 'question'.

4) INFO
   - Preguntas sobre funciones o capacidades del sistema (Arturito).
   - Ayuda, quién eres, qué puedes hacer como bot.

5) NGM_HELP
   - Preguntas sobre cómo usar NGM Hub o sus módulos.
   - Señales: "dónde puedo ver...", "cómo funciona...", "dónde están los...", "qué es X en el sistema".
   - Preguntas sobre ubicación de funciones: gastos, facturas, tareas, proyectos, etc.
   - Extrae: 'module' (expenses, pipeline, projects, vendors, etc.), 'topic' (qué busca).

6) NGM_ACTION
   - Usuario quiere EJECUTAR una acción en el sistema (navegar, abrir modales).
   - Señales: "agregar gasto", "crear tarea", "llévame a...", "abrir...", "escanear recibo".
   - Extrae: 'action' (nombre de la acción a ejecutar).

7) COPILOT
   - Usuario quiere controlar la PAGINA ACTUAL: filtrar, ordenar, buscar, expandir/colapsar.
   - Señales: "muestrame solo...", "filtrar por...", "ordenar por...", "buscar...", "expandir todo".
   - Comandos para la UI de la pagina actual sin necesidad de navegar.
   - Extrae: 'command_type' (filter, sort, search, expand, collapse, clear_filters), 'params' (parametros del comando).
   - Ejemplos: "muestrame solo gastos pendientes", "filtrar por proyecto Del Rio", "ordenar por fecha".

8) REPORT_BUG
   - Usuario quiere reportar un bug, error o problema.
   - Señales: "reportar bug", "encontré un error", "algo no funciona", "hay un problema".
   - Extrae: 'description' (descripción del problema).

9) EXPENSE_REMINDER
   - Usuario quiere enviar recordatorio a los autorizadores de gastos.
   - Señales: "gastos sin autorizar", "muchos gastos pendientes", "recordatorio de gastos", "avisar a los autorizadores".
   - Activar cuando el usuario se queja de gastos sin autorizar o pide enviar notificación/recordatorio.
   - Extrae: 'message' (mensaje original del usuario).

10) SMALL_TALK
   - Conversación general o dudas técnicas simples.
   - Saludos, chistes, preguntas no relacionadas con proyectos ni NGM Hub.

PRIORIDAD DE CLASIFICACIÓN:
- Si el usuario quiere CONTROLAR la pagina actual (filtrar, ordenar, buscar) → COPILOT
- Si el usuario pregunta sobre FUNCIONES del sistema NGM Hub → NGM_HELP
- Si el usuario quiere NAVEGAR o ABRIR modales → NGM_ACTION
- Si el usuario reporta un PROBLEMA → REPORT_BUG
- Si el usuario quiere ENVIAR RECORDATORIO de GASTOS pendientes → EXPENSE_REMINDER
- Si pregunta sobre datos de un PROYECTO (budget, actuals) → BUDGET_VS_ACTUALS o CONSULTA_ESPECIFICA
- Si pregunta sobre Arturito (el bot) → INFO

REGLAS DE RESPUESTA:
- Devuelve SOLO JSON válido, sin markdown ni explicaciones.
- Si el proyecto no se menciona claramente, usa null.
- Formato exacto:
{
  "intent": "INTENT_NAME",
  "entities": { "project": "...", "topic": "...", "module": "...", "action": "...", "description": "..." },
  "confidence": 0.85
}
"""


def interpret_with_gpt(text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Usa OpenAI para clasificar el intent cuando las reglas locales no matchean.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "error": "OPENAI_API_KEY not configured"
        }

    client = OpenAI(api_key=api_key)

    # Agregar contexto del espacio si existe
    context_info = ""
    if context:
        if context.get("space_name"):
            context_info = f"\n\nContexto: El usuario está en el espacio '{context['space_name']}'"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Modelo rápido y económico para NLU
            messages=[
                {"role": "system", "content": NLU_SYSTEM_PROMPT},
                {"role": "user", "content": f"Mensaje del usuario: {text}{context_info}"}
            ],
            temperature=0,
            max_tokens=200
        )

        raw_response = response.choices[0].message.content.strip()

        # Limpiar respuesta (quitar markdown fences si los hay)
        cleaned = raw_response
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        # Parsear JSON
        parsed = json.loads(cleaned)

        return {
            "intent": parsed.get("intent", "UNKNOWN").upper(),
            "entities": parsed.get("entities", {}),
            "confidence": parsed.get("confidence", 0.7),
            "source": "gpt"
        }

    except json.JSONDecodeError as e:
        return {
            "intent": "SMALL_TALK",
            "entities": {},
            "confidence": 0.5,
            "source": "gpt",
            "parse_error": str(e)
        }
    except Exception as e:
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "error": str(e)
        }


# ================================
# Pipeline Principal de Interpretación
# ================================

def interpret_message(text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Pipeline completo de interpretación:
    1. Intenta reglas locales (rápido, sin API)
    2. Si no hay match, usa GPT
    3. Normaliza y retorna resultado estructurado
    """
    if not text or not text.strip():
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "raw_text": ""
        }

    clean_text = text.strip()

    # 1. Intentar interpretación local
    local_result = interpret_local(clean_text)
    if local_result:
        local_result["raw_text"] = clean_text
        return local_result

    # 2. Delegar a GPT
    gpt_result = interpret_with_gpt(clean_text, context)
    gpt_result["raw_text"] = clean_text

    # 3. Inferir proyecto desde contexto si no se detectó
    if not gpt_result.get("entities", {}).get("project"):
        if context and context.get("space_name"):
            # Solo usar el nombre del espacio si parece un proyecto
            space_name = context["space_name"]
            if space_name and space_name.lower() not in ["default", "general", "random"]:
                gpt_result.setdefault("entities", {})["project"] = space_name
                gpt_result["project_inferred"] = True

    return gpt_result
