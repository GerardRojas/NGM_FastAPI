# Usar imagen oficial de Python
FROM python:3.13-slim

# Instalar dependencias del sistema (incluyendo poppler-utils)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar requirements primero para aprovechar cache de Docker
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Exponer puerto
EXPOSE 10000

# Comando para iniciar la aplicación
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "10000"]
