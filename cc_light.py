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

import fcntl
import functools
import glob
import json
import os
import subprocess
import sys
import time

import rumps

STATUS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "status")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", "config.json")
POLL_INTERVAL = 0.25     # 轮询间隔（秒）：比原版 500ms 更跟手，变色更及时
STALE_DROP = 30 * 60     # 无 claude_pid 的旧文件才用的兜底：超 30 分钟未更新视为僵死，从灯里移除
RUNNING_STALE = 120      # running 心跳过期：绿灯超 120s 没刷新视为卡态（ESC 中断等无收尾事件），降级为 idle

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


# 注意：不要叫 LABEL —— 那是上面的状态标签字典，别覆盖它。
AGENT_LABEL = "com.cc-light"
LAUNCH_AGENT_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_LABEL}.plist")
LOCK_PATH = os.path.join(os.path.expanduser("~"), ".claude", "cc-light", ".lock")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cc-light.log")

_lock_fh = None  # 单实例文件锁句柄，进程存活期间一直持有（关闭即释放锁）


def _acquire_single_instance() -> bool:
    """抢占单实例锁：已有实例在跑则返回 False（新进程应直接退出，避免两个菜单栏图标）。"""
    global _lock_fh
    try:
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        _lock_fh = open(LOCK_PATH, "w")
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _autostart_enabled() -> bool:
    return os.path.exists(LAUNCH_AGENT_PATH)


def _set_autostart(enable: bool) -> None:
    """开/关开机自启：只增删 LaunchAgent plist 文件，不 load/unload，故绝不影响当前正在跑的灯。
    下次登录由 launchd 按此文件决定是否自启（单实例锁保证不会和手动启动的重复）。
    """
    if enable:
        # sys.executable 即当前解释器（经 pythonw 启动时为 framework python，菜单栏可正常显示）。
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<plist version="1.0"><dict>\n'
            f"  <key>Label</key><string>{AGENT_LABEL}</string>\n"
            "  <key>ProgramArguments</key><array>\n"
            f"    <string>{sys.executable}</string><string>{os.path.abspath(__file__)}</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key><true/>\n"
            "  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n"
            f"  <key>StandardOutPath</key><string>{LOG_PATH}</string>\n"
            f"  <key>StandardErrorPath</key><string>{LOG_PATH}</string>\n"
            "</dict></plist>\n"
        )
        try:
            os.makedirs(os.path.dirname(LAUNCH_AGENT_PATH), exist_ok=True)
            with open(LAUNCH_AGENT_PATH, "w") as f:
                f.write(plist)
        except OSError:
            pass
    else:
        try:
            os.remove(LAUNCH_AGENT_PATH)
        except OSError:
            pass


def _format_title(counts: dict) -> str:
    """把各状态数量拼成菜单栏文字，如 "🔴2 🟡1 ⚪3"；数量为 0 的状态不显示。

    多会话时一眼看清「几个卡住、几个在跑、几个空闲」，无需点开下拉。
    """
    parts = [f"{GLYPH[st]}{counts[st]}" for st in TITLE_ORDER if counts.get(st)]
    return " ".join(parts) if parts else "⚪"


def _pid_alive(pid: int) -> bool:
    """探活：进程存在返回 True。跨用户等权限错误也按存活处理（进程确实在）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _effective_state(entry: dict, now: float) -> str:
    """返回会话状态；未知/历史状态（如旧的 done）一律按空闲处理。

    running 是有心跳的活动态：真在跑时 PreToolUse 会持续刷新 updated_at；
    ESC 中断等无收尾事件的情形会把绿灯冻结，故超 RUNNING_STALE 未刷新即降级为 idle。
    """
    st = entry.get("state", "idle")
    if st not in PRIORITY:
        return "idle"
    if st == "running" and now - entry.get("updated_at", 0) > RUNNING_STALE:
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
        pid = e.get("claude_pid", 0)
        if pid:
            # 有主进程 PID：以进程存活为准——只要开着就常驻，与多久没操作无关。
            # 进程已死（哪怕没正常收到 SessionEnd）则移除，并顺手清掉残留状态文件。
            if not _pid_alive(pid):
                try:
                    os.remove(p)
                except OSError:
                    pass
                continue
        elif now - e.get("updated_at", 0) > STALE_DROP:
            continue  # 无 PID 的旧文件：回退到 30 分钟超时兜底
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

    def _menubar_text(self, counts: dict) -> str:
        """按当前模式生成菜单栏文字。方法名避开 rumps 内部的 self._title 属性，勿改回 _title。

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
        self.title = self._menubar_text(counts)

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
        # 开机自启：勾选=写入 LaunchAgent（下次登录自动启动）；取消=删除。不影响当前正在跑的灯。
        auto = rumps.MenuItem("开机自启动（下次登录生效）", callback=self._toggle_autostart)
        auto.state = 1 if _autostart_enabled() else 0
        self.menu.add(auto)
        self.menu.add(None)  # 分隔线
        self.menu.add(rumps.MenuItem("历史会话管理…", callback=self._open_history))
        self.menu.add(rumps.MenuItem("用量统计…", callback=self._open_usage))
        self.menu.add(rumps.MenuItem("Claude Code 配置编辑…", callback=self._open_config))
        self.menu.add(None)  # 分隔线
        self.menu.add(rumps.MenuItem("退出 cc-light", callback=rumps.quit_application))

    def _toggle_expanded(self, sender) -> None:
        """切换菜单栏显示模式并持久化；标题下一轮 refresh(≤250ms) 自动跟上。"""
        self._expanded = not self._expanded
        sender.state = 1 if self._expanded else 0
        _save_expanded(self._expanded)

    def _open_history(self, _sender=None) -> None:
        """打开历史会话管理窗口（惰性导入，出错不影响状态灯）。控制器存在实例上防止被回收。

        异常打印到 stderr —— start.sh 把它重定向到 cc-light.log，便于排查问题。
        """
        try:
            if getattr(self, "_history", None) is None:
                import history_window
                self._history = history_window.HistoryController.alloc().init()
            self._history.show()
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)

    def _open_usage(self, _sender=None) -> None:
        """打开用量统计窗口（惰性导入，出错记入日志不影响状态灯）。"""
        try:
            if getattr(self, "_usage", None) is None:
                import usage_window
                self._usage = usage_window.UsageController.alloc().init()
            self._usage.show()
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)

    def _open_config(self, _sender=None) -> None:
        """打开 Claude Code 配置编辑窗口（惰性导入，出错记入日志不影响状态灯）。"""
        try:
            if getattr(self, "_config", None) is None:
                import config_window
                self._config = config_window.ConfigController.alloc().init()
            self._config.show()
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)

    def _toggle_autostart(self, sender) -> None:
        """开/关开机自启，勾选态按实际结果回读（写失败也不会显示成功）。"""
        _set_autostart(not _autostart_enabled())
        sender.state = 1 if _autostart_enabled() else 0

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
    # 单实例：已有一个灯在跑（手动或开机自启）就直接退出，避免菜单栏出现两个图标。
    if not _acquire_single_instance():
        sys.exit(0)
    CCLight().run()
