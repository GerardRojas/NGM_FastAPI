# Configuración de Deploy en Render

## Variables de Entorno Requeridas

En el dashboard de Render, ve a **Environment** y agrega estas variables:

```
SUPABASE_URL=https://frpshidpuazlqfxodrbs.supabase.co
SUPABASE_KEY=sb_publishable_ZIGuV1t4RA4WN_SpHfyZwg_JSa5VMrP
SUPABASE_DB_URL=postgresql://postgres:0XSeNdWO8Lzyrmgg@db.frpshidpuazlqfxodrbs.supabase.co:5432/postgres
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZycHNoaWRwdWF6bHFmeG9kcmJzIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NTA1NjI4OCwiZXhwIjoyMDgwNjMyMjg4fQ.76fv0Rfuudpz_4XeoUlgZXSllSnUqql8iygTfMiaEDg
OPENAI_API_KEY=<TU-OPENAI-API-KEY-AQUI>
```

**IMPORTANTE:** Reemplaza `<TU-OPENAI-API-KEY-AQUI>` con tu API key real de OpenAI.

## Configuración de Build

### Opción 1: Usar el script de build (Recomendado)

**Build Command:**
```bash
bash render-build.sh
```

**Start Command:**
```bash
uvicorn main:app --host 0.0.0.0 --port 10000
```

### Opción 2: Build command manual

Si el script no funciona, usa este comando directo:

**Build Command:**
```bash
apt-get update && apt-get install -y poppler-utils && pip install --upgrade pip && pip install -r requirements.txt
```

**Start Command:**
```bash
uvicorn main:app --host 0.0.0.0 --port 10000
```

## Verificación Post-Deploy

Después del deploy, verifica que todo funciona:

1. **Verifica que el servicio está corriendo:**
   ```
   https://tu-servicio.onrender.com/
   ```

2. **Prueba el endpoint de parse-receipt:**
   ```bash
   curl -X POST https://tu-servicio.onrender.com/expenses/parse-receipt \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -F "file=@receipt.jpg"
   ```

3. **Verifica los logs en Render** para confirmar que:
   - Poppler se instaló correctamente
   - Todas las dependencias de Python se instalaron
   - No hay errores relacionados con OpenAI API key

## Compatibilidad de Versiones

Todas las versiones en `requirements.txt` son compatibles con Render:

- **Python**: Render usa Python 3.11+ por defecto (compatible)
- **Pillow**: Versión 12.1.0+ tiene wheels pre-compilados para Linux
- **pdf2image**: Compatible con poppler-utils de apt
- **openai**: Última versión estable
- **FastAPI, Supabase, etc.**: Todas compatibles

## Solución de Problemas

### Error: "Poppler not found"

Si ves este error, verifica que el build command instaló poppler:

```bash
which pdftoppm
```

Debe retornar `/usr/bin/pdftoppm`

### Error: "OpenAI API key not configured"

Asegúrate de que agregaste `OPENAI_API_KEY` en las variables de entorno de Render.

### Error de permisos con apt-get

Render puede tener restricciones en algunos planes. Si `apt-get` falla, contacta soporte de Render o usa un plan que permita custom build commands.

## Notas

- El código detecta automáticamente el sistema operativo (Windows vs Linux)
- En Windows usa: `C:\poppler\poppler-24.08.0\Library\bin`
- En Linux usa: poppler-utils del sistema (en PATH)
- No necesitas cambiar nada en el código para que funcione en ambos entornos
