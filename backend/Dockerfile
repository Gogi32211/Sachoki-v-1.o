# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
# VITE_API_URL is empty → API calls use relative paths (same host/port)
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Build context is repo root; explicitly use backend requirements
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source (*.py explicitly — avoids BuildKit checksum bug with dir COPY)
COPY backend/*.py .

# Copy compiled React app into /app/static
COPY --from=frontend-builder /frontend/dist ./static

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
