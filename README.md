# VEEAM-CLEANER

Limpia carpetas de backups duplicados de Veeam dentro de una ruta montada en `/backups` usando Docker.

## Requisitos

- Docker instalado.
- Docker Compose disponible como `docker compose`.
- Acceso SMB/CIFS al recurso compartido de la NAS.
- Permisos de escritura y borrado en la NAS para el usuario SMB.

## 1) Clonar y preparar variables

```bash
git clone <URL_DEL_REPO>
cd VEEAM-CLEANER
cp .env.example .env
```

En `.env`, deja como mínimo:

```env
BACKUP_DIR=/backups
ALLOWED_BACKUP_ROOTS=/backups
LOG_FILE=/app/logs/veeam_cleanup.log
```

## 2) Montar la NAS en el host (Linux)

Crear punto de montaje:

```bash
sudo mkdir -p /mnt/veeambackup
```

Montar share SMB:

```bash
sudo mount -t cifs //<NAS_IP>/<NAS_SHARE> /mnt/veeambackup -o username='<USUARIO>',password='<PASSWORD>',vers=3.0
```

Validar que montó:

```bash
findmnt -t cifs,smb3
ls -lah /mnt/veeambackup | head -n 40
```

## 3) Verificar que Docker vea la NAS

El `docker-compose.yml` monta `/mnt/veeambackup` dentro del contenedor como `/backups`.

```bash
docker compose run --rm --entrypoint sh veeam-cleaner -lc 'ls -lah /backups | head -n 40'
docker compose run --rm --entrypoint sh veeam-cleaner -lc 'find /backups -type f \( -name "*.vbk" -o -name "*.vib" -o -name "*.vbm" \) | head -n 30'
```

## 4) Ejecutar prueba (dry-run)

```bash
docker compose run --rm veeam-cleaner --no-hash
```

Esto no elimina nada; solo muestra qué borraría.

## 5) Ejecutar eliminación real

```bash
docker compose run --rm veeam-cleaner --execute --yes --no-hash
```

## Solución de problemas rápida

- `Permission denied` al borrar archivos:
  - El usuario SMB no tiene permisos `delete/modify` en la NAS.
  - Ajusta ACL/permisos del share y reintenta.
- No encuentra duplicados:
  - Verifica que `/backups` dentro del contenedor realmente tenga los `.vbk/.vib/.vbm`.
- Error con `docker compose`:
  - Instala/activa Docker Compose en tu sistema.

## Seguridad

- No subas `.env` al repositorio.
- No publiques credenciales de NAS en commits, logs o capturas.
