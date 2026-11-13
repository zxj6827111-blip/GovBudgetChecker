#!/bin/sh
set -eu
# 从仓库根目录执行。先启动交接记录中相同的 OCR 镜像/模型，并传入真实端口。
# 例如本次镜像（本地开发验证镜像，非全新 Dockerfile 构建证明）：
# docker run -d --rm --name gbc-ocr-review --read-only --tmpfs /tmp:rw,size=1g \
#   -e PADDLE_HOME=/models -e OCR_CPU_THREADS=2 -p 127.0.0.1:9011:9010 \
#   govbudgetchecker-ocr:round11-offline-current
task_ocr_url="${1:?请传 OCR localhost URL}"
task_output="${2:?请传新的输出目录}"
if [ -e "$task_output" ]; then
  echo '输出目录已存在，请选择新目录以保留先前结果。' >&2
  exit 2
fi
task_model_sha="$(shasum -a 256 models/MANIFEST.sha256)"
task_model_sha="${task_model_sha%% *}"
.venv/bin/python scripts/run_isolated_service_validation.py \
  --output "$task_output" \
  --budget-pdf 'outputs/remediation_20261006_round11/development_materials/普陀区委社会工作部2025年部门预算.pdf' \
  --scanned-budget-pdf outputs/remediation_20261006_round11/development_materials/derived_scanned_budget.pdf \
  --mixed-final-pdf outputs/remediation_20261006_round11/development_materials/derived_mixed_final.pdf \
  --ocr-control-pdf outputs/remediation_20261006_round11/development_materials/synthetic_ocr_control_scan.pdf \
  --original-scanned-budget-pdf outputs/remediation_20261006_round13/original_materials/zhejiang_budget.pdf \
  --original-mixed-final-pdf outputs/remediation_20261006_round13/original_materials/zhejiang_final.pdf \
  --ocr-url "$task_ocr_url" --ocr-model-fingerprint "$task_model_sha" --timeout-seconds 3600
