"""
Database Backup Module

Provides automated backup functionality for PostgreSQL database.
"""
import os
import subprocess
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import asyncio
import asyncpg

logger = logging.getLogger(__name__)


class DatabaseBackup:
    def __init__(
        self,
        database_url: str,
        backup_dir: str = "backups",
        retention_days: int = 7,
        compress: bool = True
    ):
        self.database_url = database_url
        self.backup_dir = Path(backup_dir)
        self.retention_days = retention_days
        self.compress = compress
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def _parse_database_url(self) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(self.database_url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "user": parsed.username or "postgres",
            "password": parsed.password or "",
            "database": parsed.path.lstrip("/") or "postgres",
        }
    
    def create_backup(self, name: Optional[str] = None) -> dict:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = name or f"backup_{timestamp}"
        
        if self.compress:
            backup_file = self.backup_dir / f"{backup_name}.sql.gz"
        else:
            backup_file = self.backup_dir / f"{backup_name}.sql"
        
        db_info = self._parse_database_url()
        
        env = os.environ.copy()
        env["PGPASSWORD"] = db_info["password"]
        
        cmd = [
            "pg_dump",
            "-h", db_info["host"],
            "-p", str(db_info["port"]),
            "-U", db_info["user"],
            "-d", db_info["database"],
            "-F", "p",
            "--no-owner",
            "--no-acl",
        ]
        
        try:
            start_time = time.time()
            
            if self.compress:
                with open(backup_file, "wb") as f:
                    dump_process = subprocess.Popen(
                        cmd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    gzip_process = subprocess.Popen(
                        ["gzip", "-c"],
                        stdin=dump_process.stdout,
                        stdout=f,
                        stderr=subprocess.PIPE
                    )
                    dump_process.stdout.close()
                    gzip_process.communicate()
                    dump_process.wait()
            else:
                with open(backup_file, "w", encoding="utf-8") as f:
                    result = subprocess.run(
                        cmd,
                        env=env,
                        stdout=f,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    if result.returncode != 0:
                        raise Exception(f"pg_dump failed: {result.stderr}")
            
            elapsed = time.time() - start_time
            file_size = backup_file.stat().st_size
            
            logger.info(f"Backup created: {backup_file} ({file_size} bytes, {elapsed:.2f}s)")
            
            return {
                "success": True,
                "file": str(backup_file),
                "size_bytes": file_size,
                "elapsed_seconds": elapsed,
                "timestamp": timestamp,
            }
        
        except FileNotFoundError:
            logger.error("pg_dump not found. Please install PostgreSQL client tools.")
            return {
                "success": False,
                "error": "pg_dump not found. Install postgresql-client.",
            }
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            env.pop("PGPASSWORD", None)
    
    def restore_backup(self, backup_file: str) -> dict:
        backup_path = Path(backup_file)
        
        if not backup_path.exists():
            return {"success": False, "error": f"Backup file not found: {backup_file}"}
        
        db_info = self._parse_database_url()
        env = os.environ.copy()
        env["PGPASSWORD"] = db_info["password"]
        
        cmd = [
            "psql",
            "-h", db_info["host"],
            "-p", str(db_info["port"]),
            "-U", db_info["user"],
            "-d", db_info["database"],
            "-v", "ON_ERROR_STOP=1",
        ]
        
        try:
            start_time = time.time()
            
            if backup_path.suffix == ".gz":
                with open(backup_path, "rb") as f:
                    gunzip_process = subprocess.Popen(
                        ["gunzip", "-c"],
                        stdin=f,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    result = subprocess.run(
                        cmd,
                        env=env,
                        stdin=gunzip_process.stdout,
                        capture_output=True,
                        text=True
                    )
                    gunzip_process.wait()
            else:
                with open(backup_path, "r", encoding="utf-8") as f:
                    result = subprocess.run(
                        cmd,
                        env=env,
                        stdin=f,
                        capture_output=True,
                        text=True
                    )
            
            if result.returncode != 0:
                raise Exception(f"psql restore failed: {result.stderr}")
            
            elapsed = time.time() - start_time
            logger.info(f"Backup restored: {backup_file} ({elapsed:.2f}s)")
            
            return {
                "success": True,
                "file": backup_file,
                "elapsed_seconds": elapsed,
            }
        
        except FileNotFoundError:
            logger.error("psql not found. Please install PostgreSQL client tools.")
            return {
                "success": False,
                "error": "psql not found. Install postgresql-client.",
            }
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            env.pop("PGPASSWORD", None)
    
    def cleanup_old_backups(self) -> List[str]:
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        deleted_files = []
        
        for backup_file in self.backup_dir.glob("backup_*.sql*"):
            try:
                file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    backup_file.unlink()
                    deleted_files.append(str(backup_file))
                    logger.info(f"Deleted old backup: {backup_file}")
            except Exception as e:
                logger.warning(f"Failed to delete backup {backup_file}: {e}")
        
        return deleted_files
    
    def list_backups(self) -> List[dict]:
        backups = []
        
        for backup_file in sorted(self.backup_dir.glob("backup_*.sql*"), reverse=True):
            try:
                stat = backup_file.stat()
                backups.append({
                    "file": str(backup_file),
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "compressed": backup_file.suffix == ".gz",
                })
            except Exception as e:
                logger.warning(f"Failed to read backup info {backup_file}: {e}")
        
        return backups


async def schedule_backup(
    database_url: str,
    interval_hours: int = 24,
    backup_dir: str = "backups",
    retention_days: int = 7
):
    backup = DatabaseBackup(
        database_url=database_url,
        backup_dir=backup_dir,
        retention_days=retention_days
    )
    
    while True:
        try:
            logger.info("Starting scheduled backup...")
            result = backup.create_backup()
            
            if result["success"]:
                logger.info(f"Scheduled backup completed: {result['file']}")
                deleted = backup.cleanup_old_backups()
                if deleted:
                    logger.info(f"Cleaned up {len(deleted)} old backup(s)")
            else:
                logger.error(f"Scheduled backup failed: {result.get('error')}")
        
        except Exception as e:
            logger.exception(f"Backup scheduler error: {e}")
        
        await asyncio.sleep(interval_hours * 3600)


def create_backup_command():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        return 1
    
    backup = DatabaseBackup(database_url)
    result = backup.create_backup()
    
    if result["success"]:
        print(f"Backup created successfully: {result['file']}")
        print(f"Size: {result['size_bytes']} bytes")
        print(f"Time: {result['elapsed_seconds']:.2f}s")
        return 0
    else:
        print(f"Backup failed: {result.get('error')}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(create_backup_command())
