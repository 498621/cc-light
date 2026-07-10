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
import subprocess
import sys
import time

# 状态文件目录：hook（可能在任意 cwd 下被调用）与 widget 都从 $HOME 推出同一路径。
STATUS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "status")
# 状态变更事件目录：记录每个会话的状态转换时间线（红灯次数等 jsonl 读不到的 cc-light 独有数据）。
# 放项目自身的 data/ 下（hook.py 所在目录），历史统计界面据此按任务区间归并红灯次数。
EVENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "events")


def _record_event(session_id: str, state: str, ts: float) -> None:
    """把一次状态变更追加到 data/events/<sid>.jsonl（失败静默，绝不影响 Claude Code）。"""
    try:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        with open(os.path.join(EVENTS_DIR, session_id + ".jsonl"), "a") as f:
            f.write(json.dumps({"ts": ts, "state": state}) + "\n")
    except Exception:
        pass


def _find_claude_pid() -> int:
    """从当前 hook 进程沿父进程链往上找到 comm=claude 的进程 PID。

    钩子是 Claude Code 的（子）子进程，往上第一个名为 claude 的进程即该会话的主进程。
    widget 靠这个 PID 探活：只要 claude 还活着，会话就常驻菜单，与是否操作无关。
    一次 ps 建表再走链，避免逐级 fork；找不到返回 0（widget 侧回退到旧的超时规则）。
    """
    try:
        out = subprocess.check_output(["ps", "-eo", "pid=,ppid=,comm="], text=True)
    except Exception:
        return 0
    parent, comm = {}, {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        parent[pid] = ppid
        comm[pid] = parts[2]
    pid = os.getpid()
    for _ in range(30):  # 上限 30 层，纯防环
        if os.path.basename(comm.get(pid, "")) == "claude":
            return pid
        pid = parent.get(pid, 0)
        if pid <= 1:
            break
    return 0


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

    # Notification 既用于「需要权限/被提问」（红），也用于「空闲 60s 提醒」。后者按空闲(灰)处理，
    # 以贴合「空闲=灰、红=有问题等待」。靠 message 文案区分（启发式：文案改了会退回红，不影响功能安全）。
    if state == "needs":
        low = message.lower()
        if "waiting" in low and "input" in low:
            state = "idle"
    # iTerm2 会话 id：环境变量形如 "w0t1p3:GUID"，取冒号后的 GUID，供菜单点击时跳回对应 tab。
    # 钩子作为 Claude Code 子进程运行，继承了它所在 iTerm pane 的这个变量；非 iTerm 环境则为空。
    iterm_full = os.environ.get("ITERM_SESSION_ID", "")
    iterm_id = iterm_full.split(":")[-1] if iterm_full else ""

    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, session_id + ".json")

    # 读旧状态：仅在状态真正变更时记录事件，避免每次 PreToolUse/PostToolUse 重复写 running。
    old_state = None
    try:
        with open(path) as f:
            old_state = json.load(f).get("state")
    except Exception:
        pass

    # 会话结束：记一条 end 事件标记时间线终点，再删状态文件从灯里移除该会话。
    if state == "end":
        if old_state != "end":
            _record_event(session_id, "end", time.time())
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
        "iterm_id": iterm_id,
        "claude_pid": _find_claude_pid(),  # 主进程 PID：widget 靠它探活，开着不操作也不会被移除
        "updated_at": time.time(),
    }
    # 先写临时文件再原子 rename，避免 widget 读到写了一半的 JSON。
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)

    # 状态变更时记录事件（供历史统计算红灯次数/停留时长）。
    if state != old_state:
        _record_event(session_id, state, payload["updated_at"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 灯出任何问题都不能影响 Claude Code，静默吞掉。
        pass
    sys.exit(0)
