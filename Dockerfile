FROM python:3.11-slim

WORKDIR /app

# Ensure Python can import modules from the project root (/app)
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Use shell mode to dynamically accept Render's $PORT environment variable
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
