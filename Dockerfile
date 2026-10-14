# Same base digest exercised by CI run 38243794527. Updates require a new run.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS runtime

# Offline migration/backup require data outside the verified code artifact.
# Keep historical data paths (/app/data) and move only the image's code root.
WORKDIR /opt/folhas

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

ARG APP_UID=10001
ARG APP_GID=10001
RUN test "$APP_UID" -gt 0 && test "$APP_GID" -gt 0 \
 && addgroup --system --gid "$APP_GID" appgroup \
 && adduser --system --uid "$APP_UID" --ingroup appgroup --home /home/appuser appuser

COPY requirements-server.txt requirements-smoke.txt requirements-linux-constraints.txt ./
RUN python -m pip install --no-cache-dir -c requirements-linux-constraints.txt --upgrade pip \
 && python -m pip install --no-cache-dir -c requirements-linux-constraints.txt gunicorn -r requirements-server.txt -r requirements-smoke.txt \
 && python -m pip check

COPY src/ src/
COPY tools/ tools/
# Build only from the extracted build_release ZIP. Every fingerprinted file
# must be present, including delivery files; never regenerate a manifest here.
# Optional QA requirements are identity metadata too; copying does not install them.
COPY passenger_wsgi.py requirements*.txt Dockerfile .dockerignore docker-compose.yml release-manifest.json ./
RUN python -B -c "from src.services.release_identity import get_release_info; assert get_release_info()['verified']"

RUN mkdir -p /app/data /home/appuser \
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

# Optional test image shares the exact runtime layer. The default final target
# remains production, without pytest, Git or test fixtures.
FROM runtime AS qa
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip install --no-cache-dir -c requirements-linux-constraints.txt pytest \
 && python -m pip check
COPY tests/ tests/
USER appuser
ENV PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
CMD ["python", "-B", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--tb=short"]

FROM runtime AS production
