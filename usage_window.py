#!/usr/bin/env python3
"""cc-light 用量统计窗口：跨会话聚合 token / 成本 / 任务，按项目、模型、日期汇总。

数据来自 stats.aggregate（解析全部 session jsonl）。用 WKWebView 渲染 HTML dashboard，
布局整齐、深浅色自适应，成本占比用中性条形表示（无花哨配色）。
激活策略与 history_window 一致（accessory 应用需提升为 Regular 才能交互）。
"""

from datetime import datetime, timedelta

import objc

from AppKit import (
    NSApplication, NSApplicationActivateIgnoringOtherApps,
    NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered, NSClosableWindowMask, NSMakeRect,
    NSMiniaturizableWindowMask, NSResizableWindowMask, NSRunningApplication,
    NSTitledWindowMask, NSWindow,
)
from Foundation import NSObject
from WebKit import WKWebView

import stats
from history_window import _build_main_menu, _external_activate

W, H = 920, 700


def _tok(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


_CSS = """
:root { color-scheme: light dark; }
body { font: 13px -apple-system, system-ui, sans-serif; margin: 20px; line-height: 1.5;
       color: #1d1d1f; }
@media (prefers-color-scheme: dark) { body { color: #e6e6ea; } }
h1 { font-size: 1.5em; margin: 0 0 .6em; }
h2 { font-size: 1.15em; margin: 1.4em 0 .5em; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; }
.card { flex: 1 1 120px; border: 1px solid rgba(127,127,127,.28); border-radius: 10px;
        padding: 12px 16px; }
.card .v { font-size: 1.7em; font-weight: 600; }
.card .l { font-size: .82em; opacity: .6; margin-top: 2px; }
table { border-collapse: collapse; width: 100%; margin: .3em 0; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid rgba(127,127,127,.18); }
th { font-weight: 600; opacity: .7; border-bottom: 1px solid rgba(127,127,127,.35); }
th:first-child, td.k { text-align: left; }
td.k { max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
td.c { font-weight: 600; }
td.bar { width: 130px; }
td.bar span { display: block; height: 8px; border-radius: 4px; background: rgba(127,127,127,.55); }
.note { margin-top: 1.6em; font-size: .8em; opacity: .55; }
.chart { display: flex; align-items: flex-end; gap: 3px; height: 150px; margin: .4em 0 2em;
         border-bottom: 1px solid rgba(127,127,127,.3); padding-bottom: 20px; }
.col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
       align-items: center; height: 100%; position: relative; }
.col .bar { width: 68%; min-height: 1px; border-radius: 3px 3px 0 0; background: rgba(127,127,127,.55); }
.col:hover .bar { background: rgba(10,132,255,.75); }
.col .xl { position: absolute; bottom: -17px; font-size: 9px; opacity: .55; white-space: nowrap; }
.col .tip { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
            margin-bottom: 6px; padding: 4px 9px; border-radius: 5px; font-size: 11px; white-space: nowrap;
            background: rgba(30,30,30,.92); color: #fff; box-shadow: 0 2px 8px rgba(0,0,0,.28);
            pointer-events: none; z-index: 5; }
.col:hover .tip { display: block; }
"""


def _daily_chart(by_day):
    """近 30 天每日用量柱状时间轴（柱高=当天成本）。"""
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    rows = [(d.strftime("%Y-%m-%d"), by_day.get(d.strftime("%Y-%m-%d"),
             {"cost": 0.0, "tasks": 0, "sessions": 0})) for d in days]
    maxc = max((v["cost"] for _, v in rows), default=0) or 1
    cols = ""
    for i, (ds, v) in enumerate(rows):
        pct = v["cost"] / maxc * 100
        label = ds[5:] if i % 5 == 0 else ""
        tip = f"{ds}｜${v['cost']:.2f}｜{v['tasks']} 任务｜{v['sessions']} 会话"
        cols += (f'<div class="col"><div class="tip">{tip}</div>'
                 f'<div class="bar" style="height:{pct:.0f}%"></div>'
                 f'<div class="xl">{label}</div></div>')
    return f'<h2>每日用量（近 30 天 · 柱高=成本 · 悬停看明细）</h2><div class="chart">{cols}</div>'


def _section(title, bucket, date_sorted=False, limit=None):
    if date_sorted:
        items = sorted(bucket.items(), reverse=True)
    else:
        items = sorted(bucket.items(), key=lambda kv: -kv[1]["cost"])
    if limit:
        items = items[:limit]
    maxc = max((v["cost"] for _, v in items), default=0) or 1
    rows = ""
    for k, v in items:
        pct = v["cost"] / maxc * 100
        rows += (f'<tr><td class="k">{k}</td><td>{v["sessions"]}</td><td>{v["tasks"]}</td>'
                 f'<td>{_tok(v["out"])}</td><td class="c">${v["cost"]:.2f}</td>'
                 f'<td class="bar"><span style="width:{pct:.0f}%"></span></td></tr>')
    head = title.replace("按", "")
    return (f'<h2>{title}</h2><table><thead><tr><th>{head}</th><th>会话</th><th>任务</th>'
            f'<th>输出</th><th>成本</th><th>成本占比</th></tr></thead><tbody>{rows}</tbody></table>')


def _build_html(agg):
    t = agg["total"]
    cards = (
        '<div class="cards">'
        f'<div class="card"><div class="v">{t["sessions"]}</div><div class="l">会话</div></div>'
        f'<div class="card"><div class="v">{t["tasks"]}</div><div class="l">任务</div></div>'
        f'<div class="card"><div class="v">{_tok(t["out"])}</div><div class="l">输出 Token</div></div>'
        f'<div class="card"><div class="v">${t["cost"]:.2f}</div><div class="l">总成本(估)</div></div>'
        '</div>'
    )
    body = (cards
            + _daily_chart(agg["by_day"])
            + _section("按模型", agg["by_model"])
            + _section("按项目", agg["by_project"])
            + _section("按日期", agg["by_day"], date_sorted=True, limit=30))
    note = '<p class="note">成本按 stats.MODEL_PRICING 估算，仅供参考；按日期仅显示最近 30 天。</p>'
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head>"
            f"<body><h1>用量统计</h1>{body}{note}</body></html>")


class UsageController(NSObject):
    def init(self):
        self = objc.super(UsageController, self).init()
        if self is None:
            return None
        self.win = None
        self.web = None
        self.menu = None
        self._pending_win = None
        self._activate_tries = 0
        return self

    @objc.python_method
    def show(self):
        if self.win is not None:
            self._reload()
            self._front(self.win)
            return
        style = (NSTitledWindowMask | NSClosableWindowMask
                 | NSResizableWindowMask | NSMiniaturizableWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        win.setTitle_("cc-light 用量统计")
        win.setReleasedWhenClosed_(False)
        win.center()
        self.web = WKWebView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        win.setContentView_(self.web)
        win.setDelegate_(self)
        self.win = win
        self._reload()
        self._front(win)

    @objc.python_method
    def _reload(self):
        # 先显示占位，再下一 runloop 解析聚合（全量解析较慢，避免开窗瞬间卡死）。
        self.web.loadHTMLString_baseURL_(
            "<body style='font:15px -apple-system;color:gray;padding:48px'>正在统计所有会话…</body>", None)
        self.performSelector_withObject_afterDelay_("_compute:", None, 0.1)

    def _compute_(self, _arg):
        agg = stats.aggregate()
        self.web.loadHTMLString_baseURL_(_build_html(agg), None)

    @objc.python_method
    def _front(self, win):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if self.menu is None:
            self.menu = _build_main_menu()
        app.setMainMenu_(self.menu)
        win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()
        app.activateIgnoringOtherApps_(True)
        _external_activate()          # 绕过新版 macOS 编程激活限制
        self._pending_win = win
        self._activate_tries = 0
        self.performSelector_withObject_afterDelay_("_activate:", None, 0.12)

    def _activate_(self, _arg):
        win = self._pending_win
        if win is None or win.isKeyWindow():
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()
        self._activate_tries += 1
        if self._activate_tries < 8:
            _external_activate()
            self.performSelector_withObject_afterDelay_("_activate:", None, 0.15)

    def windowWillClose_(self, note):
        if note.object() is self.win:
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)
