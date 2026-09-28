FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/* \
 && addgroup --system --gid 10001 appgroup \
 && adduser --system --uid 10001 --ingroup appgroup --home /home/appuser appuser

COPY requirements-server.txt requirements-smoke.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir gunicorn \
 && pip install --no-cache-dir -r requirements-server.txt -r requirements-smoke.txt

COPY src/ src/
COPY passenger_wsgi.py .
COPY tools/smoke_pdf.py tools/test_runtime.py tools/

RUN mkdir -p /app/data/graph-cache /home/appuser \
 && chown -R appuser:appgroup /app/data /home/appuser

USER appuser

ENV HOME=/home/appuser
ENV FS_BASE_PATH=/app/data
ENV FS_APP_DATA_DIR=/app/data
ENV GRAPH_CACHE_DIR=/app/data/graph-cache
ENV FS_GRAPH_QUEUE_IN_WEB=false
ENV FS_PDF_BROWSER_PATH=/usr/bin/chromium
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--worker-tmp-dir", "/tmp", "passenger_wsgi:application"]
