FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FP_DB=/data/fitness.db

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --uid 1000 --create-home fp && mkdir -p /data && chown fp /data
USER fp
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# Fetch any missing form photos into /data/photos (about 60 MB the first time, a few seconds
# after that; never blocks startup if GitHub is unreachable), then start the app.
CMD ["sh", "-c", "python -m app.fetch_photos; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-server-header"]
