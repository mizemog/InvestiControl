#!/bin/bash

# 1. Esperar a que PostgreSQL esté listo
echo "⏳ Esperando a que PostgreSQL acepte conexiones..."
while ! nc -z $DB_HOST $DB_PORT; do
  sleep 0.5
done
echo "✅ PostgreSQL está listo!"

# 2. GENERACIÓN AUTOMÁTICA DE PLANOS (Plug & Play Real)
# Esto crea los archivos .py de migración dentro del contenedor si no existen
echo "🛠️ Generando planos de base de datos (makemigrations)..."
python manage.py makemigrations gestion --noinput

# 3. Aplicar las tablas
echo "🏗️ Construyendo tablas en PostgreSQL (migrate)..."
python manage.py migrate --noinput

# 4. Cargar datos de prueba
echo "🌱 Sembrando datos iniciales y plantillas (seed_data)..."
python manage.py seed_data

# 5. Archivos estáticos
echo "🎨 Preparando archivos estáticos..."
python manage.py collectstatic --noinput

# 6. Iniciar el servidor
echo "🚀 ¡SISTEMA ONLINE! Accede a http://localhost:8000"
python manage.py runserver 0.0.0.0:8000