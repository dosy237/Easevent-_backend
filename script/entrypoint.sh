#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "📡 Waiting for the database to be ready..."
sleep 5

# Migrations et fichiers statiques : uniquement dans le conteneur web.
# Les conteneurs celery / celery-beat partagent le même dossier : les lancer
# partout en même temps provoquerait des migrations concurrentes et un
# « collectstatic --clear » pendant que le site sert ces fichiers.
if [ "$1" = "gunicorn" ]; then
  echo "🛠️ Running migrations..."
  python manage.py migrate --noinput

  echo "🖼️ Demo event covers..."
  python manage.py fix_demo_covers || true

  echo "📦 Collecting static files..."
  python manage.py collectstatic --noinput --clear
else
  # Laisse au conteneur web le temps d'appliquer les migrations
  sleep 20
fi

# 🚀 Execute the container command (gunicorn, celery, etc.)
echo "🚀 Starting: $@"
exec "$@"