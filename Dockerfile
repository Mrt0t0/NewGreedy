FROM python:3.11-slim

LABEL maintainer="Mrt0t0"
LABEL version="v1.7.0"
LABEL description="BitTorrent announce proxy — Upload ratio spoofer"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python3 -c "from mitmproxy.certs import CertStore; CertStore.from_store('/root/.mitmproxy', 'mitmproxy')" || true

EXPOSE 3456 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/api/health || exit 1

CMD ["python3", "newgreedy.py"]
