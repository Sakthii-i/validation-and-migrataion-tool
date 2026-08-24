FROM python:3.11-slim

WORKDIR /app

# Prevent Python output buffering and set module search path
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt ./requirements.txt

# Copy all application files to /app
COPY . .
RUN ls -la /app && find /app -iname "validation_tool*"

EXPOSE 8000

# Run Uvicorn pointing directly to api.main:app
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
