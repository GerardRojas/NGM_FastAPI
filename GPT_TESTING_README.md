# GPT Model Testing Guide

## Overview

Este script te permite probar manualmente los 4 niveles (tiers) de modelos GPT que usa el sistema de Andrew agent.

## Tiers de Modelos

### 1. Internal Tier - `gpt-5-nano`
- **Uso**: NLU, parsing, fuzzy matching
- **Casos**: Extracción de intent del usuario, parsing de contexto
- **Características**: Rápido, barato, sin output visible al usuario
- **Temperatura**: 0.0 (determinístico)

### 2. Chat Tier - `gpt-5-mini`
- **Uso**: Personality wrapping, conversación, smart layers
- **Casos**: Respuestas de Andrew con personalidad, confirmaciones
- **Características**: Conversacional, amigable pero profesional
- **Temperatura**: 0.4-0.7 (algo creativo)

### 3. Medium Tier - `gpt-5-1`
- **Uso**: OCR, categorización, brain routing, análisis
- **Casos**: Decisiones de routing, categorización de expenses
- **Características**: Balance entre costo y capacidad analítica
- **Temperatura**: 0.1-0.2 (mayormente determinístico)

### 4. Heavy Tier - `gpt-5-2`
- **Uso**: Reconciliación compleja, resolución de duplicados
- **Casos**: Mismatch protocol, correction mode con razonamiento profundo
- **Características**: Máxima capacidad analítica, más lento y costoso
- **Temperatura**: 0.2 (preciso pero con algo de flexibilidad)

## Instalación

1. Asegúrate de tener Python 3.8+:
```bash
python --version
```

2. Instala las dependencias necesarias:
```bash
pip install openai python-dotenv
```

3. Configura tu API key de OpenAI:
```bash
# En Windows (PowerShell)
$env:OPENAI_API_KEY="sk-your-key-here"

# En Windows (CMD)
set OPENAI_API_KEY=sk-your-key-here

# O crea un archivo .env en NGM_API:
echo OPENAI_API_KEY=sk-your-key-here > .env
```

## Uso

### Modo Interactivo (recomendado)
```bash
cd C:\Users\germa\Desktop\NGM_API
python test_gpt_models.py
```

El script te mostrará un menú:
```
Available tests:
  1. Internal tier (gpt-5-nano) - Context extraction
  2. Chat tier (gpt-5-mini) - Personality wrapper
  3. Medium tier (gpt-5-1) - Brain routing
  4. Heavy tier (gpt-5-2) - Mismatch reconciliation
  5. Run all tests
  0. Exit

Select test (0-5):
```

### Modo Automático (todos los tests)
```bash
python test_gpt_models.py --all
```

## Qué Esperar de Cada Test

### Test 1: Internal Tier
**Input**: Mensaje del usuario con contexto de recibo
```
"pago de drywall para trasher, mitad para este proyecto y mitad para main street"
```

**Output esperado**: JSON estructurado
```json
{
  "project_decision": "split",
  "split_projects": [
    {"name": "this_project", "portion": "half", "amount": null},
    {"name": "main street", "portion": "half", "amount": null}
  ],
  "category_hints": ["drywall"],
  "vendor_hint": null,
  "amount_hint": null,
  "date_hint": null
}
```

**Tokens típicos**: ~200 prompt, ~100 completion

---

### Test 2: Chat Tier
**Input**: Respuesta genérica del usuario
```
"Great! All the categories look correct. Please create the expenses."
```

**Output esperado**: Respuesta con personalidad de Andrew
```
Perfect! I'll create those expenses for you right now.

✓ All categories confirmed
✓ Creating expense entries...

I'll let you know as soon as they're in the system.
```

**Tokens típicos**: ~250 prompt, ~80 completion

---

### Test 3: Medium Tier
**Input**: Mensaje del usuario con attachment
```
"@Andrew process this receipt"
```

**Output esperado**: Decisión de routing en JSON
```json
{
  "action": "function_call",
  "function": "process_receipt",
  "parameters": {},
  "ack_message": "Processing your receipt now..."
}
```

**Tokens típicos**: ~400 prompt, ~80 completion

---

### Test 4: Heavy Tier
**Input**: Caso complejo de mismatch
```
Receipt total: $1,048.05
Database total: $850.00
Difference: -$198.05
```

**Output esperado**: Análisis detallado con recomendación
```json
{
  "issue_type": "missing_items",
  "confidence": 85,
  "explanation": "Database expenses total $850 but receipt shows $1,048.05. The line items from OCR sum to ~$322, suggesting expenses were grouped/consolidated incorrectly.",
  "recommended_action": "flag_for_review",
  "suggested_correction": {
    "action": "create_missing_expenses",
    "details": "Create additional expense entries to match receipt line items, or adjust existing consolidated amounts"
  }
}
```

**Tokens típicos**: ~600 prompt, ~250 completion

## Personalización de Tests

Puedes modificar los prompts en el archivo `test_gpt_models.py` para probar diferentes escenarios:

```python
TESTS = {
    "internal": {
        "model": "gpt-5-nano",
        "prompt": "Tu prompt personalizado aquí...",
        "temperature": 0.0,
        "max_tokens": 250,
    },
    # ...
}
```

## Métricas a Observar

1. **Calidad de la respuesta**: ¿Sigue el formato esperado?
2. **Coherencia**: ¿Las decisiones tienen sentido?
3. **Tokens usados**: ¿Está dentro del rango esperado?
4. **Tiempo de respuesta**: ¿Qué tan rápido responde cada tier?

## Costos Estimados (por llamada)

Nota: Estos son estimados aproximados, verifica los precios actuales de OpenAI.

- **gpt-5-nano**: ~$0.0001 por llamada (muy barato)
- **gpt-5-mini**: ~$0.0005 por llamada (barato)
- **gpt-5-1**: ~$0.002 por llamada (moderado)
- **gpt-5-2**: ~$0.01 por llamada (costoso)

## Troubleshooting

### Error: "OPENAI_API_KEY not found"
```bash
# Verifica que la variable esté configurada
echo %OPENAI_API_KEY%  # Windows CMD
echo $env:OPENAI_API_KEY  # Windows PowerShell
```

### Error: "Invalid model"
Los nombres de modelo `gpt-5-nano`, `gpt-5-mini`, etc. son los que usa el sistema actualmente. Si OpenAI cambió los nombres de modelo, actualiza el script con los nombres correctos (ejemplo: `gpt-4o-mini`, `gpt-4o`, etc.)

### Error: Rate limit
Si haces muchas pruebas seguidas, puedes alcanzar el rate limit de OpenAI. Espera unos segundos entre tests.

## Próximos Pasos

Después de probar los modelos:

1. **Ajusta temperaturas**: Si las respuestas son muy variables o muy rígidas
2. **Modifica max_tokens**: Si las respuestas se cortan o son muy largas
3. **Cambia prompts**: Para mejorar la precisión en casos específicos
4. **Compara modelos**: Prueba diferentes modelos para ver cuál funciona mejor para cada caso

## Referencias

- [Memoria del proyecto](../.claude/memory/MEMORY.md) - Ver sección "GPT Model Tier System"
- [Agent Brain código](./api/services/agent_brain.py) - Implementación completa
- [Andrew Personas](./api/services/agent_personas.py) - Configuración de personalidad
