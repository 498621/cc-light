#!/usr/bin/env python3
"""cc-light Claude Code 配置编辑窗口（原生 AppKit）。

上方分段 tab 切换 ~/.claude/CLAUDE.md 与 ~/.claude/settings.json（tab 只显文件名，
下方显示完整路径）。CLAUDE.md 纯文本编辑；settings.json 用智能文本编辑器：JSON 语法
高亮 + 保存时校验（非法则提示不写）+ 自动美化回写。右上「修改历史」看本功能对该文件的
历次改动 diff（仅存 diff）。激活策略与 history_window 一致（accessory 需外部激活）。
"""

import difflib
import html
import json
import os
import re
import time
from datetime import datetime

import objc

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSBezelBorder,
    NSBezelStyleRounded, NSButton, NSClosableWindowMask, NSColor, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSMakeRect, NSMakeSize,
    NSMiniaturizableWindowMask, NSResizableWindowMask, NSScrollView,
    NSSegmentedControl, NSTextAlignmentLeft, NSTextField, NSTextView,
    NSTitledWindowMask, NSView, NSViewHeightSizable, NSViewMaxYMargin,
    NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable, NSWindow,
)
from Foundation import NSMutableAttributedString, NSObject
from WebKit import WKWebView

from history_window import _build_main_menu, _external_activate

W, H = 860, 700
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HIST_DIR = os.path.join(DATA_DIR, "config_history")

_J_STR = re.compile(r'"(?:[^"\\]|\\.)*"')
_J_NUM = re.compile(r'(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
_J_KW = re.compile(r'\b(?:true|false|null)\b')


def _highlight_json(tv):
    """对 NSTextView 里的 JSON 文本着色：key 蓝、字符串红、数字紫、true/false/null 橙。"""
    text = tv.string()
    ts = tv.textStorage()
    ts.beginEditing()
    ts.addAttribute_value_range_(NSForegroundColorAttributeName, NSColor.labelColor(),
                                 (0, ts.length()))
    for m in _J_NUM.finditer(text):
        ts.addAttribute_value_range_(NSForegroundColorAttributeName,
                                     NSColor.systemPurpleColor(), (m.start(), len(m.group())))
    for m in _J_KW.finditer(text):
        ts.addAttribute_value_range_(NSForegroundColorAttributeName,
                                     NSColor.systemOrangeColor(), (m.start(), len(m.group())))
    for m in _J_STR.finditer(text):
        is_key = text[m.end():].lstrip().startswith(":")
        color = NSColor.systemBlueColor() if is_key else NSColor.systemRedColor()
        ts.addAttribute_value_range_(NSForegroundColorAttributeName, color,
                                     (m.start(), len(m.group())))
    ts.endEditing()


def _hist_path(name):
    return os.path.join(HIST_DIR, name.replace("/", "_") + ".jsonl")


def _record_diff(name, old, new):
    """把一次保存的 diff 追加到历史（内容无变化则不记）。"""
    if old == new:
        return
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="旧", tofile="新", lineterm="\n"))
    if not diff.strip():
        return
    try:
        os.makedirs(HIST_DIR, exist_ok=True)
        with open(_hist_path(name), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "diff": diff}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _load_diffs(name):
    out = []
    try:
        with open(_hist_path(name), encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


_DIFF_CSS = """
:root { color-scheme: light dark; }
body { font: 13px -apple-system, system-ui, sans-serif; margin: 18px; color: #1d1d1f; }
@media (prefers-color-scheme: dark) { body { color: #e6e6ea; } }
h1 { font-size: 1.35em; margin: 0 0 .8em; }
.rec { border: 1px solid rgba(127,127,127,.28); border-radius: 8px; margin: 0 0 14px; overflow: hidden; }
.ts { padding: 6px 12px; font-weight: 600; background: rgba(127,127,127,.12); font-size: .92em; }
.diff { font-family: ui-monospace, Menlo, monospace; font-size: 12px; padding: 6px 0; overflow-x: auto; }
.ln { padding: 0 12px; white-space: pre; }
.add { background: rgba(52,199,89,.18); }
.del { background: rgba(255,69,58,.18); }
.hunk { color: #8e8e93; }
.meta { color: #8e8e93; }
.empty { opacity: .55; }
"""


def _diff_html(name, records):
    body = ""
    for r in reversed(records):
        ts = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        lines = ""
        for ln in r.get("diff", "").splitlines():
            cls = "ln ctx"
            if ln.startswith("+++") or ln.startswith("---"):
                cls = "ln meta"
            elif ln.startswith("+"):
                cls = "ln add"
            elif ln.startswith("-"):
                cls = "ln del"
            elif ln.startswith("@@"):
                cls = "ln hunk"
            lines += f'<div class="{cls}">{html.escape(ln) or "&nbsp;"}</div>'
        body += f'<div class="rec"><div class="ts">{ts}</div><div class="diff">{lines}</div></div>'
    if not records:
        body = '<p class="empty">（本功能尚未修改过该文件）</p>'
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_DIFF_CSS}</style></head>"
            f"<body><h1>{html.escape(name)} · 修改历史</h1>{body}</body></html>")


def _editor_textview():
    tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
    tv.setEditable_(True)
    tv.setRichText_(False)
    tv.setFont_(NSFont.userFixedPitchFontOfSize_(12.5))
    tv.setTextContainerInset_(NSMakeSize(8, 8))
    tv.setAutomaticQuoteSubstitutionEnabled_(False)   # 别把 " 变成弯引号，毁掉 JSON
    tv.setAutomaticDashSubstitutionEnabled_(False)
    tv.setHorizontallyResizable_(False)
    tv.setVerticallyResizable_(True)
    tv.textContainer().setWidthTracksTextView_(True)
    return tv


class ConfigController(NSObject):
    def init(self):
        self = objc.super(ConfigController, self).init()
        if self is None:
            return None
        home = os.path.expanduser("~/.claude")
        self.tabs = [
            {"name": "CLAUDE.md", "path": os.path.join(home, "CLAUDE.md"), "json": False},
            {"name": "settings.json", "path": os.path.join(home, "settings.json"), "json": True},
        ]
        self.cur = 0
        self.loaded_text = ""
        self.win = None
        self.editor = None
        self.path_label = None
        self.err_label = None
        self.menu = None
        self.hist_win = None
        self.hist_web = None
        self._pending_win = None
        self._activate_tries = 0
        return self

    # ---- 主窗口 ----
    @objc.python_method
    def show(self):
        if self.win is not None:
            self._load(self.cur)
            self._front(self.win)
            return
        style = (NSTitledWindowMask | NSClosableWindowMask
                 | NSResizableWindowMask | NSMiniaturizableWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        win.setTitle_("Claude Code 配置编辑")
        win.setReleasedWhenClosed_(False)
        win.center()
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))

        seg = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(14, H - 38, 260, 26))
        seg.setSegmentCount_(len(self.tabs))
        for i, t in enumerate(self.tabs):
            seg.setLabel_forSegment_(t["name"], i)
            seg.setWidth_forSegment_(126, i)
        seg.setSelectedSegment_(0)
        seg.setTarget_(self)
        seg.setAction_("onTab:")
        seg.setAutoresizingMask_(NSViewMinYMargin)

        hist = NSButton.alloc().initWithFrame_(NSMakeRect(W - 108, H - 34, 94, 20))
        hist.setBordered_(False)
        hist.setAttributedTitle_(NSMutableAttributedString.alloc().initWithString_attributes_(
            "修改历史", {NSForegroundColorAttributeName: NSColor.linkColor(),
                     NSFontAttributeName: NSFont.systemFontOfSize_(12)}))
        hist.setTarget_(self)
        hist.setAction_("onHistory:")
        hist.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)

        self.path_label = NSTextField.alloc().initWithFrame_(NSMakeRect(14, H - 62, W - 28, 18))
        self.path_label.setBezeled_(False)
        self.path_label.setDrawsBackground_(False)
        self.path_label.setEditable_(False)
        self.path_label.setFont_(NSFont.systemFontOfSize_(11))
        self.path_label.setTextColor_(NSColor.secondaryLabelColor())
        self.path_label.setAutoresizingMask_(NSViewMinYMargin | NSViewWidthSizable)

        self.editor = _editor_textview()
        edscroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 52, W - 24, H - 122))
        edscroll.setHasVerticalScroller_(True)
        edscroll.setBorderType_(NSBezelBorder)
        edscroll.setDocumentView_(self.editor)
        edscroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        self.err_label = NSTextField.alloc().initWithFrame_(NSMakeRect(14, 16, W - 240, 20))
        self.err_label.setBezeled_(False)
        self.err_label.setDrawsBackground_(False)
        self.err_label.setEditable_(False)
        self.err_label.setFont_(NSFont.systemFontOfSize_(12))
        self.err_label.setAutoresizingMask_(NSViewMaxYMargin | NSViewWidthSizable)

        save = NSButton.alloc().initWithFrame_(NSMakeRect(W - 100, 12, 86, 30))
        save.setTitle_("保存")
        save.setBezelStyle_(NSBezelStyleRounded)
        save.setKeyEquivalent_("\r")          # 回车即保存（这里默认按钮蓝色是合理的）
        save.setTarget_(self)
        save.setAction_("onSave:")
        save.setAutoresizingMask_(NSViewMaxYMargin | NSViewMinXMargin)

        for sub in (seg, hist, self.path_label, edscroll, self.err_label, save):
            box.addSubview_(sub)
        win.setContentView_(box)
        win.setDelegate_(self)
        self.win = win
        self._load(0)
        self._front(win)

    @objc.python_method
    def _load(self, idx):
        self.cur = idx
        t = self.tabs[idx]
        self.path_label.setStringValue_(t["path"])
        try:
            with open(t["path"], encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            raw = ""
        if t["json"] and raw.strip():
            try:                              # 加载即美化，非法则原样显示
                raw = json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n"
            except ValueError:
                pass
        self.editor.setString_(raw)
        if t["json"]:
            _highlight_json(self.editor)
        self.loaded_text = self.editor.string()
        self._flash("", None)

    @objc.python_method
    def _flash(self, msg, color):
        self.err_label.setStringValue_(msg or "")
        if color is not None:
            self.err_label.setTextColor_(color)

    def onTab_(self, sender):
        self._load(sender.selectedSegment())

    def onSave_(self, sender):
        t = self.tabs[self.cur]
        text = self.editor.string()
        if t["json"]:
            try:
                obj = json.loads(text)
            except ValueError as e:
                self._flash(f"JSON 错误：第 {e.lineno} 行 · {e.msg}", NSColor.systemRedColor())
                return
            out = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        else:
            out = text
        try:
            with open(t["path"], "w", encoding="utf-8") as f:
                f.write(out)
        except OSError as e:
            self._flash(f"写入失败：{e}", NSColor.systemRedColor())
            return
        _record_diff(t["name"], self.loaded_text, out)
        self.editor.setString_(out)           # 回显美化结果
        if t["json"]:
            _highlight_json(self.editor)
        self.loaded_text = out
        self._flash("已保存 ✓", NSColor.systemGreenColor())

    def onHistory_(self, sender):
        name = self.tabs[self.cur]["name"]
        html_doc = _diff_html(name, _load_diffs(name))
        if self.hist_win is None:
            style = (NSTitledWindowMask | NSClosableWindowMask
                     | NSResizableWindowMask | NSMiniaturizableWindowMask)
            hw = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 640, 620), style, NSBackingStoreBuffered, False)
            hw.setReleasedWhenClosed_(False)
            hw.center()
            self.hist_web = WKWebView.alloc().initWithFrame_(NSMakeRect(0, 0, 640, 620))
            hw.setContentView_(self.hist_web)
            self.hist_win = hw
        self.hist_win.setTitle_(f"{name} · 修改历史")
        self.hist_web.loadHTMLString_baseURL_(html_doc, None)
        self._front(self.hist_win)

    # ---- 激活（与 history_window 一致：切 Regular + 外部激活 + 兜底重试）----
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
        _external_activate()
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
