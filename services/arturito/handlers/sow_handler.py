# services/arturito/handlers/sow_handler.py
# ================================
# Handler: Scope of Work (SOW)
# ================================
# Migrado desde HandleSOW.gs

from typing import Dict, Any, Optional
import os
from openai import OpenAI
from ..persona import get_persona_prompt


def handle_scope_of_work(
    request: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Responde consultas sobre el Scope of Work de un proyecto.

    En la versión de Apps Script, esto:
    1. Busca el documento SOW del proyecto en Drive
    2. Extrae el contenido del documento
    3. Usa GPT para responder la pregunta basándose en el SOW

    Args:
        request: {intent, entities: {project?, question?}, raw_text}
        context: {user, space_id, space_name}

    Returns:
        Dict con text/data y action
    """
    entities = request.get("entities", {})
    ctx = context or {}
    space_id = ctx.get("space_id", "default")

    project = entities.get("project", "").strip()
    question = entities.get("question", "").strip() or request.get("raw_text", "")

    # Fallback: usar nombre del espacio si no hay proyecto
    if not project:
        project = ctx.get("space_name", "")

    if not project or project.lower() in ["default", "general"]:
        return {
            "ok": False,
            "text": "⚠️ ¿De qué proyecto quieres consultar el Scope of Work?",
            "action": "missing_project"
        }

    # TODO: Implementar búsqueda real del SOW
    # Opciones:
    # 1. Google Drive API para buscar el documento
    # 2. Supabase para almacenar contenido del SOW
    # 3. Vector store (embeddings) para búsqueda semántica

    # Por ahora: respuesta stub
    return {
        "ok": True,
        "text": f"📝 Consultando el Scope of Work de *{project}*...\n\n⏳ Esta función está en desarrollo. Pronto podré responder preguntas sobre el alcance de obra.",
        "action": "query_sow",
        "data": {
            "project": project,
            "question": question,
            "status": "pending_implementation"
        }
    }


async def get_sow_content(project_name: str) -> Optional[str]:
    """
    Obtiene el contenido del SOW de un proyecto.

    TODO: Implementar con una de estas estrategias:

    Opción A - Google Drive API:
    ```python
    from googleapiclient.discovery import build

    drive = build('drive', 'v3', credentials=creds)
    # Buscar documento por nombre
    results = drive.files().list(
        q=f"name contains '{project_name}' and name contains 'SOW'",
        fields="files(id, name)"
    ).execute()

    if results.get('files'):
        doc_id = results['files'][0]['id']
        # Exportar como texto
        content = drive.files().export(
            fileId=doc_id,
            mimeType='text/plain'
        ).execute()
        return content.decode('utf-8')
    ```

    Opción B - Supabase con contenido pre-indexado:
    ```python
    result = supabase.table("project_sow").select("content").eq("project", project_name).execute()
    if result.data:
        return result.data[0]['content']
    ```

    Opción C - Vector store para búsqueda semántica:
    ```python
    # Usar embeddings de OpenAI + Supabase pgvector
    ```
    """
    return None


async def answer_sow_question(
    project_name: str,
    question: str,
    sow_content: str,
    space_id: str = "default"
) -> str:
    """
    Usa GPT para responder una pregunta sobre el SOW.

    Args:
        project_name: Nombre del proyecto
        question: Pregunta del usuario
        sow_content: Contenido del documento SOW
        space_id: ID del espacio para personalidad

    Returns:
        Respuesta generada por GPT
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "⚠️ OpenAI no está configurado."

    client = OpenAI(api_key=api_key)

    system_prompt = f"""{get_persona_prompt(space_id)}

Tienes acceso al Scope of Work (SOW) del proyecto "{project_name}".
Responde la pregunta del usuario basándote ÚNICAMENTE en el contenido del SOW.
Si la información no está en el documento, indica que no se menciona en el alcance.

CONTENIDO DEL SOW:
{sow_content}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            temperature=0.3,
            max_completion_tokens=1000
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"⚠️ Error consultando el SOW: {str(e)}"
