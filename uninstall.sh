#!/usr/bin/env bash
# cc-light 一键卸载：停止并移除开机自启、清除 hooks 与状态文件。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.cc-light"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl unload "$PLIST" 2>/dev/null || true
pkill -f "cc_light.py" 2>/dev/null || true
rm -f "$PLIST"

# 只移除本工具的 hooks，保留用户其它配置。
python3 "$DIR/scripts/install_hooks.py" remove || true

rm -rf "$HOME/.claude/cc-light"

echo "已卸载：停止并移除自启、清除 cc-light 的 hooks 与状态/配置文件。"
echo "（保留了 ~/.claude/settings.json 的其它配置。）"
