FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend /app/backend
COPY core /app/core
RUN useradd --create-home app && chown -R app:app /app
USER app
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

FROM api AS worker
USER root
COPY backend/worker-requirements.txt /app/backend/worker-requirements.txt
RUN pip install --no-cache-dir -r backend/worker-requirements.txt
# Model artifacts cached in the image; runtime needs no model download.
RUN python -m core.ocr && chown -R app:app /app/models
USER app
CMD ["python", "-m", "backend.worker"]
