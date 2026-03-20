# NewGreedy v1.1 — Docker image
# Modes:
#   Standard  : docker run ... newgreedy
#   mitmproxy : docker run ... newgreedy --mitmproxy

FROM python:3.11-slim

LABEL maintainer="Mrt0t0"
LABEL version="1.1"
LABEL description="NewGreedy BitTorrent HTTP/HTTPS proxy"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl git curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir requests mitmproxy

WORKDIR /app
COPY newgreedy.py newgreedy_addon.py config.ini ./

# Volumes for persistence and logs
VOLUME ["/app/data"]

# Default config points stats_file / logs to /app/data
RUN mkdir -p /app/data

EXPOSE 3456

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []
