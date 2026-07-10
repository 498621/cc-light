#!/usr/bin/env python3
"""cc-light 菜单栏状态灯。

每 250ms 轮询状态目录。三态：工作中(绿)、等待中(红)、空闲(灰)。菜单栏有两种显示模式，
下拉里的开关可切换（默认「合并为一个灯」）：
- 合并为一个灯：按优先级取最高（红 > 绿 > 灰），只显示一个圆点。
- 分状态计数：如 "🔴2 🟢1 ⚪3"，一眼看清几个等待、几个在跑、几个空闲。
点开下拉还能看每个会话（按项目目录名区分）各自的状态，点击可跳回其 iTerm2 tab。

需要 rumps（pip install rumps）。Anaconda 等非 framework 版 Python 若菜单栏不显示图标，
用 pythonw 启动（见 start.sh 与 README）。
"""

import functools
import glob
import json
import os
import subprocess
import time

import rumps

STATUS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "status")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "config.json")
POLL_INTERVAL = 0.25     # 轮询间隔（秒）：比原版 500ms 更跟手，变色更及时
STALE_DROP = 30 * 60     # 超过 30 分钟未更新的会话视为僵死（未正常收到 SessionEnd），从灯里移除

# 三态：工作中(绿)、等待中(红)、空闲(灰)。数字为优先级（大=高），合并成单灯时取最高。
GLYPH = {"needs": "🔴", "running": "🟢", "idle": "⚪"}
LABEL = {"needs": "等待中", "running": "工作中", "idle": "空闲"}
PRIORITY = {"needs": 2, "running": 1, "idle": 0}

# 「分状态计数」模式下菜单栏的展示顺序（等待 > 工作 > 空闲）。
TITLE_ORDER = ("needs", "running", "idle")


def _load_expanded() -> bool:
    """读取「菜单栏分状态计数」开关；默认关闭（合并为单个灯）。"""
    try:
        with open(CONFIG_PATH) as f:
            return bool(json.load(f).get("expanded", False))
    except Exception:
        return False


def _save_expanded(expanded: bool) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({"expanded": expanded}, f)
    except Exception:
        pass


def _format_title(counts: dict) -> str:
    """把各状态数量拼成菜单栏文字，如 "🔴2 🟡1 ⚪3"；数量为 0 的状态不显示。

    多会话时一眼看清「几个卡住、几个在跑、几个空闲」，无需点开下拉。
    """
    parts = [f"{GLYPH[st]}{counts[st]}" for st in TITLE_ORDER if counts.get(st)]
    return " ".join(parts) if parts else "⚪"


def _effective_state(entry: dict, now: float) -> str:
    """返回会话状态；未知/历史状态（如旧的 done）一律按空闲处理。"""
    st = entry.get("state", "idle")
    return st if st in PRIORITY else "idle"


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
        # 设成 accessory：只在菜单栏显示圆点，不在 Dock 出现、也不进 Cmd+Tab 切换器。
        # rumps 自身不设激活策略，这里设的会生效；包裹 try 防止个别环境导入失败影响启动。
        try:
            import AppKit

            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory
            )
        except Exception:
            pass
        self._expanded = _load_expanded()  # 菜单栏是分状态计数(True)还是合并单灯(False)
        self._sig = None  # 上一次菜单内容签名，内容没变就不重建下拉（避免打开菜单时闪烁）
        self._render([], time.time())

    def _title(self, counts: dict) -> str:
        """按当前模式生成菜单栏文字。

        分状态计数：如 "🔴2 🟢1 ⚪3"；合并单灯：取最高优先级的一个灯（红 > 绿 > 灰）。
        """
        if self._expanded:
            return _format_title(counts)
        for st in TITLE_ORDER:
            if counts.get(st):
                return GLYPH[st]
        return "⚪"

    @rumps.timer(POLL_INTERVAL)
    def refresh(self, _sender) -> None:
        sessions, now = _read_sessions()

        counts = {"needs": 0, "running": 0, "idle": 0}
        for e in sessions:
            counts[_effective_state(e, now)] += 1
        self.title = self._title(counts)

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
                iterm_id = e.get("iterm_id", "") or ""
                # 末尾带 6 位会话 id：保证同名项目的多个会话在菜单里 key 唯一，不互相覆盖。
                item = rumps.MenuItem(f"{GLYPH[st]} {proj} — {LABEL[st]}  ·{sid}")
                # 有 iTerm 会话 id 的：点击跳回对应 tab；没有的（非 iTerm 启动）点击无动作。
                if iterm_id:
                    item.set_callback(functools.partial(self._jump, iterm_id))
                self.menu.add(item)
        self.menu.add(None)  # 分隔线
        # 显示模式开关：勾选=菜单栏分状态计数（🔴2 🟢1 ⚪3）；不勾选=合并成一个灯（红>绿>灰）。默认不勾选。
        toggle = rumps.MenuItem("菜单栏分状态计数（不勾选则合并为一个灯）", callback=self._toggle_expanded)
        toggle.state = 1 if self._expanded else 0
        self.menu.add(toggle)
        self.menu.add(None)  # 分隔线
        self.menu.add(rumps.MenuItem("退出 cc-light", callback=rumps.quit_application))

    def _toggle_expanded(self, sender) -> None:
        """切换菜单栏显示模式并持久化；标题下一轮 refresh(≤250ms) 自动跟上。"""
        self._expanded = not self._expanded
        sender.state = 1 if self._expanded else 0
        _save_expanded(self._expanded)

    def _jump(self, iterm_id: str, _sender=None) -> None:
        """点击会话菜单项：把 iTerm2 前置并选中该会话所在的 tab。"""
        script = (
            'tell application "iTerm2"\n'
            "  activate\n"
            "  repeat with w in windows\n"
            "    repeat with t in tabs of w\n"
            "      repeat with s in sessions of t\n"
            f'        if id of s is "{iterm_id}" then\n'
            "          select t\n"
            "          select s\n"
            "          return\n"
            "        end if\n"
            "      end repeat\n"
            "    end repeat\n"
            "  end repeat\n"
            "end tell\n"
        )
        try:
            subprocess.Popen(["osascript", "-e", script])
        except Exception:
            pass


if __name__ == "__main__":
    CCLight().run()
