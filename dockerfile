FROM python:3.12-slim-bookworm

# System dependencies for Audio & Go
RUN apt-get update && apt-get install -y \
    build-essential \
    golang-go \
    sqlite3 \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 🟢 FIX: Ensure the path matches your 'python/' folder exactly
COPY python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# Set paths so Python finds your services
ENV PYTHONPATH="/app/python:/app/python/audio"

CMD ["python3", "python/main.py"]