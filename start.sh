#!/usr/bin/env bash
# 启动 cc-light 菜单栏状态灯。
# 首次运行会自动：装 rumps 依赖、幂等配置 Claude Code hooks、把 `alias cc-light` 写进你的 shell profile。
# 之后新开终端直接输入 cc-light 即可启动。停止请点菜单栏「退出 cc-light」；开机自启在菜单里勾选。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1) 选解释器：优先 pythonw（Anaconda 等非 framework Python 下菜单栏图标更稳）。
if command -v pythonw >/dev/null 2>&1; then
  PY="$(command -v pythonw)"
else
  PY="$(command -v python3)"
fi

# 2) 确保 rumps 可被该解释器导入。
if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "安装 rumps ..."
  python3 -m pip install rumps 2>/dev/null || pip3 install rumps 2>/dev/null || true
fi
if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "rumps 不可用，请先运行：pip3 install rumps" >&2
  exit 1
fi

# 3) 幂等配置 Claude Code hooks（合并进 ~/.claude/settings.json，不动其它配置）。
python3 "$DIR/scripts/install_hooks.py" install

# 4) 把 alias cc-light 写入 shell profile（幂等；只在缺失时追加）。
case "${SHELL:-}" in
  *zsh) PROFILE="$HOME/.zshrc" ;;
  *bash) PROFILE="$HOME/.bash_profile" ;;
  *) PROFILE="$HOME/.zshrc" ;;
esac
if ! grep -qF "alias cc-light=" "$PROFILE" 2>/dev/null; then
  {
    echo ""
    echo "# cc-light 菜单栏状态灯"
    echo "alias cc-light=\"$DIR/start.sh\""
  } >>"$PROFILE"
  echo "已写入 alias 到 $PROFILE —— 新开终端后可直接输入 cc-light 启动。"
fi

# 5) 启动（已在运行则不重复；cc_light.py 自身也有单实例锁兜底）。
if pgrep -f "cc_light.py" >/dev/null 2>&1; then
  echo "cc-light 已在运行。"
  exit 0
fi
nohup "$PY" "$DIR/cc_light.py" >"$DIR/cc-light.log" 2>&1 &
echo "cc-light 已启动（pid $!）。菜单栏右上角应出现圆点。"
