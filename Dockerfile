FROM python:3.12-slim

WORKDIR /app

# Cache bust v7 — 2026-03-28 22:18
RUN echo "build-v7-1774743480" > /build-version.txt

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# MCP_MODE env var controls free|premium (set per-service in Railway variables)
CMD ["python3", "server.py", "--transport", "sse"]
