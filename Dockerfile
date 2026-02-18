FROM python:3.11-slim

WORKDIR /app

COPY cleanup.py .

RUN chmod +x cleanup.py

# Script de entrada
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
