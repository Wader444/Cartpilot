# CartPilot – Hackathon Dockerfile
# Runs both FastAPI backend (port 8000) and Streamlit frontend (public port $PORT)
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY . .

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    CARTPILOT_ENABLE_FAULTS="true" \
    CARTPILOT_FIXED_NOW="17:00" \
    USE_MOCK="0" \
    SKIP_TESTS_ON_START="1" \
    PORT=8501

# Expose Streamlit and API ports
EXPOSE 8501 8000

# Start both services via the unified runner
CMD ["python", "scripts/run_all.py"]
