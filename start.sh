#!/usr/bin/env bash
# 启动 cc-light 菜单栏状态灯（后台常驻）。
# 菜单栏 GUI 程序在 Anaconda 这类非 framework Python 下用 pythonw 更稳（图标才显示），
# 有 pythonw 就优先用它，否则退回 python3。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/cc-light.log"
PIDFILE="$DIR/cc-light.pid"

# 已在运行则不重复起。
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "cc-light 已在运行（pid $(cat "$PIDFILE")）。先 ./stop.sh 再启动。"
  exit 0
fi

if command -v pythonw >/dev/null 2>&1; then
  PY=pythonw
else
  PY=python3
fi

# rumps 缺失时给出明确提示，别让用户对着空日志猜。
if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "缺少 rumps 依赖，先安装：pip3 install rumps"
  exit 1
fi

nohup "$PY" "$DIR/cc_light.py" >"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "cc-light 已启动（pid $!），日志：$LOG"
echo "看菜单栏右上角的圆点图标；若没出现，见 README 的 pythonw 说明。"
