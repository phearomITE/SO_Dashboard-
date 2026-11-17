FROM node:22-alpine AS admin-ui
WORKDIR /ui
COPY admin_frontend/package.json admin_frontend/package-lock.json ./
RUN npm ci
COPY admin_frontend/index.html admin_frontend/vite.config.js ./
COPY admin_frontend/src ./src
COPY admin_frontend/public ./public
RUN npm run build

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN python -m compileall -q app && python -c "import app.dashboard"
COPY --from=admin-ui /app/static/admin ./app/static/admin
COPY data ./data
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
CMD ["python", "-m", "app.main"]

