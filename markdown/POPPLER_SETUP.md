# Instalación de Poppler para Windows

## ¿Qué es Poppler?

Poppler es una librería que permite convertir archivos PDF a imágenes. Es necesaria para que `pdf2image` funcione correctamente en Python.

---

## Pasos para Instalar Poppler en Windows

### Método 1: Descarga Manual (Recomendado)

#### 1. Descargar Poppler

Descarga la versión precompilada para Windows:

**Opción A - Desde GitHub (Recomendado):**
- URL: https://github.com/oschwartz10612/poppler-windows/releases/
- Descarga el archivo **Release-XX.XX.X-0.zip** (la última versión disponible)
- Ejemplo: `Release-24.08.0-0.zip`

**Opción B - Desde el mirror alternativo:**
- URL: https://github.com/Belval/pdf2image#windows
- Sigue el link a las releases

#### 2. Extraer el Archivo

1. Una vez descargado, extrae el archivo ZIP
2. Mueve la carpeta extraída a una ubicación permanente
   - Ejemplo: `C:\Program Files\poppler-24.08.0\`
   - O: `C:\poppler\`

#### 3. Agregar a PATH

**Opción A - Interfaz Gráfica:**

1. Abre "Este equipo" o "Mi PC"
2. Click derecho → "Propiedades"
3. Click en "Configuración avanzada del sistema"
4. Click en "Variables de entorno"
5. En "Variables del sistema", busca la variable `Path`
6. Click en "Editar"
7. Click en "Nuevo"
8. Agrega la ruta a la carpeta `bin` de poppler:
   ```
   C:\Program Files\poppler-24.08.0\Library\bin
   ```
   (Ajusta la versión según la que descargaste)
9. Click "Aceptar" en todas las ventanas

**Opción B - Línea de Comandos (PowerShell como Admin):**

```powershell
# Reemplaza la ruta con donde extrajiste poppler
$popplerPath = "C:\Program Files\poppler-24.08.0\Library\bin"
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$popplerPath", [EnvironmentVariableTarget]::Machine)
```

#### 4. Verificar la Instalación

Abre una **nueva** terminal (PowerShell o CMD) y ejecuta:

```bash
pdftoppm -v
```

Deberías ver la versión de poppler instalada. Por ejemplo:
```
pdftoppm version 24.08.0
```

Si ves esto, ¡poppler está instalado correctamente! ✅

---

### Método 2: Usando Chocolatey (Si ya tienes Chocolatey instalado)

Si ya tienes Chocolatey instalado, puedes instalar poppler con un solo comando:

```bash
choco install poppler
```

---

## Configuración Alternativa (Sin PATH)

Si no quieres modificar el PATH del sistema, puedes especificar la ruta de poppler directamente en el código Python:

**Actualiza `expenses.py` línea 504:**

```python
# En lugar de:
images = convert_from_bytes(file_content, first_page=1, last_page=1, dpi=200)

# Usa:
images = convert_from_bytes(
    file_content,
    first_page=1,
    last_page=1,
    dpi=200,
    poppler_path=r'C:\Program Files\poppler-24.08.0\Library\bin'  # Ajusta la ruta
)
```

---

## Verificación Final

Después de instalar poppler, verifica que el backend funcione:

### 1. Instalar dependencias de Python

```bash
cd C:\Users\germa\Desktop\NGM_API
pip install -r requirements.txt
```

### 2. Probar en Python

Abre Python y ejecuta:

```python
from pdf2image import convert_from_path
print("✅ pdf2image instalado correctamente")
```

Si no hay errores, ¡todo está listo!

---

## Problemas Comunes

### Error: "Unable to get page count. Is poppler installed?"

**Solución:**
- Verifica que agregaste la ruta correcta al PATH
- Asegúrate de apuntar a la carpeta `bin` dentro de poppler
- Reinicia la terminal después de agregar al PATH

### Error: "FileNotFoundError: [WinError 2]"

**Solución:**
- Poppler no está en el PATH
- Usa el método alternativo especificando `poppler_path` en el código

### pdftoppm no se reconoce como comando

**Solución:**
- Reinicia la terminal
- Verifica que la ruta en PATH apunte a la carpeta `Library\bin` (no solo a la carpeta raíz)

---

## Resumen Rápido

```bash
# 1. Descargar poppler
https://github.com/oschwartz10612/poppler-windows/releases/

# 2. Extraer a:
C:\Program Files\poppler-XX.XX.X\

# 3. Agregar al PATH:
C:\Program Files\poppler-XX.XX.X\Library\bin

# 4. Verificar (en nueva terminal):
pdftoppm -v

# 5. Instalar dependencias Python:
pip install -r requirements.txt
```

---

**Última actualización:** 2025-01-17
