#!/bin/sh
set -eu

# El volumen del host debe estar mapeado a /backups.
mkdir -p /backups

# Pasa argumentos desde docker-compose (ej: --path /backups).
exec python3 /app/cleanup.py "$@"
