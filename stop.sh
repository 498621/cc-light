#!/usr/bin/env bash
# 停止 cc-light 菜单栏状态灯。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/cc-light.pid"

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE")"
  if kill "$PID" 2>/dev/null; then
    echo "已停止 cc-light（pid $PID）。"
  else
    echo "进程 $PID 不在了，清理 pid 文件。"
  fi
  rm -f "$PIDFILE"
else
  # 兜底：按脚本名杀。
  pkill -f "cc_light.py" 2>/dev/null && echo "已按进程名停止 cc-light。" || echo "cc-light 未在运行。"
fi
