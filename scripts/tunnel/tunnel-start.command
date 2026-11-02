#!/bin/bash
# 双击运行：把本机 3000 端口（GovBudgetChecker 前端）发布到 govbudget.2729453.xyz
# 关闭窗口或 Ctrl+C 即收回外网访问。
set -u

TOKEN="$HOME/.cloudflared/govbudget.token"
LOG="$HOME/Library/Logs/govbudget-tunnel.log"

if [ ! -f "$TOKEN" ]; then
  echo "缺少 token 文件：$TOKEN"
  echo
  echo "到 Cloudflare 后台 → Networks → Tunnels → govbudget → 配置，复制 token，然后在本窗口执行："
  echo "  pbpaste | tr -d '\\r\\n' > ~/.cloudflared/govbudget.token && chmod 600 ~/.cloudflared/govbudget.token"
  echo
  read -r -p "按回车关闭窗口..." _
  exit 1
fi

if ! curl -s -o /dev/null --max-time 3 http://127.0.0.1:3000/; then
  echo "警告：127.0.0.1:3000 没有响应。"
  echo "（若 3000 被占用，next dev 会自动换到 3001，而隧道固定指向 3000，会返回 502。）"
  echo
  read -r -p "按回车仍然启动，或 Ctrl+C 退出..." _
fi

mkdir -p "$(dirname "$LOG")"
echo "外网地址：https://govbudget.2729453.xyz"
echo "关闭此窗口或按 Ctrl+C 即收回外网访问。"
echo "日志：$LOG"
echo
/opt/homebrew/bin/cloudflared tunnel run --protocol http2 --no-autoupdate --token-file "$TOKEN" 2>&1 | tee -a "$LOG"
