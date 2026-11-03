FROM python:3.11-slim

# the version lives in _version.py; CI passes it: --build-arg VERSION=$(python3 _version.py)
ARG VERSION=dev
LABEL maintainer="Mrt0t0"
LABEL version="$VERSION"
LABEL org.opencontainers.image.version="$VERSION"
LABEL description="BitTorrent announce proxy - Upload ratio spoofer"
LABEL org.opencontainers.image.source="https://github.com/Mrt0t0/NewGreedy"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --uid 1000 --create-home --home-dir /home/newgreedy --shell /usr/sbin/nologin newgreedy

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# The mitmproxy CA is generated at the first start, inside the mitmproxy_ca volume:
# never at build time, or every copy of the image would share the same private key.
ENV PYTHONUNBUFFERED=1 \
    NEWGREEDY_DOCKER=1 \
    NEWGREEDY_DATA_DIR=/app/data \
    NEWGREEDY_LISTEN_HOST=0.0.0.0 \
    NEWGREEDY_WEB_HOST=0.0.0.0 \
    NEWGREEDY_MITM_CONFDIR=/home/newgreedy/.mitmproxy

EXPOSE 3456 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -sf http://localhost:8080/api/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "newgreedy.py"]
