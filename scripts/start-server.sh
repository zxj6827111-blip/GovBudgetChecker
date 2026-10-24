#!/usr/bin/env bash
# Start the complete server stack; no host Python or Node.js is required.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

env_file="$project_root/.env"
compose_files=(-f "$project_root/docker-compose.yml")
build_options=(--build)
check_only=false

usage() {
  echo "用法：bash scripts/start-server.sh [--check] [--no-build] [--ai] [--env-file 文件]"
  echo "默认启动 PostgreSQL、OCR、API、Worker、网页，并等待服务就绪。"
  echo "--check 仅检查配置；--no-build 使用已有镜像；--ai 额外启动独立 AI 抽取器。"
}

while (($#)); do
  case "$1" in
    --check) check_only=true; shift ;;
    --no-build) build_options=(--no-build); shift ;;
    --ai) compose_files+=(-f "$project_root/docker-compose.ai.yml"); shift ;;
    --env-file)
      if (($# < 2)); then usage >&2; exit 2; fi
      env_file="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "$env_file" ]]; then
  echo "缺少配置文件：${env_file}。首次部署请复制 .env.example 并填写服务器配置。" >&2
  exit 1
fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "请先安装 Docker Engine 与 Docker Compose 插件。" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker 未运行或当前用户无法连接 Docker，请先启动 Docker 服务。" >&2
  exit 1
fi

compose=(docker compose --env-file "$env_file" "${compose_files[@]}")
"${compose[@]}" config --quiet
if [[ ! -f models/MANIFEST.sha256 || ! -f models/MODELS.json ]]; then
  echo "缺少离线 OCR 模型包。请将完整模型目录放入 models/，并配置 OCR_MODEL_FINGERPRINT。" >&2
  exit 1
fi
if "$check_only"; then
  echo "Compose 配置有效，OCR 模型清单文件存在；权重校验在启动时执行。未修改服务。"
  exit 0
fi

echo "正在构建并启动服务器服务，等待数据库、OCR、队列和网页就绪…"
if ! "${compose[@]}" up -d "${build_options[@]}" --wait --wait-timeout 600; then
  "${compose[@]}" ps
  echo "服务尚未全部就绪。请使用 docker compose logs 检查失败服务后重新执行。" >&2
  exit 1
fi

# Health checks prove service readiness; also prove the API uses this OCR bundle.
"${compose[@]}" exec -T backend python -c '
import json, os, urllib.request
url = os.environ["OCR_SERVICE_URL"].rstrip("/") + "/health"
health = json.load(urllib.request.urlopen(url, timeout=10))
expected = os.environ.get("OCR_MODEL_FINGERPRINT", "").strip()
if not expected or not health.get("models_ready") or expected != health.get("model_fingerprint"):
    raise SystemExit("OCR 模型指纹未配置或与实际服务不一致，请核对 OCR_MODEL_FINGERPRINT。")
'
"${compose[@]}" ps
echo "服务已就绪。默认网页地址：http://服务器IP:3001；已有数据保存在持久化卷与挂载目录中。"
