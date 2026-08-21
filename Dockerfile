FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (needed for snowflake / crypto)
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better caching)
COPY requirements.txt ./requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Default to running the API (docker-compose can override per service)
CMD ["python", "-m", "uvicorn", "validation_tool.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

