# Usar imagen oficial de Python
FROM python:3.13-slim

# Instalar dependencias del sistema (runtime + build)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    libcairo2 \
    gcc g++ pkg-config libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar requirements primero para aprovechar cache de Docker
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc g++ pkg-config libcairo2-dev

# Copiar el resto del código
COPY . .

# Exponer puerto
EXPOSE 10000

# Comando para iniciar la aplicación
CMD gunicorn api.main:app -w ${GUNICORN_WORKERS:-1} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:10000 --timeout 120 --max-requests 5000 --max-requests-jitter 200
