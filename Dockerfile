# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy project
COPY . /app/

# Make entrypoint.sh executable
RUN chmod +x /app/script/entrypoint.sh

# Use entrypoint script
ENTRYPOINT ["/app/script/entrypoint.sh"]

# Default command (can be overridden in docker-compose)
CMD ["gunicorn", "easevent.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "120"]
