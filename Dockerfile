# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim
# No .pyc write attempts under the read-only root filesystem docker-compose.yml sets
# (harmless either way — Python just silently skips caching on write failure — but
# this avoids the wasted attempt entirely).
ENV PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ ./app/

# /data is where docker-compose.yml mounts the app_data named volume. A fresh named
# volume is created root-owned by the Docker daemon; since this container never runs
# as root, it could never write to it. Pre-creating the directory here with the
# right ownership fixes this: Docker copies a mount point's existing content
# (ownership included) from the image into a volume the first time it's empty.
RUN mkdir -p /data && chown appuser:appuser /data

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
