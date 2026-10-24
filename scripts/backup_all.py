#!/usr/bin/env python
"""备份与恢复 DATA_RETENTION 契约要求的三件套（缺口 B-12）。

覆盖范围
--------
`docs/DATA_RETENTION.md` 明确："备份必须同时覆盖 `UPLOAD_DIR`、PostgreSQL 和审计日志"。
现有 `scripts/db_backup.py` 只覆盖数据库，本脚本补齐三件套：

1. **PostgreSQL**：`pg_dump` 纯文本转储（`--no-owner --no-acl`），gzip 压缩；
2. **`UPLOAD_DIR`**：原始 PDF 与任务产物，打成 `uploads.tar.gz`；
3. **审计日志**：`AUDIT_LOG_PATH` 指向的文件，原样复制。

同时产出 `manifest.json`：每个构件的字节数、sha256、来源路径、条目数，
恢复时先校验再落地——没有校验的备份等于没有备份。

安全约束（防止演练把生产/开发数据冲掉）
------------------------------------
`restore` 默认**拒绝**写入当前环境正在使用的 `UPLOAD_DIR` 与 `DATABASE_URL`，
必须显式给出临时目标；确实要覆盖时才需要 `--force`（并有二次确认提示语）。

用法
----
    # 备份（只读源数据）
    python scripts/backup_all.py create --output backups/2026-08-27

    # 校验归档完整性
    python scripts/backup_all.py verify --archive backups/2026-08-27

    # 恢复到临时库与临时目录（演练）
    python scripts/backup_all.py restore --archive backups/2026-08-27 \
        --uploads-dir /tmp/restore-uploads \
        --database-url postgres://user:pw@localhost:5432/drill_db \
        --audit-log /tmp/restore-audit.jsonl
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MANIFEST_NAME = "manifest.json"
DATABASE_ARTIFACT = "database.sql.gz"
UPLOADS_ARTIFACT = "uploads.tar.gz"
AUDIT_ARTIFACT = "audit-log"
STATE_ARTIFACT = "application-state.tar.gz"

#: pg 客户端工具的常见安装位置（PATH 里没有时按序探测）
_PG_BIN_CANDIDATES = (
    "C:/Program Files/PostgreSQL/17/bin",
    "C:/Program Files/PostgreSQL/16/bin",
    "C:/Program Files/PostgreSQL/15/bin",
    "C:/Program Files/PostgreSQL/14/bin",
    "/usr/lib/postgresql/16/bin",
    "/usr/lib/postgresql/15/bin",
    "/usr/local/bin",
)


class BackupError(RuntimeError):
    """备份或恢复失败。"""


# ---------------------------------------------------------------------------
# 工具解析与通用助手
# ---------------------------------------------------------------------------
def resolve_pg_tool(name: str) -> str:
    """定位 pg_dump / psql 可执行文件。

    优先 `PG_BIN_DIR`，其次 PATH，最后探测常见安装目录。
    本机（Windows + PostgreSQL 15）安装目录不在 PATH 上，
    如果只按名字调用会直接 FileNotFoundError，演练根本跑不起来。
    """
    explicit = str(os.getenv("PG_BIN_DIR", "") or "").strip()
    if explicit:
        candidate = Path(explicit) / name
        for suffix in ("", ".exe"):
            if (candidate.parent / f"{candidate.name}{suffix}").is_file():
                return str(candidate.parent / f"{candidate.name}{suffix}")

    found = shutil.which(name)
    if found:
        return found

    for directory in _PG_BIN_CANDIDATES:
        for suffix in ("", ".exe"):
            candidate = Path(directory) / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)

    raise BackupError(
        f"未找到 {name}。请安装 PostgreSQL 客户端工具，或用 PG_BIN_DIR 指定其 bin 目录。"
    )


def parse_database_url(database_url: str) -> Dict[str, Any]:
    parsed = urlparse(database_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "").lstrip("/") or "postgres",
    }


def redact_database_url(database_url: str) -> str:
    """输出/落盘时隐去口令，manifest 与日志里不能出现明文密码。"""
    info = parse_database_url(database_url)
    return f"postgres://{info['user']}:***@{info['host']}:{info['port']}/{info['database']}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------
def dump_database(database_url: str, target: Path) -> Dict[str, Any]:
    """pg_dump → gzip。只读操作，不会改动源库。"""
    info = parse_database_url(database_url)
    env = os.environ.copy()
    if info["password"]:
        env["PGPASSWORD"] = info["password"]

    command = [
        resolve_pg_tool("pg_dump"),
        "-h",
        str(info["host"]),
        "-p",
        str(info["port"]),
        "-U",
        str(info["user"]),
        "-d",
        str(info["database"]),
        "-F",
        "p",
        "--no-owner",
        "--no-acl",
    ]

    started = time.time()
    # 用 Python 的 gzip 压缩，不依赖外部 gzip 可执行文件（Windows 上没有）
    with gzip.open(target, "wb") as sink:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        shutil.copyfileobj(process.stdout, sink)
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        returncode = process.wait()
    if returncode != 0:
        raise BackupError(f"pg_dump 失败（exit={returncode}）：{stderr.strip()[:500]}")

    return {
        "artifact": target.name,
        "source": redact_database_url(database_url),
        "database": info["database"],
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "elapsed_seconds": round(time.time() - started, 3),
    }


def archive_uploads(uploads_dir: Path, target: Path) -> Dict[str, Any]:
    """把 UPLOAD_DIR 打成 tar.gz。只读源目录。"""
    if not uploads_dir.is_dir():
        raise BackupError(f"UPLOAD_DIR 不存在：{uploads_dir}")

    started = time.time()
    file_count = 0
    total_bytes = 0
    # 历史证据采用硬链接节省磁盘；备份应保存每条路径的普通文件内容。
    # 下方仍逐项拒绝符号链接，恢复器无需放宽对归档链接成员的安全限制。
    with tarfile.open(target, "w:gz", dereference=True) as tar:
        for path in sorted(uploads_dir.rglob("*")):
            relative = path.relative_to(uploads_dir).as_posix()
            if path.is_symlink():
                raise BackupError(f"源目录存在链接，不能完整备份：{relative}")
            if path.is_file():
                file_count += 1
                total_bytes += path.stat().st_size
                tar.add(str(path), arcname=relative, recursive=False)
            elif path.is_dir():
                tar.add(str(path), arcname=relative, recursive=False)

    return {
        "artifact": target.name,
        "source": uploads_dir.as_posix(),
        "file_count": file_count,
        "source_bytes": total_bytes,
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "elapsed_seconds": round(time.time() - started, 3),
    }


def copy_audit_log(audit_log_path: Path, target_dir: Path) -> Dict[str, Any]:
    """复制审计日志。文件不存在时如实记录 present=false，不静默跳过。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    if not audit_log_path.is_file():
        return {
            "artifact": f"{AUDIT_ARTIFACT}/",
            "source": audit_log_path.as_posix(),
            "present": False,
            "note": "审计日志文件不存在（可能尚无管理员敏感操作）",
        }

    target = target_dir / audit_log_path.name
    shutil.copy2(audit_log_path, target)
    target.chmod(0o600)
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        line_count = sum(1 for _ in handle)
    return {
        "artifact": f"{AUDIT_ARTIFACT}/{audit_log_path.name}",
        "source": audit_log_path.as_posix(),
        "present": True,
        "filename": audit_log_path.name,
        "line_count": line_count,
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }


def application_state_paths() -> Dict[str, Path]:
    """These files are runtime authorities outside UPLOAD_DIR, not DB mirrors."""
    data = (
        Path(tempfile.gettempdir()) / "govbudgetchecker_test_data"
        if os.getenv("TESTING", "").lower() in {"true", "1", "yes"}
        else _REPO_ROOT / "data"
    )
    return {
        "job_org_links.json": Path(os.getenv("ORG_DATA_DIR", str(data))) / "job_org_links.json",
        "organizations.json": Path(os.getenv("ORG_DATA_DIR", str(data))) / "organizations.json",
        "users.json": Path(
            os.getenv("USER_FILE", str(Path(os.getenv("USER_DATA_DIR", str(data))) / "users.json"))
        ),
    }


def archive_application_state(target: Path) -> Dict[str, Any]:
    files = {}
    with tarfile.open(target, "w:gz", dereference=True) as tar:
        for name, path in application_state_paths().items():
            if path.is_symlink():
                raise BackupError(f"应用状态文件是链接，拒绝备份：{name}")
            files[name] = {"present": path.is_file()}
            if path.is_file():
                files[name]["sha256"] = sha256_file(path)
                tar.add(path, arcname=name, recursive=False)
    target.chmod(0o600)
    return {
        "artifact": target.name,
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "files": files,
    }


def restore_application_state(archive: Path, target_dir: Path) -> Dict[str, Any]:
    info = load_manifest(archive).get("artifacts", {}).get("application_state")
    if not info:
        raise BackupError("旧备份不包含组织/用户文件，无法完整恢复应用状态")
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(archive / STATE_ARTIFACT, "r:gz") as tar:
        members = tar.getmembers()
        seen = set()
        for member in members:
            if (
                member.name not in {"organizations.json", "users.json", "job_org_links.json"}
                or not member.isfile()
                or member.name in seen
            ):
                raise BackupError("应用状态归档存在异常成员")
            target = target_dir / member.name
            if target.exists() or target.is_symlink():
                raise BackupError(f"应用状态恢复目标已存在：{target}")
            seen.add(member.name)
        for member in members:
            source = tar.extractfile(member)
            if source is None:
                raise BackupError("无法读取应用状态归档成员")
            target = target_dir / member.name
            with target.open("xb") as sink:
                shutil.copyfileobj(source, sink)
            target.chmod(0o600)
            if sha256_file(target) != info["files"][member.name]["sha256"]:
                raise BackupError("应用状态恢复后的文件哈希不一致")
    return {"target": str(target_dir), "files": sorted(seen), "verified": True}


def create_backup(
    output: Path,
    *,
    uploads_dir: Path,
    database_url: str,
    audit_log_path: Path,
    include_database: bool = True,
) -> Dict[str, Any]:
    if output.resolve().is_relative_to(uploads_dir.resolve()):
        raise BackupError("备份输出不能位于上传源目录内")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (output / MANIFEST_NAME).exists():
        raise BackupError("备份目标已有归档，请使用新的目录")
    manifest: Dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool": "scripts/backup_all.py",
        "contract": "docs/DATA_RETENTION.md",
        "artifacts": {},
    }

    manifest["artifacts"]["application_state"] = archive_application_state(output / STATE_ARTIFACT)
    manifest["artifacts"]["uploads"] = archive_uploads(uploads_dir, output / UPLOADS_ARTIFACT)
    manifest["artifacts"]["audit_log"] = copy_audit_log(audit_log_path, output / AUDIT_ARTIFACT)
    if include_database and database_url.strip():
        manifest["artifacts"]["database"] = dump_database(database_url, output / DATABASE_ARTIFACT)
    else:
        manifest["artifacts"]["database"] = {
            "artifact": DATABASE_ARTIFACT,
            "present": False,
            "note": "未配置 DATABASE_URL 或显式跳过，数据库未纳入本次备份",
        }

    (output / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for artifact in output.iterdir():
        if artifact.is_file():
            artifact.chmod(0o600)
    return manifest


# ---------------------------------------------------------------------------
# 校验与恢复
# ---------------------------------------------------------------------------
def load_manifest(archive: Path) -> Dict[str, Any]:
    manifest_path = archive / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f"归档缺少 {MANIFEST_NAME}：{archive}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BackupError(f"{MANIFEST_NAME} 格式不正确")
    return payload


def verify_backup(archive: Path) -> Dict[str, Any]:
    """逐个构件校验存在性与 sha256。任一不符即视为归档不可用。"""
    manifest = load_manifest(archive)
    results: Dict[str, Any] = {}
    ok = True
    for name, info in (manifest.get("artifacts") or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("present") is False:
            results[name] = {"status": "absent", "note": info.get("note")}
            continue
        artifact_name = str(info.get("artifact") or "")
        if (
            not artifact_name
            or "\\" in artifact_name
            or Path(artifact_name).is_absolute()
            or not (archive / artifact_name).resolve().is_relative_to(archive.resolve())
        ):
            raise BackupError("备份清单存在越界构件路径")
        artifact_path = archive / artifact_name
        if not artifact_path.is_file():
            results[name] = {"status": "missing", "path": artifact_path.as_posix()}
            ok = False
            continue
        actual = sha256_file(artifact_path)
        expected = str(info.get("sha256") or "")
        matched = bool(expected) and actual == expected
        results[name] = {
            "status": "ok" if matched else "checksum_mismatch",
            "path": artifact_path.as_posix(),
            "bytes": artifact_path.stat().st_size,
            "sha256": actual,
        }
        if not matched:
            ok = False
    return {"ok": ok, "artifacts": results, "manifest": manifest}


def restore_uploads(archive: Path, target_dir: Path) -> Dict[str, Any]:
    source = archive / UPLOADS_ARTIFACT
    if not source.is_file():
        raise BackupError(f"归档缺少 {UPLOADS_ARTIFACT}")
    target_dir.mkdir(parents=True, exist_ok=True)
    if any(target_dir.iterdir()):
        raise BackupError("上传恢复目标必须为空，避免覆盖既有文件")
    with tarfile.open(source, "r:gz") as tar:
        for member in tar.getmembers():
            # 防路径穿越：拒绝绝对路径与 .. 跳出目标目录的成员
            member_path = (target_dir / member.name).resolve()
            if (
                not member_path.is_relative_to(target_dir.resolve())
                or "\\" in member.name
                or Path(member.name).is_absolute()
            ):
                raise BackupError(f"归档中存在越界路径，已中止：{member.name}")
            if not (member.isfile() or member.isdir()):
                raise BackupError(f"归档中存在链接或特殊文件，已中止：{member.name}")
        tar.extractall(str(target_dir))
    restored = [path for path in target_dir.rglob("*") if path.is_file()]
    return {
        "target": target_dir.as_posix(),
        "file_count": len(restored),
        "bytes": sum(path.stat().st_size for path in restored),
    }


def restore_database(archive: Path, database_url: str) -> Dict[str, Any]:
    source = archive / DATABASE_ARTIFACT
    if not source.is_file():
        raise BackupError(f"归档缺少 {DATABASE_ARTIFACT}")

    info = parse_database_url(database_url)
    env = os.environ.copy()
    if info["password"]:
        env["PGPASSWORD"] = info["password"]

    command = [
        resolve_pg_tool("psql"),
        "-h",
        str(info["host"]),
        "-p",
        str(info["port"]),
        "-U",
        str(info["user"]),
        "-d",
        str(info["database"]),
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
    ]

    started = time.time()
    with gzip.open(source, "rb") as handle:
        process = subprocess.Popen(
            command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdin is not None
        shutil.copyfileobj(handle, process.stdin)
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise BackupError(
            f"psql 恢复失败（exit={process.returncode}）："
            f"{stderr.decode('utf-8', errors='replace').strip()[:800]}"
        )
    return {
        "target": redact_database_url(database_url),
        "elapsed_seconds": round(time.time() - started, 3),
        "stdout_tail": stdout.decode("utf-8", errors="replace").strip()[-500:],
    }


def restore_audit_log(archive: Path, target_path: Path) -> Dict[str, Any]:
    audit_dir = archive / AUDIT_ARTIFACT
    files = sorted(audit_dir.glob("*")) if audit_dir.is_dir() else []
    if not files:
        return {"target": target_path.as_posix(), "present": False}
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(files[0], target_path)
    return {
        "target": target_path.as_posix(),
        "present": True,
        "bytes": target_path.stat().st_size,
        "sha256": sha256_file(target_path),
    }


def _guard_live_targets(
    *,
    force: bool,
    target_uploads: Optional[Path],
    target_database_url: Optional[str],
    target_audit_log: Optional[Path],
) -> List[str]:
    """拒绝把恢复结果写进当前环境正在使用的目标（除非 --force）。"""
    problems: List[str] = []
    live_uploads = Path(os.getenv("UPLOAD_DIR", "uploads"))
    live_db = str(os.getenv("DATABASE_URL", "") or "").strip()
    live_audit = str(os.getenv("AUDIT_LOG_PATH", "") or "").strip()

    if target_uploads is not None and _same_path(target_uploads, live_uploads):
        problems.append(f"--uploads-dir 指向当前 UPLOAD_DIR（{live_uploads}）")
    if target_database_url and live_db and target_database_url.strip() == live_db:
        problems.append("--database-url 与当前 DATABASE_URL 相同")
    if (
        target_audit_log is not None
        and live_audit
        and _same_path(target_audit_log, Path(live_audit))
    ):
        problems.append("--audit-log 指向当前 AUDIT_LOG_PATH")

    if problems and not force:
        return problems
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _default_audit_log_path() -> Path:
    configured = str(os.getenv("AUDIT_LOG_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    return Path("logs/admin-actions.jsonl")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="备份/校验/恢复 UPLOAD_DIR + PostgreSQL + 审计日志三件套",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建备份（只读源数据）")
    create.add_argument("--output", required=True, help="归档输出目录")
    create.add_argument("--uploads-dir", default=os.getenv("UPLOAD_DIR", "uploads"))
    create.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    create.add_argument("--audit-log", default=str(_default_audit_log_path()))
    create.add_argument("--skip-database", action="store_true", help="跳过数据库转储")

    verify = sub.add_parser("verify", help="校验归档完整性")
    verify.add_argument("--archive", required=True)

    restore = sub.add_parser("restore", help="恢复到指定目标（默认拒绝写入线上目标）")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--uploads-dir", default=None)
    restore.add_argument("--database-url", default=None)
    restore.add_argument("--audit-log", default=None)
    restore.add_argument(
        "--state-dir",
        default=None,
        help="组织/用户文件恢复目录；重启时将 ORG_DATA_DIR 和 USER_DATA_DIR 指向这里",
    )
    restore.add_argument(
        "--force",
        action="store_true",
        help="允许恢复到当前环境正在使用的 UPLOAD_DIR / DATABASE_URL / AUDIT_LOG_PATH",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        if args.command == "create":
            manifest = create_backup(
                Path(args.output).expanduser(),
                uploads_dir=Path(args.uploads_dir).expanduser(),
                database_url=str(args.database_url or ""),
                audit_log_path=Path(args.audit_log).expanduser(),
                include_database=not args.skip_database,
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0

        if args.command == "verify":
            report = verify_backup(Path(args.archive).expanduser())
            print(json.dumps(report["artifacts"], ensure_ascii=False, indent=2))
            print("归档校验：" + ("通过" if report["ok"] else "失败"))
            return 0 if report["ok"] else 1

        archive = Path(args.archive).expanduser()
        target_uploads = Path(args.uploads_dir).expanduser() if args.uploads_dir else None
        target_audit = Path(args.audit_log).expanduser() if args.audit_log else None
        problems = _guard_live_targets(
            force=bool(args.force),
            target_uploads=target_uploads,
            target_database_url=args.database_url,
            target_audit_log=target_audit,
        )
        if args.state_dir and not args.force:
            state_target = Path(args.state_dir).expanduser().resolve()
            if any(
                _same_path(state_target / name, path)
                for name, path in application_state_paths().items()
            ):
                problems.append("--state-dir 指向当前组织或用户文件目录")
        if problems:
            print("拒绝恢复，目标指向当前环境正在使用的数据：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("确认要覆盖请显式加 --force。", file=sys.stderr)
            return 2

        report = verify_backup(archive)
        if not report["ok"]:
            print("归档校验失败，拒绝恢复：", file=sys.stderr)
            print(json.dumps(report["artifacts"], ensure_ascii=False, indent=2), file=sys.stderr)
            return 2

        result: Dict[str, Any] = {"archive": archive.as_posix(), "verified": True}
        if args.state_dir:
            result["application_state"] = restore_application_state(
                archive, Path(args.state_dir).expanduser()
            )
        if target_uploads is not None:
            result["uploads"] = restore_uploads(archive, target_uploads)
        if args.database_url:
            result["database"] = restore_database(archive, str(args.database_url))
        if target_audit is not None:
            result["audit_log"] = restore_audit_log(archive, target_audit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BackupError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
