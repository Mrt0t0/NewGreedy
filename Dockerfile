FROM python:3.11-slim

LABEL maintainer="Mrt0t0" \
      version="1.3" \
      description="NewGreedy - BitTorrent announce proxy"

WORKDIR /app

# System deps
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Generate mitmproxy CA on build
RUN mitmdump --quiet & sleep 4 && kill $! 2>/dev/null; true

# Copy app files
COPY newgreedy.py        .
COPY newgreedy_addon.py  .
COPY config.ini          .

# Expose proxy port
EXPOSE 3456

# Mount point for persistent data (stats.json, logs, config override)
VOLUME ["/app/data"]

# Entrypoint
CMD ["python", "newgreedy.py"]
