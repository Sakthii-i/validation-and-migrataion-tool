FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (needed for snowflake / crypto)
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better caching)
COPY validation_tool/requirements.txt ./requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY validation_tool ./validation_tool

# Expose Streamlit port (API uses 8000 via compose)
EXPOSE 8501

# Streamlit config
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Default to UI (docker-compose overrides command per service)
CMD ["streamlit", "run", "validation_tool/app.py"]

