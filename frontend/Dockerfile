# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

COPY backend/requirements.txt backend/*.py ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy built React app into ./static (served by FastAPI StaticFiles)
COPY --from=frontend-build /app/frontend/dist ./static

ENV DB_PATH=/tmp/scanner.db

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8080"]
