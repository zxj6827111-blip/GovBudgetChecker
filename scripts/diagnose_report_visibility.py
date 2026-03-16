#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def print_check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK" if ok else "FAIL"
    suffix = f" | {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}")


def run_command(command: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    text = output if output else error
    if completed.returncode != 0:
        return False, text or f"exit code {completed.returncode}"
    return True, text


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = 12,
) -> tuple[bool, int | None, dict[str, str], bytes, str]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return True, resp.status, dict(resp.headers.items()), data, ""
    except urllib.error.HTTPError as exc:
        data = exc.read()
        return False, exc.code, dict(exc.headers.items()), data, str(exc)
    except Exception as exc:
        return False, None, {}, b"", str(exc)


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 12,
) -> tuple[bool, int | None, Any]:
    ok, status, _headers, body, error = fetch(url, timeout=timeout, headers=headers)
    if not ok:
        text = body.decode("utf-8", errors="replace") if body else error
        return False, status, text
    try:
        return True, status, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return False, status, body.decode("utf-8", errors="replace")


def compact_json(payload: Any, *, limit: int = 260) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def derive_org_id(job_payload: Any) -> str | None:
    if not isinstance(job_payload, dict):
        return None
    raw = str(job_payload.get("organization_id") or "").strip()
    return raw or None


def extract_report_kind(job_payload: Any) -> str:
    if not isinstance(job_payload, dict):
        return ""
    return str(job_payload.get("report_kind") or "").strip().lower()


def compose_base_command(compose_file: str) -> list[str] | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    return [docker, "compose", "-f", compose_file]


def inspect_container_env(
    compose_cmd: list[str] | None,
    repo_root: Path,
    service: str,
    env_keys: list[str],
) -> tuple[bool, str]:
    if not compose_cmd:
        return False, "docker 不可用"
    cmd = compose_cmd + [
        "exec",
        "-T",
        service,
        "sh",
        "-lc",
        " && ".join([f'printf "{key}=%s\\n" "${key}"' for key in env_keys]),
    ]
    return run_command(cmd, repo_root)


def inspect_job_files(
    compose_cmd: list[str] | None,
    repo_root: Path,
    job_id: str,
) -> tuple[bool, str]:
    if not compose_cmd:
        return False, "docker 不可用"
    cmd = compose_cmd + [
        "exec",
        "-T",
        "backend",
        "sh",
        "-lc",
        (
            f'echo "-- uploads/{job_id} --"; '
            f'ls -lah /app/uploads/{job_id} 2>/dev/null || true; '
            f'echo "\\n-- status.json --"; '
            f'sed -n "1,120p" /app/uploads/{job_id}/status.json 2>/dev/null || true'
        ),
    ]
    return run_command(cmd, repo_root)


def analyze_results(
    *,
    backend_job_ok: bool,
    frontend_job_ok: bool,
    org_job_found: bool | None,
    source_ok: bool | None,
    preview_ok: bool | None,
    report_kind: str,
) -> list[str]:
    hints: list[str] = []

    if backend_job_ok and not frontend_job_ok:
        hints.append("后端已有任务，但前端取不到：优先检查前端容器的 BACKEND_URL、GOVBUDGET_API_KEY、反向代理。")
    if backend_job_ok and frontend_job_ok and org_job_found is False:
        hints.append("任务存在但不在组织列表里：优先检查 organization_id 绑定、部门页面 include_children 和组织筛选。")
    if backend_job_ok and frontend_job_ok and report_kind == "final":
        hints.append("如果你当前在部门页“预算报告”标签，新上传的决算会被隐藏；切到“决算报告”再看。")
    if source_ok is False or preview_ok is False:
        hints.append("文件流或预览失败：优先检查 backend 的 UPLOAD_DIR、宿主机 HOST_UPLOADS_DIR 挂载、任务目录是否真的有 PDF。")
    if not hints:
        hints.append("链路看起来基本正常；如果前台仍看不到，优先复查当前页面标签、年份筛选、状态筛选。")
    return hints


def choose_frontend_url(candidate: str, headers: dict[str, str]) -> str:
    ok, status, _payload = fetch_json(f"{candidate}/api/jobs", headers=headers, timeout=6)
    if ok and status == 200:
        return candidate

    if candidate.endswith(":3001"):
        fallback = candidate[:-5] + ":3000"
        ok, status, _payload = fetch_json(f"{fallback}/api/jobs", headers=headers, timeout=6)
        if ok and status == 200:
            return fallback
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断上传报告后前台不显示的问题")
    parser.add_argument("--job-id", help="要排查的任务 ID")
    parser.add_argument("--org-id", help="组织 ID；不传时会尝试从任务详情自动推断")
    parser.add_argument("--frontend-url", default=os.getenv("DIAG_FRONTEND_URL", "http://127.0.0.1:3001"))
    parser.add_argument("--backend-url", default=os.getenv("DIAG_BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--compose-file", default="docker-compose.ai.yml")
    parser.add_argument(
        "--api-key",
        default=os.getenv("GOVBUDGET_API_KEY") or os.getenv("BACKEND_API_KEY") or "",
        help="后端 API Key；未传则尝试读取环境变量 GOVBUDGET_API_KEY/BACKEND_API_KEY",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    compose_cmd = compose_base_command(args.compose_file)
    request_headers = {"X-API-Key": args.api_key} if args.api_key else {}
    frontend_url = choose_frontend_url(args.frontend_url, request_headers)

    print("GovBudgetChecker 报告可见性一键诊断")
    print(f"项目目录: {repo_root}")
    print(f"前端地址: {frontend_url}")
    print(f"后端地址: {args.backend_url}")
    if args.job_id:
        print(f"任务 ID: {args.job_id}")
    if args.org_id:
        print(f"组织 ID: {args.org_id}")

    print_section("容器状态")
    if compose_cmd:
        ok, output = run_command(compose_cmd + ["ps"], repo_root)
        print_check("docker compose ps", ok, output.splitlines()[0] if output else "")
        if output:
            print(output)
    else:
        print_check("docker compose ps", False, "未找到 docker 命令")

    print_section("关键环境变量")
    ok, output = inspect_container_env(
        compose_cmd,
        repo_root,
        "frontend",
        ["BACKEND_URL", "GOVBUDGET_API_KEY"],
    )
    print_check("frontend env", ok, output.replace("\n", " | "))

    ok, output = inspect_container_env(
        compose_cmd,
        repo_root,
        "backend",
        ["UPLOAD_DIR", "GOVBUDGET_API_KEY"],
    )
    print_check("backend env", ok, output.replace("\n", " | "))

    print_section("基础连通性")
    backend_ok, backend_status, backend_health = fetch_json(
        f"{args.backend_url}/health",
        headers=request_headers,
    )
    print_check("backend /health", backend_ok, f"status={backend_status} body={compact_json(backend_health)}")

    frontend_jobs_ok, frontend_jobs_status, frontend_jobs_payload = fetch_json(
        f"{frontend_url}/api/jobs",
        headers=request_headers,
    )
    detail = (
        f"status={frontend_jobs_status} count={len(frontend_jobs_payload) if isinstance(frontend_jobs_payload, list) else 'unknown'}"
        if frontend_jobs_ok
        else f"status={frontend_jobs_status} body={compact_json(frontend_jobs_payload)}"
    )
    print_check("frontend /api/jobs", frontend_jobs_ok, detail)

    backend_job_ok = False
    frontend_job_ok = False
    source_ok: bool | None = None
    preview_ok: bool | None = None
    org_job_found: bool | None = None
    report_kind = ""
    resolved_org_id = args.org_id

    if args.job_id:
        print_section("任务详情")
        backend_job_ok, backend_job_status, backend_job_payload = fetch_json(
            f"{args.backend_url}/api/jobs/{args.job_id}",
            headers=request_headers,
        )
        print_check(
            "backend job detail",
            backend_job_ok,
            f"status={backend_job_status} body={compact_json(backend_job_payload)}",
        )

        frontend_job_ok, frontend_job_status, frontend_job_payload = fetch_json(
            f"{frontend_url}/api/jobs/{args.job_id}",
            headers=request_headers,
        )
        print_check(
            "frontend job detail",
            frontend_job_ok,
            f"status={frontend_job_status} body={compact_json(frontend_job_payload)}",
        )

        if not resolved_org_id:
            resolved_org_id = derive_org_id(backend_job_payload) or derive_org_id(frontend_job_payload)
        report_kind = extract_report_kind(backend_job_payload) or extract_report_kind(frontend_job_payload)

        print_section("文件接口")
        source_ok, source_status, source_headers, _source_body, source_error = fetch(
            f"{frontend_url}/api/files/{args.job_id}/source",
            method="HEAD",
            headers=request_headers,
        )
        print_check(
            "source pdf",
            source_ok,
            f"status={source_status} content-type={source_headers.get('Content-Type', '') or source_headers.get('content-type', '') or source_error}",
        )

        preview_ok, preview_status, preview_headers, preview_body, preview_error = fetch(
            f"{frontend_url}/api/files/{args.job_id}/preview?page=1&scale=1.6&padding=0",
            headers=request_headers,
        )
        print_check(
            "preview image",
            preview_ok,
            (
                f"status={preview_status} content-type={preview_headers.get('Content-Type', '') or preview_headers.get('content-type', '')} bytes={len(preview_body)}"
                if preview_ok
                else f"status={preview_status} error={preview_error}"
            ),
        )

        print_section("后端任务目录")
        ok, output = inspect_job_files(compose_cmd, repo_root, args.job_id)
        print_check("backend uploads dir", ok, output.splitlines()[0] if output else "")
        if output:
            print(output)

    if resolved_org_id:
        print_section("组织任务列表")
        org_ok, org_status, org_payload = fetch_json(
            f"{args.backend_url}/api/organizations/{resolved_org_id}/jobs?include_children=true",
            headers=request_headers,
        )
        if org_ok and isinstance(org_payload, dict):
            jobs = org_payload.get("jobs")
            if isinstance(jobs, list) and args.job_id:
                org_job_found = any(str(item.get("job_id") or "") == args.job_id for item in jobs if isinstance(item, dict))
            print_check(
                "organization jobs",
                True,
                f"status={org_status} jobs={len(jobs) if isinstance(jobs, list) else 'unknown'} found_job={org_job_found}",
            )
        else:
            print_check("organization jobs", False, f"status={org_status} body={compact_json(org_payload)}")

    print_section("诊断建议")
    for hint in analyze_results(
        backend_job_ok=backend_job_ok,
        frontend_job_ok=frontend_job_ok,
        org_job_found=org_job_found,
        source_ok=source_ok,
        preview_ok=preview_ok,
        report_kind=report_kind,
    ):
        print(f"- {hint}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
