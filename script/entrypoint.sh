#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "📡 Waiting for the database to be ready..."
sleep 5

echo "🛠️ Running migrations..."
python manage.py migrate --noinput

echo "📦 Collecting static files..."
python manage.py collectstatic --noinput --clear



# 🚀 Execute the container command (gunicorn, celery, etc.)
echo "🚀 Starting: $@"
exec "$@"