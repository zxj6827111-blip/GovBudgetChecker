#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${REPO_ROOT}/.env"
  set +a
fi

usage() {
  cat <<'EOF'
用法：
  bash scripts/diagnose_report_visibility.sh <JOB_ID> [ORG_ID] [额外参数...]

示例：
  bash scripts/diagnose_report_visibility.sh ef67c3fa4030c28059323b0cd2b63931
  bash scripts/diagnose_report_visibility.sh ef67c3fa4030c28059323b0cd2b63931 362ef2f8090e
  bash scripts/diagnose_report_visibility.sh ef67c3fa4030c28059323b0cd2b63931 --frontend-url http://127.0.0.1:3000

环境变量：
  GOVBUDGET_API_KEY / BACKEND_API_KEY  自动作为 --api-key
  DIAG_FRONTEND_URL                    默认前端地址，未设时为 http://127.0.0.1:3001
  DIAG_BACKEND_URL                     默认后端地址，未设时为 http://127.0.0.1:8000
  DIAG_COMPOSE_FILE                    默认 compose 文件，未设时为 docker-compose.ai.yml
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

JOB_ID="$1"
shift

ORG_ID=""
if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  ORG_ID="$1"
  shift
fi

FRONTEND_URL="${DIAG_FRONTEND_URL:-http://127.0.0.1:3001}"
BACKEND_URL="${DIAG_BACKEND_URL:-http://127.0.0.1:8000}"
COMPOSE_FILE="${DIAG_COMPOSE_FILE:-docker-compose.ai.yml}"
API_KEY="${DIAG_API_KEY:-${GOVBUDGET_API_KEY:-${BACKEND_API_KEY:-}}}"

CMD=(
  python3
  "${REPO_ROOT}/scripts/diagnose_report_visibility.py"
  --job-id "${JOB_ID}"
  --frontend-url "${FRONTEND_URL}"
  --backend-url "${BACKEND_URL}"
  --compose-file "${COMPOSE_FILE}"
)

if [[ -n "${ORG_ID}" ]]; then
  CMD+=(--org-id "${ORG_ID}")
fi

if [[ -n "${API_KEY}" ]]; then
  CMD+=(--api-key "${API_KEY}")
fi

if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi

cd "${REPO_ROOT}"
echo "执行诊断命令：${CMD[*]}"
exec "${CMD[@]}"
