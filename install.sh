#!/usr/bin/env bash
# cc-light 一键安装：装依赖 + 配置 Claude Code hooks + 安装 LaunchAgent
#（开机自启、崩溃自愈、装完立即启动）。反复运行即「重装/重启」——改了代码后跑一次就用上新代码。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.cc-light"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# 1) 选解释器：优先 pythonw（Anaconda 等非 framework Python 下菜单栏图标更稳），否则 python3。
if command -v pythonw >/dev/null 2>&1; then
  PY="$(command -v pythonw)"
else
  PY="$(command -v python3)"
fi
echo "使用 Python: $PY"

# 2) 确保 rumps 可被该解释器导入。
if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "安装 rumps ..."
  python3 -m pip install rumps 2>/dev/null || pip3 install rumps 2>/dev/null || true
fi
if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "rumps 仍不可用，请手动运行：pip3 install rumps 后重试。" >&2
  exit 1
fi

# 3) 幂等配置 Claude Code hooks（合并进 ~/.claude/settings.json，不动其它配置）。
python3 "$DIR/scripts/install_hooks.py" install

# 4) 生成 LaunchAgent（路径按本机解析，故可分享给他人）。
mkdir -p "$HOME/Library/LaunchAgents"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DIR/cc_light.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <!-- 只在异常退出时自愈；从菜单「退出」是干净退出(exit 0)，不会被拉起 -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$DIR/cc-light.log</string>
  <key>StandardErrorPath</key><string>$DIR/cc-light.log</string>
</dict></plist>
EOF

# 5) 重新加载：先清掉任何旧实例（手动跑的或已加载的），再由 launchd 启动，避免出现两个图标。
launchctl unload "$PLIST" 2>/dev/null || true
pkill -f "cc_light.py" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "安装完成。菜单栏右上角应出现圆点；已设为开机自启、崩溃自愈。"
echo "注意：新增/改动 hooks 需重启 Claude Code 会话才生效。"
echo "卸载：./uninstall.sh"
