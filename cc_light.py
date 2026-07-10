#!/usr/bin/env python3
"""cc-light 菜单栏状态灯。

每 500ms 轮询状态目录：菜单栏图标显示所有活跃 Claude Code 会话的「聚合」状态
（有会话等待确认就红、有在跑就黄、有刚完成就绿、全空闲则灰）；点开下拉可看每个会话
（按其项目目录名区分）各自的状态。

需要 rumps（pip install rumps）。Anaconda 等非 framework 版 Python 若菜单栏不显示图标，
用 pythonw 启动（见 start.sh 与 README）。
"""

import glob
import json
import os
import time

import rumps

STATUS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "status")
POLL_INTERVAL = 0.5      # 轮询间隔（秒），与原版一致
DONE_TTL = 5             # done(绿) 显示满 5 秒后自动转为空闲，与原版一致
STALE_DROP = 30 * 60     # 超过 30 分钟未更新的会话视为僵死（未正常收到 SessionEnd），从灯里移除

# 状态 → 菜单栏图标、中文标签、优先级（数字大=优先级高，聚合取最高）。
GLYPH = {"needs": "🔴", "running": "🟡", "done": "🟢", "idle": "⚪"}
LABEL = {"needs": "等待确认", "running": "运行中", "done": "已完成", "idle": "空闲"}
PRIORITY = {"needs": 3, "running": 2, "done": 1, "idle": 0}


def _effective_state(entry: dict, now: float) -> str:
    """done 满 DONE_TTL 秒后视为 idle（绿灯自动熄），其余按记录的状态。"""
    st = entry.get("state", "idle")
    if st == "done" and now - entry.get("updated_at", 0) > DONE_TTL:
        return "idle"
    return st


def _read_sessions():
    now = time.time()
    out = []
    for p in glob.glob(os.path.join(STATUS_DIR, "*.json")):
        try:
            with open(p) as f:
                e = json.load(f)
        except Exception:
            continue  # 半截/损坏文件下一轮再读
        if now - e.get("updated_at", 0) > STALE_DROP:
            continue  # 僵死会话不显示
        out.append(e)
    return out, now


class CCLight(rumps.App):
    def __init__(self) -> None:
        # quit_button=None：自己在下拉里加「退出」，以便把它排在会话列表下方。
        super().__init__("⚪", quit_button=None)
        self._sig = None  # 上一次菜单内容签名，内容没变就不重建下拉（避免打开菜单时闪烁）
        self._render([], time.time())

    @rumps.timer(POLL_INTERVAL)
    def refresh(self, _sender) -> None:
        sessions, now = _read_sessions()

        # 聚合图标：取所有会话里优先级最高的状态。菜单栏文字每轮都更新（廉价、不闪烁）。
        agg = "idle"
        for e in sessions:
            st = _effective_state(e, now)
            if PRIORITY[st] > PRIORITY[agg]:
                agg = st
        self.title = GLYPH[agg]

        # 下拉内容仅在「有会话状态变化」时才重建，避免菜单打开时被每 500ms 的重建打断。
        sig = tuple(
            sorted(
                (e.get("session_id", ""), _effective_state(e, now), os.path.basename(e.get("cwd", "") or ""))
                for e in sessions
            )
        )
        if sig != self._sig:
            self._sig = sig
            self._render(sessions, now)

    def _render(self, sessions: list, now: float) -> None:
        self.menu.clear()
        if not sessions:
            self.menu.add(rumps.MenuItem("（暂无活跃会话）"))
        else:
            # 按优先级排序：等待确认 > 运行中 > 已完成 > 空闲。
            ordered = sorted(sessions, key=lambda e: -PRIORITY[_effective_state(e, now)])
            for e in ordered:
                st = _effective_state(e, now)
                proj = os.path.basename(e.get("cwd", "") or "") or "（未知目录）"
                sid = str(e.get("session_id", ""))[:6]
                # 末尾带 6 位会话 id：保证同名项目的多个会话在菜单里 key 唯一，不互相覆盖。
                self.menu.add(rumps.MenuItem(f"{GLYPH[st]} {proj} — {LABEL[st]}  ·{sid}"))
        self.menu.add(None)  # 分隔线
        self.menu.add(rumps.MenuItem("退出 cc-light", callback=rumps.quit_application))


if __name__ == "__main__":
    CCLight().run()
