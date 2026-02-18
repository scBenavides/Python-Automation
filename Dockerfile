FROM python:3.11-slim

WORKDIR /app

# Instala utilidades para montar CIFS
RUN apt-get update && \
    apt-get install -y cifs-utils && \
    rm -rf /var/lib/apt/lists/*

COPY cleanup.py .

RUN chmod +x cleanup.py

# Script de entrada
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
