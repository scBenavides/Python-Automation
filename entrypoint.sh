#!/bin/bash
set -e

# Monta la NAS en /backups
if [ ! -d /backups ]; then
  mkdir -p /backups
fi

# Solo monta si no está montado
if ! mountpoint -q /backups; then
  echo "Montando NAS //${NAS_HOST}/${NAS_SHARE} en /backups..."
  mount -t cifs "//${NAS_HOST}/${NAS_SHARE}" /backups -o username=${NAS_USER},password=${NAS_PASS},vers=3.0
fi

# Ejecuta el script con la ruta /backups
python3 cleanup.py --path /backups
