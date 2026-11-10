FROM alpine:3.20

# Install Python 3, aiohttp, zeroconf, and curl directly via Alpine apk
# Pre-compiled packages eliminate the need for gcc/musl-dev/pip compilation
RUN apk add --no-cache \
    python3 \
    py3-aiohttp \
    py3-zeroconf \
    curl

WORKDIR /app

COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh && chmod +x /run.sh

COPY src /app/src

ENV PYTHONPATH="/app"

ENTRYPOINT [ "/run.sh" ]
