#!/usr/bin/env bash
# cc-light 一键卸载：停止、移除开机自启、清 hooks / 状态文件 / profile 里的 alias。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.cc-light"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# 停止进程 + 移除开机自启。
launchctl unload "$PLIST" 2>/dev/null || true
pkill -f "cc_light.py" 2>/dev/null || true
rm -f "$PLIST"

# 只移除本工具的 hooks，保留用户其它配置。
python3 "$DIR/scripts/install_hooks.py" remove || true

# 移除 profile 里的 alias 行（zsh / bash 都扫一遍）。
for PROFILE in "$HOME/.zshrc" "$HOME/.bash_profile"; do
  [ -f "$PROFILE" ] || continue
  # 删掉 alias 行及其上一行的注释标题。
  sed -i '' '/# cc-light 菜单栏状态灯/d; /alias cc-light=/d' "$PROFILE" 2>/dev/null || true
done

rm -rf "$HOME/.claude/cc-light"

echo "已卸载：停止、移除开机自启、清除 cc-light 的 hooks / 状态 / alias（保留你其它配置）。"
