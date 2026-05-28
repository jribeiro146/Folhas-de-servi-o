FROM python:3.13-slim

WORKDIR /app

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY requirements-server.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir gunicorn \
 && pip install --no-cache-dir -r requirements-server.txt

COPY src/ src/
COPY passenger_wsgi.py .

RUN mkdir -p /app/data/graph-cache \
 && chown -R appuser:appgroup /app/data

USER appuser

ENV FS_APP_DATA_DIR=/app/data
ENV GRAPH_CACHE_DIR=/app/data/graph-cache
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--worker-tmp-dir", "/tmp", "passenger_wsgi:application"]
