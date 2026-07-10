#!/usr/bin/env python3
"""幂等地把 cc-light 的 hooks 装入 / 移除 ~/.claude/settings.json。

  install_hooks.py install   # 先剔除本工具旧 hook，再装入（可反复运行，结果一致）
  install_hooks.py remove    # 仅剔除本工具的 hook，保留用户其它配置与其它 hooks

只处理命令里含 'cc-light/hook.py' 的条目，绝不动用户的 statusLine / 其它 hooks。
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-light 项目根目录
HOOK_PY = os.path.join(BASE, "hook.py")                             # hook.py 真实绝对路径
SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOKS_SRC = os.path.join(BASE, "settings.hooks.json")
PLACEHOLDER = "__CC_LIGHT_HOOK__"  # 模板里的占位符，安装时替换为 HOOK_PY，故项目放哪都能用
# 识别本工具 hook：新装用真实绝对路径；旧装写死过 $HOME/proj/cc-light，用通用子串兼容以便升级清理。
MARKS = (HOOK_PY, "cc-light/hook.py")


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _is_ours(group):
    return any(
        any(m in h.get("command", "") for m in MARKS) for h in group.get("hooks", [])
    )


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "install"
    cfg = _load(SETTINGS, {})
    hooks_cfg = cfg.get("hooks", {})

    # 幂等：先去掉本工具已有的 hook 组（按标记识别），保留其它 hook 组。
    for event in list(hooks_cfg.keys()):
        hooks_cfg[event] = [g for g in hooks_cfg[event] if not _is_ours(g)]
        if not hooks_cfg[event]:
            del hooks_cfg[event]

    if action == "install":
        for event, groups in _load(HOOKS_SRC, {"hooks": {}})["hooks"].items():
            for g in groups:
                for h in g.get("hooks", []):
                    if "command" in h:
                        # 占位符替换成本机真实路径，安装到用户 settings.json
                        h["command"] = h["command"].replace(PLACEHOLDER, HOOK_PY)
            hooks_cfg.setdefault(event, []).extend(groups)

    if hooks_cfg:
        cfg["hooks"] = hooks_cfg
    else:
        cfg.pop("hooks", None)

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[hooks] {action} 完成，当前 hook 事件:", list(cfg.get("hooks", {}).keys()))


if __name__ == "__main__":
    main()
