#!/usr/bin/env python3
"""
Script de Limpieza de Backups Duplicados de Veeam
==================================================
Estrategia: Elimina SIEMPRE el backup MÁS ANTIGUO (basándose en fechas reales)
            Conserva SIEMPRE el backup MÁS RECIENTE (protección inteligente)

Características:
- Detecta patrones: "[Nombre] Backup / Backup_1 / Backup_2 / ..." y "[Nombre] / [Nombre]_1 / [Nombre]_2 / ..."
- Usa fechas de archivos .vib/.vbk/.vbm (no de carpetas)
- Invierte lógica automáticamente si detecta backup reciente sin _1
- Protección contra pérdida de datos

Autor: Versión Final Unificada
Fecha: 2026-02-11
"""

from pathlib import Path
from datetime import datetime
import time
import hashlib
import shutil
import argparse
import sys
import re
import os

# =====================================================
# CONFIGURACIÓN - AJUSTAR SEGÚN TU ENTORNO
# =====================================================
# Opción 1: Ruta relativa al script (RECOMENDADO para testing)
# SCRIPT_DIR = Path(__file__).parent.resolve()
# BACKUP_DIR = SCRIPT_DIR / "test_backups"

# Opción 2: Ruta absoluta (RECOMENDADO para producción)
DEFAULT_BACKUP_DIR = Path("/backups")

# Archivo de log
LOG_FILE = Path(os.getenv("LOG_FILE", "/backups/veeam_cleanup.log"))
FALLBACK_LOG_FILE = Path("/tmp/veeam_cleanup.log")
ENV_FILE = Path(__file__).parent / ".env"

# Rutas permitidas para BACKUP_DIR (seguridad extra)
# Puedes definirlas en .env como lista separada por comas:
# ALLOWED_BACKUP_ROOTS=/backups,/mnt/backup
ALLOWED_BACKUP_ROOTS = [
    "/backups",
]

# Guardrails de tiempo
FUTURE_TIME_SKEW_DAYS = 7
MIN_VALID_TIMESTAMP = 60 * 60 * 24  # 1 dia despues del epoch
RECENT_ACTIVITY_MINUTES = 10
# =====================================================

# Extensiones de archivos de backup de Veeam
VEEAM_EXTENSIONS = ("*.vib", "*.vbk", "*.vbm")

# Patrones a excluir del análisis (nombres completos de carpetas)
# Ejemplo: r"^Server_Tier_1 Backup$"
EXCLUDE_PATTERNS = [
    r"^Server_Tier_1 Backup$",
]


def load_env_file(env_file: Path):
    """
    Carga variables KEY=VALUE desde .env si existe.
    No sobrescribe variables ya definidas en el entorno.
    """
    if not env_file.exists() or not env_file.is_file():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        log(f"No se pudo cargar {env_file}: {e}")


def get_configured_backup_dir(cli_path: str | None):
    """Resuelve BACKUP_DIR con precedencia: --path > env > default."""
    if cli_path:
        return Path(cli_path), "cli"
    env_path = os.getenv("BACKUP_DIR")
    if env_path:
        return Path(env_path), "env"
    return DEFAULT_BACKUP_DIR, "default"


def log(msg, print_it=True):
    """Registra mensaje en log y opcionalmente lo imprime en consola"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Fallback a /tmp si no se puede escribir en el log principal
        if print_it:
            print("No se pudo escribir en el log principal, usando /tmp/veeam_cleanup.log")
        try:
            with open(FALLBACK_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # Si tampoco se puede escribir, solo imprime en consola
            pass
    
    if print_it:
        print(msg)


def get_latest_backup_time(folder: Path):
    """
    Obtiene la fecha del archivo de backup de Veeam más reciente
    Busca archivos .vib, .vbk, .vbm dentro de la carpeta recursivamente
    
    Returns:
        float: Timestamp Unix del archivo más reciente
        None: Si no se encuentran archivos de backup
    """
    times = []
    try:
        for pattern in VEEAM_EXTENSIONS:
            for backup_file in folder.rglob(pattern):
                if backup_file.is_file():
                    times.append(backup_file.stat().st_mtime)
        return max(times) if times else None
    except (OSError, PermissionError) as e:
        log(f"Error al leer archivos en {folder.name}: {e}")
        return None


def get_folder_size(folder: Path):
    """Calcula el tamaño total de una carpeta recursivamente"""
    total = 0
    try:
        for item in folder.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
        return total
    except Exception as e:
        log(f"Error calculando tamaño de {folder.name}: {e}")
        return 0


def is_folder_empty(folder: Path):
    """Retorna True si la carpeta está vacía"""
    try:
        return not any(folder.iterdir())
    except Exception as e:
        log(f"Error al leer contenido de {folder.name}: {e}")
        return False


def get_backup_files(folder: Path):
    """Devuelve lista de archivos de backup Veeam dentro de la carpeta"""
    files = []
    for pattern in VEEAM_EXTENSIONS:
        files.extend([p for p in folder.rglob(pattern) if p.is_file()])
    return files


def is_safe_path(path: Path, base_dir: Path):
    """Valida que la ruta esté dentro de base_dir (evita borrar fuera del árbol)"""
    try:
        return path.resolve().is_relative_to(base_dir.resolve())
    except Exception:
        return False


def get_allowed_backup_roots():
    """
    Obtiene roots permitidos desde env o configuración por defecto.
    Env soportado: ALLOWED_BACKUP_ROOTS=/r1,/r2
    """
    env_roots = os.getenv("ALLOWED_BACKUP_ROOTS", "").strip()
    if env_roots:
        return [r.strip() for r in env_roots.split(",") if r.strip()]
    return ALLOWED_BACKUP_ROOTS


def is_allowed_backup_root(base_dir: Path, allowed_roots: list[str] | None = None):
    """Valida que BACKUP_DIR esté dentro de rutas permitidas"""
    roots = allowed_roots if allowed_roots is not None else get_allowed_backup_roots()
    if not roots:
        return True
    try:
        base_resolved = base_dir.resolve()
        for root in roots:
            root_path = Path(root).resolve()
            if base_resolved.is_relative_to(root_path):
                return True
    except Exception:
        return False
    return False


def is_symlink_path(path: Path):
    """Retorna True si la ruta es symlink"""
    try:
        return path.is_symlink()
    except Exception:
        return False


def hash_backup_folder(folder: Path, use_hash: bool = True):
    """
    Calcula hash SHA256 de todos los archivos de backup dentro de la carpeta.
    Se usan rutas relativas + tamaño + contenido para un hash estable.
    
    Returns:
        str: hash hex, o None si no hay archivos o hay error
    """
    if not use_hash:
        return None
    try:
        backup_files = get_backup_files(folder)
        if not backup_files:
            return None
        
        h = hashlib.sha256()
        for file_path in sorted(backup_files, key=lambda p: str(p.relative_to(folder))):
            rel = str(file_path.relative_to(folder)).encode("utf-8")
            h.update(rel)
            h.update(str(file_path.stat().st_size).encode("utf-8"))
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError) as e:
        log(f"Error calculando hash en {folder.name}: {e}")
        return None


def format_time(timestamp):
    """Formatea timestamp Unix a formato legible"""
    if timestamp is None:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def is_valid_backup_time(ts: float):
    """Valida timestamps absurdos (futuro lejano o epoch)"""
    if ts is None:
        return False
    now = time.time()
    if ts < MIN_VALID_TIMESTAMP:
        return False
    if ts > now + (FUTURE_TIME_SKEW_DAYS * 24 * 60 * 60):
        return False
    return True


def has_recent_activity(folder: Path):
    """Detecta actividad reciente en archivos de backup (evita borrar mientras se escribe)"""
    now = time.time()
    for pattern in VEEAM_EXTENSIONS:
        for backup_file in folder.rglob(pattern):
            if backup_file.is_file():
                try:
                    if now - backup_file.stat().st_mtime < RECENT_ACTIVITY_MINUTES * 60:
                        return True
                except (OSError, PermissionError):
                    continue
    return False


def format_size(size_bytes):
    """Formatea bytes a formato legible (KB, MB, GB, etc.)"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def analyze_group(folders, pattern_type: str, use_hash: bool = True):
    """
    Analiza un grupo de carpetas duplicadas (múltiples sufijos)
    y devuelve decisiones para eliminar todas menos la más reciente.
    """
    infos = []
    for folder in folders:
        time_val = get_latest_backup_time(folder)
        hash_val = hash_backup_folder(folder, use_hash=use_hash)
        infos.append(
            {
                "folder": folder,
                "time": time_val,
                "hash": hash_val,
                "size": get_folder_size(folder),
            }
        )
    
    # Guardrail: evitar borrar si hay actividad reciente
    for info in infos:
        if has_recent_activity(info["folder"]):
            log(f"  Actividad reciente detectada en {info['folder'].name} (se protege el grupo)")
            return []
    
    # Filtrar carpetas con backups reales
    with_backups = [i for i in infos if i["time"] is not None]
    if not with_backups:
        decisions = []
        for info in infos:
            if is_folder_empty(info["folder"]):
                decisions.append(
                    {
                        "delete": info["folder"],
                        "keep": None,
                        "time_delete": None,
                        "time_keep": None,
                        "size": info["size"],
                        "warning": " Carpeta vacía sin backups reales",
                        "status": "EMPTY_NO_BACKUPS",
                        "pattern": pattern_type,
                        "inverted": False,
                        "hash_delete": None,
                        "hash_keep": None,
                        "hash_equal": False,
                    }
                )
            else:
                log(f" PROTEGIDO: {info['folder'].name} (sin backups reales)")
        return decisions
    
    # Guardrail: timestamps inválidos
    for info in with_backups:
        if not is_valid_backup_time(info["time"]):
            log(f"  Timestamp inválido en {info['folder'].name} (se protege el grupo)")
            return []
    
    # Conservar la más reciente por fecha real
    keep_info = max(with_backups, key=lambda i: i["time"])
    decisions = []
    
    for info in infos:
        if info["folder"] == keep_info["folder"]:
            continue
        if info["time"] is None:
            if is_folder_empty(info["folder"]):
                decisions.append(
                    {
                        "delete": info["folder"],
                        "keep": keep_info["folder"],
                        "time_delete": None,
                        "time_keep": keep_info["time"],
                        "size": info["size"],
                        "warning": "  Carpeta vacía sin backups reales",
                        "status": "EMPTY_NO_BACKUPS",
                        "pattern": pattern_type,
                        "inverted": False,
                        "hash_delete": None,
                        "hash_keep": keep_info["hash"],
                        "hash_equal": False,
                    }
                )
            else:
                log(f" PROTEGIDO: {info['folder'].name} (sin backups reales)")
            continue
        
        decisions.append(
            {
                "delete": info["folder"],
                "keep": keep_info["folder"],
                "time_delete": info["time"],
                "time_keep": keep_info["time"],
                "size": info["size"],
                "warning": "",
                "status": "MULTI",
                "pattern": pattern_type,
                "inverted": False,
                "hash_delete": info["hash"],
                "hash_keep": keep_info["hash"],
                "hash_equal": (
                    info["hash"] is not None
                    and keep_info["hash"] is not None
                    and info["hash"] == keep_info["hash"]
                ),
            }
        )
    
    return decisions


def find_duplicate_backups(base_dir: Path, mode: str = "all", use_hash: bool = True, allow_symlinks: bool = False):
    """
    Encuentra backups duplicados siguiendo estos patrones:
    1. [Nombre] Backup / [Nombre] Backup_1 / Backup_2 / ... (patrón estándar Veeam)
    2. [Nombre] / [Nombre]_1 / [Nombre]_2 / ... (patrón genérico)
    
    Marca para eliminar todos menos el backup MÁS RECIENTE.
    """
    if not base_dir.exists():
        log(f" ERROR: La ruta {base_dir} no existe")
        return []
    
    results = []
    
    # Obtener todas las carpetas
    all_folders = [f for f in base_dir.iterdir() if f.is_dir()]
    
    veeam_groups = {}
    generic_groups = {}
    
    for folder in all_folders:
        name = folder.name
        
        # Excluir nombres completos configurados
        if any(re.match(pat, name) for pat in EXCLUDE_PATTERNS):
            log(f" EXCLUIDO: {name} (patrón en EXCLUDE_PATTERNS)")
            continue
        
        # Excluir symlinks por seguridad
        if is_symlink_path(folder) and not allow_symlinks:
            log(f" EXCLUIDO: {name} (symlink detectado)")
            continue
        
        # Patrón Veeam (case-insensitive):
        # "Nombre Backup"
        # "Nombre Backup_1"
        # "Nombre Backup _1"
        veeam_base_match = re.match(r"^(?P<base>.+?)\s+backup\s*$", name, flags=re.IGNORECASE)
        if veeam_base_match:
            base = veeam_base_match.group("base").strip()
            base_key = f"{base} Backup"
            veeam_groups.setdefault(base_key, []).append(folder)
            continue
        
        veeam_suffix_match = re.match(r"^(?P<base>.+?)\s+backup\s*_\s*(?P<num>\d+)\s*$", name, flags=re.IGNORECASE)
        if veeam_suffix_match:
            base = veeam_suffix_match.group("base").strip()
            base_key = f"{base} Backup"
            veeam_groups.setdefault(base_key, []).append(folder)
            continue
        
        # Patrón genérico: "Nombre" o "Nombre_N"
        if "_" in name and name.rsplit("_", 1)[1].isdigit():
            base = name.rsplit("_", 1)[0]
            generic_groups.setdefault(base, []).append(folder)
    
    # Procesar grupos Veeam (incluye la base si existe)
    if mode in ("all", "veeam"):
        for base_name, folders in veeam_groups.items():
            base_folder = base_dir / base_name
            if base_folder.exists() and base_folder.is_dir():
                if base_folder not in folders:
                    folders.append(base_folder)
            if len(folders) < 2:
                for f in folders:
                    log(f" PROTEGIDO: {f.name} (no tiene gemelas suficientes)")
                continue
            results.extend(analyze_group(folders, "Veeam", use_hash=use_hash))
    
    # Procesar grupos genéricos
    if mode in ("all", "generic"):
        for base_name, folders in generic_groups.items():
            base_folder = base_dir / base_name
            if base_folder.exists() and base_folder.is_dir():
                if base_folder not in folders:
                    folders.append(base_folder)
            if len(folders) < 2:
                for f in folders:
                    log(f" PROTEGIDO: {f.name} (no tiene gemelas suficientes)")
                continue
            results.extend(analyze_group(folders, "Genérico", use_hash=use_hash))
    
    return results


def analyze_pair(folder_old: Path, folder_new: Path, pattern_type: str, use_hash: bool = True):
    """
    Analiza un par de carpetas duplicadas y determina cuál eliminar
    
    LÓGICA INTELIGENTE:
    - Compara fechas de archivos .vib/.vbk/.vbm (no de carpetas)
    - Elimina SIEMPRE el backup MÁS ANTIGUO
    - Conserva SIEMPRE el backup MÁS RECIENTE
    - Invierte la lógica automáticamente si es necesario
    
    Args:
        folder_old: Carpeta SIN sufijo _1
        folder_new: Carpeta CON sufijo _1
        pattern_type: Tipo de patrón detectado ("Veeam" o "Genérico")
    
    Returns:
        dict: Información del par con decisión de eliminación
    """
    # Obtener fecha del backup más reciente en cada carpeta
    time_old = get_latest_backup_time(folder_old)
    time_new = get_latest_backup_time(folder_new)
    hash_old = hash_backup_folder(folder_old, use_hash=use_hash)
    hash_new = hash_backup_folder(folder_new, use_hash=use_hash)
    
    # Guardrail: evitar borrar si hay actividad reciente
    if has_recent_activity(folder_old) or has_recent_activity(folder_new):
        log(f"  Actividad reciente detectada en {folder_old.name} o {folder_new.name} (se protege el par)")
        return None
    
    # Guardrail: timestamps inválidos
    if time_old is not None and not is_valid_backup_time(time_old):
        log(f"  Timestamp inválido en {folder_old.name} (se protege el par)")
        return None
    if time_new is not None and not is_valid_backup_time(time_new):
        log(f"  Timestamp inválido en {folder_new.name} (se protege el par)")
        return None
    
    # Determinar estado y advertencias
    warning = ""
    status = "NORMAL"
    
    # ═══════════════════════════════════════════════════════════
    # LÓGICA DE DECISIÓN
    # ═══════════════════════════════════════════════════════════
    
    # CASO 1: Ninguna carpeta tiene backups
    if time_old is None and time_new is None:
        warning = "  No se encontraron archivos de backup en ninguna carpeta"
        status = "SIN_BACKUPS"
        # No eliminar nada si no hay backups reales
        return None
    
    # CASO 2: Solo la carpeta SIN _1 está vacía o sin backups
    elif time_old is None:
        if is_folder_empty(folder_old):
            warning = "  Carpeta SIN _1 está vacía - se eliminará"
            status = "OLD_EMPTY"
            to_delete = folder_old
            to_keep = folder_new
            size = get_folder_size(folder_old)
        else:
            warning = " Carpeta SIN _1 sin backups reales (no vacía) - se protege"
            status = "OLD_NO_BACKUPS"
            return None
    
    # CASO 3: Solo la carpeta CON _1 está vacía o sin backups
    elif time_new is None:
        if is_folder_empty(folder_new):
            warning = "  Carpeta CON _1 está vacía - se eliminará (INVERTIDO)"
            status = "NEW_EMPTY"
            to_delete = folder_new
            to_keep = folder_old
            size = get_folder_size(folder_new)
        else:
            warning = "  Carpeta CON _1 sin backups reales (no vacía) - se protege"
            status = "NEW_NO_BACKUPS"
            return None
    
    # CASO 4: Ambas tienen backups - COMPARAR FECHAS
    else:
        # ┌─────────────────────────────────────────────────────┐
        # │ LÓGICA INTELIGENTE: Comparar fechas reales         │
        # └─────────────────────────────────────────────────────┘
        
        if time_old < time_new:
            # CASO NORMAL: La carpeta SIN _1 es más antigua
            # → Eliminar la SIN _1 (antigua)
            # → Conservar la CON _1 (reciente)
            to_delete = folder_old
            to_keep = folder_new
            status = "NORMAL"
            warning = ""
        
        elif time_old > time_new:
            # CASO INVERTIDO: La carpeta SIN _1 es MÁS RECIENTE
            # ¡PROTEGER EL BACKUP RECIENTE!
            # → Eliminar la CON _1 (antigua)
            # → Conservar la SIN _1 (reciente)
            to_delete = folder_new
            to_keep = folder_old
            status = "INVERTED"
            warning = (
                f" INVERSIÓN DETECTADA: Se eliminará '{folder_new.name}' (antigua) "
                f"y se conservará '{folder_old.name}' (más reciente)"
            )
        
        else:
            # CASO EMPATE: Misma fecha (muy raro)
            to_delete = folder_old
            to_keep = folder_new
            status = "EMPATE"
            warning = "  Ambas carpetas tienen la misma fecha de backup"
        
        size = get_folder_size(to_delete)
    
    # ═══════════════════════════════════════════════════════════
    # RETORNAR RESULTADO DEL ANÁLISIS
    # ═══════════════════════════════════════════════════════════
    
    return {
        "delete": to_delete,                              # Carpeta a eliminar (la MÁS ANTIGUA)
        "keep": to_keep,                                  # Carpeta a conservar (la MÁS RECIENTE)
        "time_delete": time_old if to_delete == folder_old else time_new,
        "time_keep": time_new if to_keep == folder_new else time_old,
        "size": size,                                      # Tamaño a liberar
        "warning": warning,                                # Mensaje de advertencia
        "status": status,                                  # Estado del análisis
        "pattern": pattern_type,                           # Tipo de patrón detectado
        "inverted": to_delete == folder_new,               # True si se invirtió la lógica
        "hash_delete": hash_old if to_delete == folder_old else hash_new,
        "hash_keep": hash_new if to_keep == folder_new else hash_old,
        "hash_equal": (hash_old is not None and hash_old == hash_new)
    }


def process_deletions(candidates, backup_dir: Path, dry_run=False, allow_symlinks=False):
    """
    Procesa las eliminaciones (real o simulación)
    
    Args:
        candidates: Lista de pares de carpetas a procesar
        dry_run: Si es True, solo simula (no elimina realmente)
    
    Returns:
        tuple: (carpetas_eliminadas, errores)
    """
    log("\n" + "=" * 80)
    log(f"MODO: {'DRY-RUN (SIMULACIÓN)' if dry_run else 'ELIMINACIÓN REAL'}")
    log("=" * 80 + "\n")
    
    total_space = 0
    deleted_count = 0
    error_count = 0
    warning_count = 0
    inverted_count = 0
    empty_deleted_count = 0
    
    for idx, item in enumerate(candidates, 1):
        # Extraer nombre del job (sin sufijos)
        job_name = item['delete'].name
        if " Backup_" in job_name:
            job_name = job_name.split(" Backup_")[0]
        job_name = job_name.replace(" Backup", "")
        if "_" in job_name and job_name.rsplit("_", 1)[1].isdigit():
            job_name = job_name.rsplit("_", 1)[0]
        
        log(f"\n{'─' * 80}")
        log(f"[{idx}/{len(candidates)}] Job: {job_name}")
        log(f"{'─' * 80}")
        
        # Indicador visual de inversión
        if item.get('inverted', False):
            log(f"INVERSIÓN DETECTADA (se conserva la carpeta SIN _1)")
            inverted_count += 1
        
        log(f"  ELIMINAR:  {item['delete'].name}")
        if item.get("keep") is not None:
            log(f"  CONSERVAR: {item['keep'].name}")
        else:
            log("  CONSERVAR: N/A (carpeta vacía)")
        log(f"  Fecha a eliminar:  {format_time(item['time_delete'])}")
        log(f"  Fecha a conservar: {format_time(item['time_keep'])}")
        log(f"  Tamaño a liberar: {format_size(item['size'])}")
        log(f"  Patrón detectado: {item['pattern']}")
        log(f"  Estado: {item['status']}")
        if item.get("hash_delete") and item.get("hash_keep"):
            log(f"  Hash eliminar:  {item['hash_delete']}")
            log(f"  Hash conservar: {item['hash_keep']}")
            if item.get("hash_equal"):
                log("  ℹHashes idénticos: se conserva el backup MÁS RECIENTE igualmente")
        
        if item['warning']:
            log(f"  {item['warning']}")
            warning_count += 1
        
        if item.get("status") in ("EMPTY_NO_BACKUPS", "OLD_EMPTY", "NEW_EMPTY"):
            empty_deleted_count += 1
        
        # Ejecutar eliminación o simulación
        if not dry_run:
            try:
                if not is_safe_path(item['delete'], backup_dir):
                    raise RuntimeError("Ruta fuera de BACKUP_DIR (bloqueado por seguridad)")
                if is_symlink_path(item['delete']) and not allow_symlinks:
                    raise RuntimeError("Ruta es symlink (bloqueado por seguridad)")
                shutil.rmtree(item['delete'])
                log(f"  Eliminado exitosamente")
                deleted_count += 1
            except Exception as e:
                log(f"  Error al eliminar: {e}")
                error_count += 1
                continue
        else:
            log(f" Simulación: NO se eliminó nada")
        
        total_space += item['size']
    
    # ═══════════════════════════════════════════════════════════
    # RESUMEN FINAL
    # ═══════════════════════════════════════════════════════════
    
    log("\n" + "=" * 80)
    log("RESUMEN DE OPERACIÓN")
    log("=" * 80)
    
    if inverted_count > 0:
        log(f" INVERSIONES: {inverted_count} par(es) con lógica invertida (se eliminó la carpeta CON _1)")
    
    if warning_count > 0:
        log(f"  ADVERTENCIAS: {warning_count} par(es) con situaciones especiales")
    
    if empty_deleted_count > 0:
        if dry_run:
            log(f" Carpetas vacías planificadas para eliminar: {empty_deleted_count}")
        else:
            log(f" Carpetas vacías eliminadas: {empty_deleted_count}")
    
    if allow_symlinks:
        log("Symlinks permitidos en esta ejecución")
    
    if dry_run:
        log(f" Carpetas que se eliminarían: {len(candidates)}")
        log(f" Espacio total a liberar: {format_size(total_space)}")
    else:
        log(f" Carpetas eliminadas: {deleted_count}")
        if error_count > 0:
            log(f" Errores: {error_count}")
        log(f" Espacio total liberado: {format_size(total_space)}")
    
    log("=" * 80 + "\n")
    
    return deleted_count, error_count


def main():
    """Función principal del script"""
    
    parser = argparse.ArgumentParser(
        description="Limpieza Inteligente de Backups Duplicados de Veeam",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""


EJEMPLOS DE USO:
  %(prog)s                    # Modo dry-run (solo muestra qué eliminaría)
  %(prog)s --execute          # Elimina carpetas antiguas (pide confirmación)
  %(prog)s --execute --yes    # Elimina sin pedir confirmación
  %(prog)s --only-veeam       # Solo patrón Veeam
  %(prog)s --only-generic     # Solo patrón genérico
  %(prog)s --no-hash          # Desactiva hash (más rápido)
  %(prog)s --allow-symlinks   # Permite procesar symlinks (NO recomendado)
  
ESTRATEGIA INTELIGENTE:
  ✓ Detecta patrones: "[Nombre] Backup_1/2/..." y "[Nombre]_1/2/..."
  ✓ Con sufijos múltiples: conserva solo el MÁS RECIENTE
  ✓ Si no hay backups reales: elimina solo carpetas vacías
  ✓ Opciones: --only-veeam, --only-generic, --no-hash, --allow-symlinks
  ✓ Compara fechas de archivos .vib/.vbk/.vbm (no de carpetas)
  ✓ Elimina SIEMPRE el backup más antiguo (protección contra pérdida de datos)
  ✓ Invierte lógica automáticamente si detecta backup reciente sin _1
  ✓ Protege carpetas _1 que no tienen gemela

CONFIGURACIÓN:
  Define BACKUP_DIR en .env o usa --path
  
ARCHIVOS:
  Log: ~/veeam_cleanup.log
        """
        #Argumentos existentes
    )
    
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Ejecutar eliminación real (por defecto es dry-run)"
    )
    
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="No pedir confirmación antes de eliminar"
    )
    
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="No calcular hash de archivos (más rápido en repos grandes)"
    )
    
    parser.add_argument(
        "--allow-symlinks",
        action="store_true",
        help="Permitir procesar symlinks (NO recomendado)"
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--only-veeam",
        action="store_true",
        help="Procesar solo patrón Veeam ([Nombre] Backup / Backup_N)"
    )
    group.add_argument(
        "--only-generic",
        action="store_true",
        help="Procesar solo patrón genérico ([Nombre] / [Nombre]_N)"
    )
    parser.add_argument(
    "--path",
    help="Ruta del directorio de backups (prioridad sobre BACKUP_DIR del .env)"
    )
    
    # Parsear argumentos
    args = parser.parse_args()
    
    # Cargar .env local y resolver BACKUP_DIR final
    load_env_file(ENV_FILE)
    backup_dir, backup_source = get_configured_backup_dir(args.path)
    
    # ═══════════════════════════════════════════════════════════
    # VALIDACIONES INICIALES
    # ═══════════════════════════════════════════════════════════
    
    if not backup_dir.exists():
        log(f" ERROR: El directorio {backup_dir} no existe")
        log(" Define BACKUP_DIR en .env o usa --path")
        return 1
    if not backup_dir.is_dir():
        log(f" ERROR: La ruta {backup_dir} no es un directorio")
        log(" Define BACKUP_DIR en .env o usa --path")
        return 1
    if backup_dir.resolve() == Path("/"):
        log(" ERROR: BACKUP_DIR no puede ser '/'")
        log(" Define BACKUP_DIR en .env o usa --path")
        return 1
    configured_roots = get_allowed_backup_roots()
    effective_roots = configured_roots if configured_roots else [str(backup_dir)]
    if not is_allowed_backup_root(backup_dir, allowed_roots=effective_roots):
        log(f" ERROR: BACKUP_DIR {backup_dir} no está dentro de ALLOWED_BACKUP_ROOTS")
        log(" Define ALLOWED_BACKUP_ROOTS en .env o usa una ruta permitida")
        return 1
    
    log(f" Buscando backups duplicados en: {backup_dir}")
    log(f" Fuente de ruta: {backup_source}")
    log(f" Roots permitidos: {', '.join(effective_roots)}")
    log(f" Estrategia: Eliminar backup MÁS ANTIGUO (protección inteligente)\n")
    
    # ═══════════════════════════════════════════════════════════
    # BUSCAR CANDIDATOS
    # ═══════════════════════════════════════════════════════════
    
    mode = "all"
    if args.only_veeam:
        mode = "veeam"
    elif args.only_generic:
        mode = "generic"
    
    if args.no_hash:
        log(" Hash desactivado: no se calcularán hashes de archivos (modo rápido)")
    if args.allow_symlinks:
        log("  Symlinks permitidos: se procesarán enlaces simbólicos (NO recomendado)")
    
    if mode == "veeam":
        log(" Modo: solo patrón Veeam")
    elif mode == "generic":
        log(" Modo: solo patrón genérico")
    else:
        log(" Modo: Veeam + genérico")
    
    candidates = find_duplicate_backups(
        backup_dir,
        mode=mode,
        use_hash=not args.no_hash,
        allow_symlinks=args.allow_symlinks,
    )
    
    if not candidates:
        log(" No se encontraron carpetas duplicadas para eliminar")
        return 0
    
    log(f"\n Se encontraron {len(candidates)} par(es) de carpetas duplicadas\n")
    
    # ═══════════════════════════════════════════════════════════
    # MOSTRAR RESUMEN DE CANDIDATOS
    # ═══════════════════════════════════════════════════════════
    
    empty_planned_count = 0
    for item in candidates:
        if item.get('inverted', False):
            log(f"   {item['delete'].name} → ELIMINAR (antigua, aunque tenga _1)")
        else:
            log(f"  • {item['delete'].name} → ELIMINAR")
        if item.get("keep") is not None:
            log(f"    ↳ Conservar: {item['keep'].name}")
        else:
            log(f"    ↳ Conservar: N/A (carpeta vacía)")
        log(f"    ↳ Tamaño: {format_size(item['size'])}")
        if item['warning']:
            log(f"    ↳ {item['warning']}")
        log("")
        
        if item.get("status") in ("EMPTY_NO_BACKUPS", "OLD_EMPTY", "NEW_EMPTY"):
            empty_planned_count += 1

    if empty_planned_count > 0:
        log(f"🧹 Carpetas vacías planificadas para eliminar: {empty_planned_count}\n")
    
    # ═══════════════════════════════════════════════════════════
    # MODO SIMULACIÓN
    # ═══════════════════════════════════════════════════════════
    
    if not args.execute:
        log("\nEsto es una SIMULACIÓN. Para eliminar realmente, ejecuta:")
        log(f"   python3 {sys.argv[0]} --execute\n")
        if empty_planned_count > 0:
            log(f"Carpetas vacías planificadas para eliminar: {empty_planned_count}")
        process_deletions(candidates, backup_dir=backup_dir, dry_run=True, allow_symlinks=args.allow_symlinks)
        log(f"Log guardado en: {LOG_FILE}")
        return 0
    
    # ═══════════════════════════════════════════════════════════
    # MODO EJECUCIÓN REAL - PEDIR CONFIRMACIÓN
    # ═══════════════════════════════════════════════════════════
    
    if not args.yes:
        log("\n" + "!" * 80)
        log(" ADVERTENCIA: Vas a ELIMINAR permanentemente estas carpetas")
        log(" El script eliminará SIEMPRE los backups MÁS ANTIGUOS")
        log(" Esta operación NO se puede deshacer")
        log("!" * 80 + "\n")
        
        response = input("¿Estás seguro? Escribe 'SI' en MAYÚSCULAS para continuar: ")
        
        if response != "SI":
            log(" Operación cancelada por el usuario")
            return 1
        
        log("\n Confirmación recibida. Iniciando eliminación...\n")
    
    # ═══════════════════════════════════════════════════════════
    # EJECUTAR ELIMINACIÓN
    # ═══════════════════════════════════════════════════════════
    
    deleted, errors = process_deletions(
        candidates,
        backup_dir=backup_dir,
        dry_run=False,
        allow_symlinks=args.allow_symlinks
    )
    
    log(f"Log completo guardado en: {LOG_FILE}")
    
    if errors > 0:
        log(f"\n Se completó con {errors} error(es)")
        return 1
    
    log("\n Operación completada exitosamente")
    return 0


# ═══════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    sys.exit(main())
