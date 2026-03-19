# Veeam Backup Cleaner

Este proyecto lo desarrollé durante mi práctica en el SENA para resolver el problema de carpetas de backups duplicadas en repositorios de Veeam montados desde una NAS. La idea fue automatizar una revisión que normalmente se hacía a mano y reducir el riesgo de borrar la carpeta equivocada. (English documentation below)

## Project Overview

This project scans a mounted Veeam backup repository and identifies duplicated backup folders that follow common naming patterns such as:

- `Job Backup`
- `Job Backup_1`, `Job Backup_2`
- `Job Backup 1`, `Job Backup 2`
- `Job`, `Job_1`, `Job_2`

Instead of trusting the folder suffix, the script checks the real backup files inside each directory (`.vbk`, `.vib`, `.vbm`) and keeps the most recent backup set.

Main behavior:

- Runs in Docker with a Python 3.11 container.
- Uses `/backups` inside the container as the working path.
- Supports dry-run mode by default.
- Requires explicit confirmation for real deletion unless `--yes` is provided.
- Logs are configurable through `LOG_FILE`. In the current sample `.env`, the log is written inside `/backups/veeam_cleanup.log`.
- Adds safety checks for allowed roots, symlinks, invalid timestamps, and recent file activity.

## Tech Stack

- Python 3.11
- Docker
- Docker Compose
- Linux host with an SMB/CIFS mount for the Veeam repository

Key files:

- [`cleanup.py`](/home/cloudone/VEEAM-CLEANER/cleanup.py): main logic for detection, comparison, and deletion
- [`Dockerfile`](/home/cloudone/VEEAM-CLEANER/Dockerfile): lightweight Python runtime
- [`docker-compose.yml`](/home/cloudone/VEEAM-CLEANER/docker-compose.yml): container configuration and mounted volumes
- [`entrypoint.sh`](/home/cloudone/VEEAM-CLEANER/entrypoint.sh): passes CLI arguments to the Python script

## Setup

### 1. Prepare the project

```bash
git clone <repository-url>
cd VEEAM-CLEANER
cp .env.example .env
```

Review the environment file:

```env
BACKUP_DIR=/backups
ALLOWED_BACKUP_ROOTS=/backups
LOG_FILE=/backups/veeam_cleanup.log
```

Notes:

- `BACKUP_DIR` is the path used inside the container.
- `ALLOWED_BACKUP_ROOTS` works as a guardrail to prevent accidental deletion outside the expected tree.
- The current `docker-compose.yml` mounts the backup repository into `/backups`.
- The repository already mounts `./logs` to `/app/logs`, but the sample log path still points to `/backups`. If you want a separated log folder, update `LOG_FILE` accordingly.

### 2. Mount the Veeam repository on the host

On Linux, mount the NAS share first:

```bash
sudo mkdir -p /mnt/veeambackup
sudo mount -t cifs //<NAS_IP>/<SHARE_NAME> /mnt/veeambackup -o username='<USER>',password='<PASSWORD>',vers=3.0
```

Validate the mount:

```bash
findmnt -t cifs,smb3
ls -lah /mnt/veeambackup | head -n 40
```

If your repository is mounted somewhere else, update the volume in [`docker-compose.yml`](/home/cloudone/VEEAM-CLEANER/docker-compose.yml) so the host path matches your environment.

### 3. Build and run the container

The current compose configuration maps:

- `/mnt/veeambackup` on the host to `/backups` in the container
- `./logs` on the host to `/app/logs` in the container

Build the image:

```bash
docker compose build
```

Check that the container can see the repository:

```bash
docker compose run --rm --entrypoint sh veeam-cleaner -lc 'ls -lah /backups | head -n 40'
docker compose run --rm --entrypoint sh veeam-cleaner -lc 'find /backups -type f \( -name "*.vbk" -o -name "*.vib" -o -name "*.vbm" \) | head -n 30'
```

## Usage

Dry-run mode is the default behavior. It only reports what would be removed.

```bash
docker compose run --rm veeam-cleaner --no-hash
```

Run the actual cleanup:

```bash
docker compose run --rm veeam-cleaner --execute --yes --no-hash
```

Useful options:

- `--execute`: performs the real deletion
- `--yes`: skips the confirmation prompt
- `--no-hash`: disables SHA-256 calculation for faster execution on large repositories
- `--only-veeam`: processes only Veeam naming patterns
- `--only-generic`: processes only generic suffix patterns
- `--allow-symlinks`: allows symlink processing, which is disabled by default for safety
- `--path /backups`: overrides `BACKUP_DIR`

## Technical Notes

Two parts of this project were especially important during implementation:

- The deletion logic does not assume that `Backup_1` is always newer. The script compares the latest modification time of real Veeam files inside each folder and keeps the newest valid backup set.
- Hash calculation was useful for verification, but it can be expensive on large repositories. Because of that, I kept `--no-hash` available for faster operational runs and left hashing as an extra validation step when needed.
- Docker volume mapping was another practical detail. The cleanup only works correctly if the host SMB mount is visible inside the container as `/backups`, so validating the mount from both the host and the container became part of the setup process.
- I also added guardrails to avoid risky deletions: allowed root validation, symlink protection, recent activity detection, and timestamp sanity checks.

## Operational Summary

This tool is meant for controlled cleanup tasks in backup repositories where duplicated folders appear after manual operations, retries, or naming inconsistencies.

What it helps with:

- Reducing manual review time
- Avoiding deletion based only on folder names
- Keeping a repeatable cleanup flow through Docker
- Leaving a log trail for verification

## Quick Reminder

- Test with dry-run first.
- Confirm that the NAS path is mounted correctly on the host.
- Verify that Docker sees the same files under `/backups`.
- Do not run real deletion without checking permissions on the share.
