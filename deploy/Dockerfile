# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN addgroup --system peawork \
    && adduser --system --ingroup peawork --home /var/lib/peawork peawork

WORKDIR /app

COPY requirements/v2.txt requirements/v2.txt
RUN pip install --no-cache-dir -r requirements/v2.txt

COPY alembic.ini ./
COPY peawork ./peawork
COPY deploy/entrypoint.sh /usr/local/bin/peawork-entrypoint

RUN chmod 0755 /usr/local/bin/peawork-entrypoint \
    && mkdir -p /var/lib/peawork/documents \
    && chown -R peawork:peawork /var/lib/peawork /app

USER peawork
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health/live', timeout=4)"

ENTRYPOINT ["/usr/local/bin/peawork-entrypoint"]
