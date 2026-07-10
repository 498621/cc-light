#!/usr/bin/env python3
"""cc-light 的 Claude Code 钩子处理器。

由 ~/.claude/settings.json 里的各 hook 事件调用，argv[1] 传入目标状态
（running / needs / done / idle / end）。从 stdin 读事件 JSON 拿到 session_id 与 cwd，
把该会话的状态原子写入状态目录，供菜单栏 widget 轮询显示。

铁律：无论发生什么都以 exit 0 结束、且绝不向 stdout 打印内容——
      PreToolUse 等钩子的非零退出或 stdout 输出会干扰甚至阻断 Claude Code 的正常执行。
"""

import json
import os
import sys
import time

# 状态文件目录：hook（可能在任意 cwd 下被调用）与 widget 都从 $HOME 推出同一路径。
STATUS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "status")


def main() -> None:
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"

    raw = sys.stdin.read() or "{}"
    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    session_id = str(data.get("session_id") or "unknown")
    cwd = data.get("cwd") or ""
    message = data.get("message") or ""

    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, session_id + ".json")

    # 会话结束：删掉状态文件，从灯里移除该会话。
    if state == "end":
        try:
            os.remove(path)
        except OSError:
            pass
        return

    payload = {
        "session_id": session_id,
        "state": state,
        "cwd": cwd,
        "message": message,
        "updated_at": time.time(),
    }
    # 先写临时文件再原子 rename，避免 widget 读到写了一半的 JSON。
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 灯出任何问题都不能影响 Claude Code，静默吞掉。
        pass
    sys.exit(0)
