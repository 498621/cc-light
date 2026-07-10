#!/usr/bin/env python3
"""cc-light 历史会话管理窗口（原生 AppKit）。

主窗口：左侧会话列表（三行：路径/session_id/创建时间），右上会话概览（dashboard 式），
右下任务记录表。双击任务弹出二级详情窗（任务信息 / 提交内容 / 返回内容三分区，返回内容按
markdown 渲染）。

数据全部来自 stats.py（离线解析 Claude Code 的 session jsonl + cc-light 红灯事件）。
GUI 无法在无图形会话的环境运行，此模块仅在用户点击菜单时由 cc_light.py 惰性导入。
"""

import re
from datetime import datetime

import markdown
import objc

from AppKit import (
    NSApplication, NSApplicationActivateIgnoringOtherApps,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSBezelBorder,
    NSBezelStyleRounded, NSButton, NSClosableWindowMask, NSColor, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSLinkAttributeName,
    NSLineBreakByTruncatingTail, NSMakeRect, NSMakeSize, NSMenu, NSMenuItem,
    NSMiniaturizableWindowMask, NSPasteboard, NSPasteboardTypeString,
    NSResizableWindowMask, NSRunningApplication, NSScrollView,
    NSScrollerStyleLegacy, NSSearchField, NSTableColumn, NSTableView,
    NSTableViewUniformColumnAutoresizingStyle, NSTextAlignmentLeft,
    NSTextAlignmentRight, NSTextField, NSTextView, NSTitledWindowMask,
    NSUnderlineStyleSingle, NSUnderlineStyleAttributeName, NSView,
    NSViewHeightSizable, NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable,
    NSWindow,
)
from Foundation import NSIndexSet, NSMutableAttributedString, NSObject
from WebKit import WKWebView

import stats

MAIN_W, MAIN_H = 1040, 680
DETAIL_W, DETAIL_H = 900, 760
ROW_SESSION = 60          # 会话行高（容纳路径/session_id/时间三行）
OVER_H = 150              # 右上概览区高度
INFO_H = 84               # 详情窗「任务信息」内容固定高度（三行，不随窗口变化）
MID_RATIO = 0.22          # 「改动文件」「提交内容」各占中部可用高度的比例，其余归返回内容

# 任务记录表列：(标识, 表头, 宽度)
TASK_COLS = [
    ("idx", "#", 34), ("prompt", "提交摘要", 300), ("dur", "耗时", 72),
    ("out", "输出tok", 78), ("tools", "工具", 60), ("reds", "红灯", 46),
    ("time", "时间", 120),
]


def _dur(s):
    s = int(s or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h{m}m" if h else (f"{m}m{sec}s" if m else f"{sec}s")


def _tok(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _time(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"


def _choice(ask):
    """从 AskUserQuestion 的 result 文本里提取用户最终选择（取末个 ="..." 值）。"""
    vals = re.findall(r'="([^"]+)"', ask.get("result", "") or "")
    return vals[-1] if vals else "（未记录）"


def _scroll(doc, horiz=False, border=False, legacy=False):
    sv = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
    sv.setHasVerticalScroller_(True)
    sv.setHasHorizontalScroller_(horiz)
    sv.setAutohidesScrollers_(True)
    if legacy:  # 传统滚动条占位（不覆盖内容），避免遮住右对齐文字
        sv.setScrollerStyle_(NSScrollerStyleLegacy)
    if border:
        sv.setBorderType_(NSBezelBorder)  # 凹陷边框做视觉分区，替代文字横线
    sv.setDocumentView_(doc)
    return sv


def _make_table(cols, header=True):
    table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
    for ident, title, width in cols:
        col = NSTableColumn.alloc().initWithIdentifier_(ident)
        col.headerCell().setStringValue_(title)
        col.setWidth_(width)
        table.addTableColumn_(col)
    table.setRowHeight_(22)
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setColumnAutoresizingStyle_(NSTableViewUniformColumnAutoresizingStyle)
    if not header:
        table.setHeaderView_(None)
    return table


def _textview(mono=True):
    """只读、可选中（配合主菜单即可 cmd+C 复制）、按容器宽度换行的文本视图。"""
    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 100))
    tv.setEditable_(False)
    tv.setSelectable_(True)
    tv.setRichText_(False)
    tv.setFont_(NSFont.userFixedPitchFontOfSize_(12) if mono
                else NSFont.systemFontOfSize_(13))
    tv.setTextContainerInset_(NSMakeSize(10, 8))
    tv.setHorizontallyResizable_(False)
    tv.setVerticallyResizable_(True)
    tv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    tv.textContainer().setWidthTracksTextView_(True)
    return tv


def _label(text, size, color, align, bold=False):
    tf = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 18))
    tf.setStringValue_(text or "")
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(True)  # 可选中复制
    tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
    tf.setTextColor_(color)
    tf.setAlignment_(align)
    tf.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return tf


def _build_main_menu():
    """最小主菜单：让 cmd+C/cmd+V/cmd+A、cmd+W 在窗口里生效（accessory 应用默认没有）。"""
    main = NSMenu.alloc().init()

    app_item = NSMenuItem.alloc().init()
    main.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_item.setSubmenu_(app_menu)
    app_menu.addItemWithTitle_action_keyEquivalent_("Hide", "hide:", "h")

    edit_item = NSMenuItem.alloc().init()
    main.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    edit_item.setSubmenu_(edit)
    for title, sel, key in [("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                            ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")]:
        edit.addItemWithTitle_action_keyEquivalent_(title, sel, key)

    win_item = NSMenuItem.alloc().init()
    main.addItem_(win_item)
    win = NSMenu.alloc().initWithTitle_("Window")
    win_item.setSubmenu_(win)
    win.addItemWithTitle_action_keyEquivalent_("Close", "performClose:", "w")
    return main


# WKWebView 渲染 markdown 用的样式：跟随系统深浅色，表格/代码块/引用清晰，无多余装饰。
_CSS = """
:root { color-scheme: light dark; }
body { font: 13px -apple-system, system-ui, sans-serif; margin: 12px; line-height: 1.55;
       color: #1d1d1f; }
@media (prefers-color-scheme: dark) { body { color: #e6e6ea; } }
h1,h2,h3,h4 { line-height: 1.3; margin: .7em 0 .35em; }
h1 { font-size: 1.5em; } h2 { font-size: 1.28em; } h3 { font-size: 1.12em; }
p { margin: .4em 0; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .9em;
       background: rgba(127,127,127,.18); padding: .1em .35em; border-radius: 4px; }
pre { background: rgba(127,127,127,.14); padding: 10px 12px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: .6em 0; font-size: .95em; }
th, td { border: 1px solid rgba(127,127,127,.4); padding: 5px 10px; text-align: left; }
th { background: rgba(127,127,127,.16); }
blockquote { margin: .5em 0; padding-left: 12px; border-left: 3px solid rgba(127,127,127,.4);
             opacity: .85; }
a { color: #0a84ff; } img { max-width: 100%; }
"""


def _render_html(md_text):
    """markdown → 完整 HTML（含表格/代码扩展与深浅色 CSS），供 WKWebView 渲染。"""
    body = markdown.markdown(md_text or "", extensions=["tables", "fenced_code", "sane_lists"])
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}</body></html>"


def _button(title, sel, target):
    """系统普通风格按钮（灰色 rounded，非蓝色 primary——不设 keyEquivalent 即非默认按钮）。"""
    b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 96, 30))
    b.setTitle_(title)
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setTarget_(target)
    b.setAction_(sel)
    return b


def _attr(text, attrs):
    return NSMutableAttributedString.alloc().initWithString_attributes_(text, attrs)


def _files_attr(files):
    """改动文件列表：每个文件带下划线 + link（hover 小手、可点击复制）。"""
    if not files:
        return _attr("（无）", {NSFontAttributeName: NSFont.systemFontOfSize_(12),
                               NSForegroundColorAttributeName: NSColor.tertiaryLabelColor()})
    ms = NSMutableAttributedString.alloc().init()
    font, color = NSFont.systemFontOfSize_(12), NSColor.linkColor()
    for i, f in enumerate(files):
        ms.appendAttributedString_(_attr(f, {
            NSLinkAttributeName: f, NSUnderlineStyleAttributeName: NSUnderlineStyleSingle,
            NSFontAttributeName: font, NSForegroundColorAttributeName: color}))
        if i < len(files) - 1:
            ms.appendAttributedString_(_attr("\n", {}))
    return ms


def _files_title_attr(hint="   点击文件名可复制"):
    """「改动文件」标题 + 浅灰小字提示。"""
    ms = NSMutableAttributedString.alloc().init()
    ms.appendAttributedString_(_attr("改动文件", {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(12),
        NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}))
    ms.appendAttributedString_(_attr(hint, {
        NSFontAttributeName: NSFont.systemFontOfSize_(10),
        NSForegroundColorAttributeName: NSColor.tertiaryLabelColor()}))
    return ms


class HistoryController(NSObject):
    """主窗口控制器，兼两个表的数据源/委托、两个窗口的 window delegate。"""

    def init(self):
        self = objc.super(HistoryController, self).init()
        if self is None:
            return None
        self.paths = []
        self.metas = []
        self.all_paths = []      # 全部会话（搜索前）
        self.all_metas = []
        self.search_field = None
        self.cache = {}
        self.summary = None
        self.tasks = []
        self.win = None
        self.session_table = None
        self.task_table = None
        self.ov = {}             # 概览各 label 引用
        self.menu = None
        self._pending_win = None  # 待激活的窗口
        self._activate_tries = 0  # 激活重试计数
        # 详情窗相关引用
        self.detail_win = None
        self.detail_box = None
        self.detail_row = 0
        self.di_view = None      # 任务信息 dashboard
        self.df_tv = self.ds_tv = None
        self.df_scroll = self.ds_scroll = None
        self.dr_web = None       # 返回内容用 WKWebView 渲染完整 markdown
        self.di_title = self.df_title = self.ds_title = self.dr_title = None
        self.prev_btn = self.next_btn = None
        return self

    # ---- 窗口置前 / 激活策略 ----
    @objc.python_method
    def _front(self, win):
        """提升为 Regular 策略并装主菜单，使 accessory 应用的窗口可交互、快捷键可用。

        accessory→Regular 的策略切换是异步的：切换后立刻 activate 有几率在策略生效前执行，
        窗口拿不到 key（表现为“刚打开点不动，切走再切回才行”）。故把激活延到下一个
        runloop tick，等策略切换落地后再激活，稳定消除间歇性无响应。
        """
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if self.menu is None:
            self.menu = _build_main_menu()
        app.setMainMenu_(self.menu)
        win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()
        # 策略切换是异步的、生效时机不定。用重试轮询兜底：反复激活直到窗口真正成为
        # key window（或超时），无论系统时序如何都能激活到位，消除“刚打开点不动”。
        self._pending_win = win
        self._activate_tries = 0
        self.performSelector_withObject_afterDelay_("_activate:", None, 0.05)

    def _activate_(self, _arg):
        win = self._pending_win
        if win is None:
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        NSRunningApplication.currentApplication().activateWithOptions_(
            NSApplicationActivateIgnoringOtherApps)
        win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()
        self._activate_tries += 1
        if not win.isKeyWindow() and self._activate_tries < 10:
            self.performSelector_withObject_afterDelay_("_activate:", None, 0.1)

    def windowWillClose_(self, note):
        if note.object() is self.win:  # 主窗口关闭 → 切回 accessory，隐藏 Dock 图标
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)

    def windowDidResize_(self, note):
        if note.object() is self.detail_win and self.detail_box is not None:
            self._layout_detail(self.detail_box.bounds().size)

    # ---- 主窗口 ----
    @objc.python_method
    def show(self):
        if self.win is not None:
            self._reload_sessions()
            self._front(self.win)
            return

        style = (NSTitledWindowMask | NSClosableWindowMask
                 | NSResizableWindowMask | NSMiniaturizableWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, MAIN_W, MAIN_H), style, NSBackingStoreBuffered, False)
        win.setTitle_("cc-light 历史会话")
        win.setReleasedWhenClosed_(False)
        win.center()

        side_w = 300
        right_w = MAIN_W - side_w

        # 左：搜索框 + 会话列表（无表头、legacy 滚动条不遮右对齐时间）。
        sf_h = 30
        self.search_field = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(6, MAIN_H - sf_h + 2, side_w - 12, sf_h - 8))
        self.search_field.setDelegate_(self)
        self.search_field.setPlaceholderString_("搜索项目 / 路径 / session")
        self.search_field.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        self.session_table = _make_table([("s", "会话", side_w)], header=False)
        self.session_table.setRowHeight_(ROW_SESSION)
        self.session_table.setDataSource_(self)
        self.session_table.setDelegate_(self)
        self.session_table.setTarget_(self)
        self.session_table.setAction_("onSessionClick:")
        sess_scroll = _scroll(self.session_table, legacy=True)
        sess_scroll.setFrame_(NSMakeRect(0, 0, side_w, MAIN_H - sf_h))
        sess_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        left = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, side_w, MAIN_H))
        left.setAutoresizingMask_(NSViewHeightSizable)
        left.addSubview_(self.search_field)
        left.addSubview_(sess_scroll)

        container = NSView.alloc().initWithFrame_(NSMakeRect(side_w, 0, right_w, MAIN_H))
        container.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        # 右上：概览（dashboard 布局）。
        overview = self._build_overview(right_w)
        overview.setFrame_(NSMakeRect(0, MAIN_H - OVER_H, right_w, OVER_H))
        overview.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)

        # 右下：任务记录表（双击弹详情）。
        self.task_table = _make_table(TASK_COLS)
        self.task_table.setDataSource_(self)
        self.task_table.setDelegate_(self)
        self.task_table.setTarget_(self)
        self.task_table.setDoubleAction_("onTaskDouble:")
        task_scroll = _scroll(self.task_table, horiz=True)
        task_scroll.setFrame_(NSMakeRect(0, 0, right_w, MAIN_H - OVER_H))
        task_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        container.addSubview_(overview)
        container.addSubview_(task_scroll)

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, MAIN_W, MAIN_H))
        content.addSubview_(left)
        content.addSubview_(container)
        win.setContentView_(content)
        win.setDelegate_(self)
        self.win = win
        self._reload_sessions()
        self._front(win)

    @objc.python_method
    def _build_overview(self, w):
        """构建 dashboard 式概览：顶部 4 个 KPI（大数值+小标签），下方三行信息。"""
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, OVER_H))
        v.setAutoresizesSubviews_(True)
        gray = NSColor.secondaryLabelColor()
        # 右上角：复制 `claude --resume <id>` 到剪贴板，方便到终端继续该会话。
        copy_btn = _button("复制会话命令", "onCopySession:", self)
        copy_btn.setFrame_(NSMakeRect(w - 140, OVER_H - 30, 128, 26))
        copy_btn.setAutoresizingMask_(NSViewMinXMargin)  # 贴右
        v.addSubview_(copy_btn)
        self.ov["copy_btn"] = copy_btn
        kpis = [("tasks", "任务数"), ("dur", "总耗时"), ("out", "输出 Token"), ("reds", "红灯合计")]
        for i, (key, name) in enumerate(kpis):
            x = 24 + i * 168
            val = _label("—", 20, NSColor.labelColor(), NSTextAlignmentLeft, bold=True)
            val.setFrame_(NSMakeRect(x, OVER_H - 56, 160, 26))
            name_lbl = _label(name, 10, gray, NSTextAlignmentLeft)
            name_lbl.setFrame_(NSMakeRect(x, OVER_H - 72, 160, 14))
            v.addSubview_(val)
            v.addSubview_(name_lbl)
            self.ov[key] = val
        # 下方信息三行
        for key, y, size, color in [("info1", 52, 12.5, NSColor.labelColor()),
                                     ("info2", 30, 11, gray),
                                     ("info3", 10, 11, gray)]:
            lbl = _label("", size, color, NSTextAlignmentLeft)
            lbl.setFrame_(NSMakeRect(24, y, w - 48, 18))
            lbl.setAutoresizingMask_(NSViewWidthSizable)
            v.addSubview_(lbl)
            self.ov[key] = lbl
        return v

    @objc.python_method
    def _update_overview(self):
        s = self.summary
        u = s["total_usage"]
        self.ov["tasks"].setStringValue_(str(s["task_count"]))
        self.ov["dur"].setStringValue_(_dur(sum(t["duration"] for t in s["tasks"])))
        self.ov["out"].setStringValue_(_tok(u.get("output_tokens", 0)))
        self.ov["reds"].setStringValue_(str(sum(t["reds"] for t in s["tasks"])))
        self.ov["info1"].setStringValue_(
            f"项目 {s['project']}      分支 {s['gitBranch'] or '—'}      模型 {s['model'] or '—'}")
        self.ov["info2"].setStringValue_(
            f"会话 {s['session_id']}    ·  输入 {_tok(u.get('input_tokens', 0))}  "
            f"缓存读 {_tok(u.get('cache_read_input_tokens', 0))}  "
            f"缓存写 {_tok(u.get('cache_creation_input_tokens', 0))}")
        self.ov["info3"].setStringValue_(f"{_time(s['first_ts'])}  ~  {_time(s['last_ts'])}")

    @objc.python_method
    def _reload_sessions(self):
        self.all_paths = stats.list_sessions()
        self.all_metas = [stats.quick_meta(p) for p in self.all_paths]
        self._apply_filter(self.search_field.stringValue() if self.search_field else "")

    @objc.python_method
    def _apply_filter(self, query):
        """按 项目名/路径/session_id 子串过滤会话列表，实时刷新。"""
        q = (query or "").strip().lower()
        if not q:
            self.paths, self.metas = list(self.all_paths), list(self.all_metas)
        else:
            self.paths, self.metas = [], []
            for p, m in zip(self.all_paths, self.all_metas):
                if (q in m["project"].lower() or q in m["session_id"].lower()
                        or q in m["cwd"].lower()):
                    self.paths.append(p)
                    self.metas.append(m)
        if not self.session_table:
            return
        self.session_table.reloadData()
        if self.paths:
            self.session_table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(0), False)
            self._load_summary(0)
            self._update_overview()
        else:  # 无匹配：右侧清空
            self.summary, self.tasks = None, []
        self.task_table.reloadData()

    def controlTextDidChange_(self, note):
        if note.object() is self.search_field:
            self._apply_filter(self.search_field.stringValue())

    @objc.python_method
    def _load_summary(self, idx):
        path = self.paths[idx]
        if path not in self.cache:
            s = stats.parse_session(path)
            stats.attach_reds(s)
            self.cache[path] = s
        self.summary = self.cache[path]
        self.tasks = self.summary["tasks"]

    # ---- 表格数据源（view-based）----
    def numberOfRowsInTableView_(self, tv):
        return len(self.paths) if tv is self.session_table else len(self.tasks)

    def tableView_viewForTableColumn_row_(self, tv, col, row):
        if tv is self.session_table:
            return self._session_cell(row)
        return self._task_cell(col.identifier(), row)

    @objc.python_method
    def _session_cell(self, row):
        m = self.metas[row]
        w, h = 300, ROW_SESSION
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        v.setAutoresizesSubviews_(True)
        path = _label(m["cwd"], 12.5, NSColor.labelColor(), NSTextAlignmentLeft)
        path.setFrame_(NSMakeRect(6, h - 25, w - 12, 18))
        path.setAutoresizingMask_(NSViewWidthSizable)
        sid = _label(m["session_id"], 10.5, NSColor.secondaryLabelColor(), NSTextAlignmentLeft)
        sid.setFrame_(NSMakeRect(6, h - 44, w - 12, 16))
        sid.setAutoresizingMask_(NSViewWidthSizable)
        tm = _label(m["created"], 10, NSColor.tertiaryLabelColor(), NSTextAlignmentRight)
        tm.setFrame_(NSMakeRect(6, 5, w - 14, 14))  # 右边距略大，避开滚动条
        tm.setAutoresizingMask_(NSViewWidthSizable)
        for lbl in (path, sid, tm):
            v.addSubview_(lbl)
        return v

    @objc.python_method
    def _task_cell(self, ident, row):
        align = NSTextAlignmentRight if ident in ("out", "tools", "reds") else NSTextAlignmentLeft
        lbl = _label(self._task_text(ident, row), 12, NSColor.labelColor(), align)
        lbl.setFrame_(NSMakeRect(4, 3, 120, 16))
        lbl.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        return lbl

    @objc.python_method
    def _task_text(self, ident, row):
        t = self.tasks[row]
        if ident == "idx":
            return str(row + 1)
        if ident == "prompt":
            return t["prompt"].strip().replace("\n", " ")[:60]
        if ident == "dur":
            return _dur(t["duration"])
        if ident == "out":
            return _tok(t["usage"].get("output_tokens", 0))
        if ident == "tools":
            return str(sum(t["tools"].values()))
        if ident == "reds":
            return str(t["reds"])
        if ident == "time":
            return _time(t["start_ts"])
        return ""

    # ---- 点击 ----
    def onSessionClick_(self, sender):
        row = self.session_table.clickedRow()
        if row < 0:
            row = self.session_table.selectedRow()
        if row < 0 or row >= len(self.paths):
            return
        self._load_summary(row)
        self._update_overview()
        self.task_table.reloadData()

    def onTaskDouble_(self, sender):
        row = self.task_table.clickedRow()
        if 0 <= row < len(self.tasks):
            self._show_detail(self.tasks[row], row)

    def onCopySession_(self, sender):
        """复制 `claude --resume <id>` 到剪贴板，可到终端直接粘贴继续该会话。"""
        if not self.summary:
            return
        cmd = f"claude --resume {self.summary['session_id']}"
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(cmd, NSPasteboardTypeString)
        sender.setTitle_("已复制 ✓")
        self.performSelector_withObject_afterDelay_("_resetCopyTitle:", None, 1.2)

    def _resetCopyTitle_(self, _arg):
        btn = self.ov.get("copy_btn")
        if btn is not None:
            btn.setTitle_("复制会话命令")

    # ---- 详情二级窗口（三分区 + 上/下一条）----
    @objc.python_method
    def _show_detail(self, t, row):
        if self.detail_win is None:
            self._build_detail()
        self._fill_detail(row)
        self._layout_detail(self.detail_box.bounds().size)
        self._front(self.detail_win)

    @objc.python_method
    def _build_detail(self):
        style = (NSTitledWindowMask | NSClosableWindowMask
                 | NSResizableWindowMask | NSMiniaturizableWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, DETAIL_W, DETAIL_H), style, NSBackingStoreBuffered, False)
        win.setReleasedWhenClosed_(False)
        win.center()

        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, DETAIL_W, DETAIL_H))
        self.di_title = _label("任务信息", 12, NSColor.secondaryLabelColor(), NSTextAlignmentLeft, bold=True)
        self.df_title = _label("", 12, NSColor.secondaryLabelColor(), NSTextAlignmentLeft)
        self.df_title.setAttributedStringValue_(_files_title_attr())
        self.ds_title = _label("提交内容", 12, NSColor.secondaryLabelColor(), NSTextAlignmentLeft, bold=True)
        self.dr_title = _label("返回内容", 12, NSColor.secondaryLabelColor(), NSTextAlignmentLeft, bold=True)
        self.di_view = self._build_info_view()          # 任务信息 dashboard
        self.df_tv = _textview(mono=False)              # 改动文件（link 可点击复制）
        self.df_tv.setDelegate_(self)
        self.df_scroll = _scroll(self.df_tv, border=True)
        self.ds_tv = _textview(mono=True)               # 提交内容
        self.ds_scroll = _scroll(self.ds_tv, border=True)
        self.dr_web = WKWebView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
        self.prev_btn = _button("上一条", "onPrev:", self)
        self.next_btn = _button("下一条", "onNext:", self)
        for sub in (self.di_title, self.di_view, self.df_title, self.df_scroll,
                    self.ds_title, self.ds_scroll, self.dr_title, self.dr_web,
                    self.prev_btn, self.next_btn):
            box.addSubview_(sub)
        win.setContentView_(box)
        win.setDelegate_(self)
        self.detail_win = win
        self.detail_box = box

    @objc.python_method
    def _build_info_view(self):
        """任务信息 dashboard：一行 KPI（耗时/输出/红灯/工具）+ token 明细行 + 时间行。"""
        v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, DETAIL_W - 20, INFO_H))
        v.setAutoresizesSubviews_(True)
        gray = NSColor.secondaryLabelColor()
        self.di_kpi = {}
        for i, (key, name) in enumerate([("dur", "耗时"), ("out", "输出"),
                                         ("red", "红灯"), ("tool", "工具")]):
            x = 2 + i * 118
            val = _label("—", 15, NSColor.labelColor(), NSTextAlignmentLeft, bold=True)
            val.setFrame_(NSMakeRect(x, INFO_H - 28, 112, 20))
            lab = _label(name, 10, gray, NSTextAlignmentLeft)
            lab.setFrame_(NSMakeRect(x, INFO_H - 42, 112, 13))
            v.addSubview_(val)
            v.addSubview_(lab)
            self.di_kpi[key] = val
        self.di_tokline = _label("", 11, gray, NSTextAlignmentLeft)
        self.di_tokline.setFrame_(NSMakeRect(2, 20, DETAIL_W - 44, 16))
        self.di_tokline.setAutoresizingMask_(NSViewWidthSizable)
        self.di_metaline = _label("", 11, gray, NSTextAlignmentLeft)
        self.di_metaline.setFrame_(NSMakeRect(2, 2, DETAIL_W - 44, 16))
        self.di_metaline.setAutoresizingMask_(NSViewWidthSizable)
        v.addSubview_(self.di_tokline)
        v.addSubview_(self.di_metaline)
        return v

    @objc.python_method
    def _fill_detail(self, row):
        self.detail_row = row
        t = self.tasks[row]
        u = t["usage"]
        self.detail_win.setTitle_(f"任务 #{row + 1} 详情 · {self.summary['project']}")
        # 任务信息 dashboard
        self.di_kpi["dur"].setStringValue_(_dur(t["duration"]))
        self.di_kpi["out"].setStringValue_(_tok(u.get("output_tokens", 0)))
        self.di_kpi["red"].setStringValue_(str(t["reds"]))
        self.di_kpi["tool"].setStringValue_(str(sum(t["tools"].values())))
        self.di_tokline.setStringValue_(
            f"输入 {_tok(u.get('input_tokens', 0))} · 输出 {_tok(u.get('output_tokens', 0))} · "
            f"缓存读 {_tok(u.get('cache_read_input_tokens', 0))} · 缓存写 {_tok(u.get('cache_creation_input_tokens', 0))}")
        self.di_metaline.setStringValue_(
            f"{_time(t['start_ts'])}    ·    任务 {row + 1}/{len(self.tasks)}    ·    模型 {self.summary['model'] or '—'}")
        # 改动文件（可点击复制）
        self.df_tv.textStorage().setAttributedString_(_files_attr(t["files"]))
        # 提交内容（含用户选择）
        sub = t["prompt"].strip()
        if t["asks"]:
            lines = ["", "── 用户选择 ──"]
            for a in t["asks"]:
                q = (a.get("input", {}).get("questions") or [{}])[0]
                lines.append(f"· {q.get('question', '')} → {_choice(a)}")
            sub += "\n" + "\n".join(lines)
        self.ds_tv.setString_(sub)
        # 返回内容
        self.dr_web.loadHTMLString_baseURL_(
            _render_html("\n\n".join(t["reply"]).strip() or "（无文本返回）"), None)
        self.ds_tv.scrollRangeToVisible_((0, 0))
        self.prev_btn.setEnabled_(row > 0)               # 第一条时置灰
        self.next_btn.setEnabled_(row < len(self.tasks) - 1)  # 最后一条时置灰

    def onPrev_(self, sender):
        if self.detail_row > 0:
            self._fill_detail(self.detail_row - 1)

    def onNext_(self, sender):
        if self.detail_row < len(self.tasks) - 1:
            self._fill_detail(self.detail_row + 1)

    def textView_clickedOnLink_atIndex_(self, tv, link, idx):
        """点击改动文件：复制完整路径到剪贴板，标题短暂反馈。"""
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(str(link), NSPasteboardTypeString)
        self.df_title.setAttributedStringValue_(_files_title_attr("   已复制 ✓"))
        self.performSelector_withObject_afterDelay_("_resetFilesTitle:", None, 1.2)
        return True

    def _resetFilesTitle_(self, _arg):
        self.df_title.setAttributedStringValue_(_files_title_attr())

    @objc.python_method
    def _layout_detail(self, size):
        """任务信息与按钮固定高；改动文件/提交各占中部 MID_RATIO；返回内容吃剩余。"""
        w, h = size.width, size.height
        gap, th, btn_h = 8, 20, 40
        top = h
        # 任务信息（固定高）
        self.di_title.setFrame_(NSMakeRect(10, top - th, w - 20, th - 2))
        self.di_view.setFrame_(NSMakeRect(10, top - th - INFO_H, w - 20, INFO_H))
        top -= th + INFO_H + gap
        avail = top - (btn_h + gap)          # 中部三块可用高度（按钮以上）
        mid = avail * MID_RATIO
        # 改动文件
        self.df_title.setFrame_(NSMakeRect(10, top - th, w - 20, th - 2))
        self.df_scroll.setFrame_(NSMakeRect(8, top - mid, w - 16, mid - th))
        top -= mid + gap
        # 提交内容
        self.ds_title.setFrame_(NSMakeRect(10, top - th, w - 20, th - 2))
        self.ds_scroll.setFrame_(NSMakeRect(8, top - mid, w - 16, mid - th))
        top -= mid + gap
        # 返回内容（剩余全部，到按钮上方）
        self.dr_title.setFrame_(NSMakeRect(10, top - th, w - 20, th - 2))
        self.dr_web.setFrame_(NSMakeRect(8, btn_h + gap, w - 16, (top - th) - (btn_h + gap)))
        # 按钮（固定底部）
        self.prev_btn.setFrame_(NSMakeRect(10, 8, 96, 28))
        self.next_btn.setFrame_(NSMakeRect(w - 106, 8, 96, 28))
