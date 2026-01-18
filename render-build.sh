#!/bin/bash
# Script de build para Render

# Instalar poppler-utils (necesario para pdf2image)
apt-get update
apt-get install -y poppler-utils

# Instalar dependencias de Python
pip install --upgrade pip
pip install -r requirements.txt
