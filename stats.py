#!/usr/bin/env python3
"""从 Claude Code 的 session jsonl 解析出会话/任务级统计。

数据源：~/.claude/projects/<cwd转义>/<session_id>.jsonl —— Claude Code 自己逐事件记录的
transcript。本模块纯只读解析，不依赖 cc-light 自身采集（红灯次数等 cc-light 独有数据由
events/<sid>.jsonl 另行补入，见 attach_reds）。

任务切分：一次「任务」= 用户一次真实文字提交（promptSource='typed' 且 content 为字符串）
到下一次真实提交之前的全部事件。中间的 tool_result（content 为列表）不算新任务。
"""

import glob
import json
import os
from datetime import datetime

PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# cc-light 独有的状态事件（红灯时间线），与 hook.py 的 EVENTS_DIR 同路径。
EVENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "events")
# 会话收藏（收藏星标），存在 data/favorites.json。
FAV_PATH = os.path.join(os.path.dirname(EVENTS_DIR), "favorites.json")


def load_favorites():
    """读取收藏的 session_id 集合。"""
    try:
        with open(FAV_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("favorites", []))
    except (OSError, ValueError):
        return set()


def toggle_favorite(session_id):
    """切换某会话的收藏状态，返回切换后是否已收藏。"""
    favs = load_favorites()
    if session_id in favs:
        favs.discard(session_id)
    else:
        favs.add(session_id)
    try:
        os.makedirs(os.path.dirname(FAV_PATH), exist_ok=True)
        with open(FAV_PATH, "w", encoding="utf-8") as f:
            json.dump({"favorites": sorted(favs)}, f)
    except OSError:
        pass
    return session_id in favs

# 会改动文件的工具：从其 tool_use 参数里提取文件路径，供任务级「文件改动」展示。
_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# 模型定价（美元 / 百万 token）。近似值，需按官方价核对/调整（cache_w=缓存写, cache_r=缓存读）。
# 未知模型按 sonnet 估算。仅供成本粗估参考。需验证。
MODEL_PRICING = {
    "opus":   {"in": 15.0, "out": 75.0, "cache_w": 18.75, "cache_r": 1.5},
    "sonnet": {"in": 3.0,  "out": 15.0, "cache_w": 3.75,  "cache_r": 0.30},
    "haiku":  {"in": 0.80, "out": 4.0,  "cache_w": 1.0,   "cache_r": 0.08},
}


def _price_for(model):
    m = (model or "").lower()
    for key, price in MODEL_PRICING.items():
        if key in m:
            return price
    return MODEL_PRICING["sonnet"]


def usage_cost(usage, model):
    """按模型定价把一份 usage 折算成美元（粗估）。"""
    p = _price_for(model)
    return (usage.get("input_tokens", 0) * p["in"]
            + usage.get("output_tokens", 0) * p["out"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_w"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_r"]) / 1_000_000


def _ts(iso: str):
    """ISO8601（尾部 Z）转 epoch 秒；解析失败返回 None。"""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(content) -> str:
    """从 message.content 抽取纯文本：字符串直接返回，列表取所有 text block 拼接。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _sum_usage(dst: dict, usage: dict) -> None:
    """把一条 assistant 的 usage 累加进 dst（分列保留，便于区分计费口径）。"""
    for k in ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens"):
        dst[k] = dst.get(k, 0) + (usage.get(k) or 0)


def parse_session(path: str) -> dict:
    """解析单个 session jsonl，返回会话汇总 + 任务列表。"""
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue

    session_id = os.path.splitext(os.path.basename(path))[0]
    tasks = []
    cur = None
    meta = {"cwd": "", "model": "", "gitBranch": ""}

    for r in rows:
        typ = r.get("type")
        msg = r.get("message", {}) if isinstance(r.get("message"), dict) else {}
        ts = _ts(r.get("timestamp"))
        if r.get("cwd"):
            meta["cwd"] = r["cwd"]
        if r.get("gitBranch"):
            meta["gitBranch"] = r["gitBranch"]

        # 任务起点：真实文字提交
        if (typ == "user" and r.get("promptSource") == "typed"
                and isinstance(msg.get("content"), str)):
            if cur:
                tasks.append(cur)
            cur = {
                "prompt": msg["content"],
                "start_ts": ts,
                "end_ts": ts,
                "usage": {},
                "tools": {},
                "asks": [],       # AskUserQuestion：问题 + 用户选择
                "reds": 0,        # 红灯次数，attach_reds 补入
                "reply": [],      # 该任务内 assistant 的文本段
                "files": [],      # 本任务改动过的文件（Edit/Write 等）
            }
            continue

        if cur is None:
            continue  # 任务开始前的零星行

        if typ == "assistant":
            if ts:
                cur["end_ts"] = ts
            if meta["model"] == "" and msg.get("model"):
                meta["model"] = msg["model"]
            _sum_usage(cur["usage"], msg.get("usage", {}) or {})
            for b in msg.get("content", []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name = b.get("name", "?")
                    cur["tools"][name] = cur["tools"].get(name, 0) + 1
                    if name == "AskUserQuestion":
                        cur["asks"].append({"input": b.get("input", {}), "tool_use_id": b.get("id")})
                    if name in _FILE_TOOLS:
                        inp = b.get("input", {}) or {}
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp and fp not in cur["files"]:
                            cur["files"].append(fp)
                elif b.get("type") == "text" and b.get("text", "").strip():
                    cur["reply"].append(b["text"])
        elif typ == "user":
            # tool_result：可能含 AskUserQuestion 的用户选择结果，回填到对应 ask
            content = msg.get("content")
            if isinstance(content, list):
                if ts:
                    cur["end_ts"] = ts
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        for a in cur["asks"]:
                            if a.get("tool_use_id") == b.get("tool_use_id"):
                                a["result"] = _text_of(b.get("content"))

    if cur:
        tasks.append(cur)

    # 会话级汇总
    total = {}
    for t in tasks:
        _sum_usage(total, t["usage"])
        t["duration"] = (t["end_ts"] - t["start_ts"]) if (t["start_ts"] and t["end_ts"]) else 0
    starts = [t["start_ts"] for t in tasks if t["start_ts"]]
    ends = [t["end_ts"] for t in tasks if t["end_ts"]]
    return {
        "session_id": session_id,
        "path": path,
        "project": os.path.basename(meta["cwd"]) or os.path.basename(os.path.dirname(path)),
        "cwd": meta["cwd"],
        "model": meta["model"],
        "gitBranch": meta["gitBranch"],
        "task_count": len(tasks),
        "total_usage": total,
        "total_cost": usage_cost(total, meta["model"]),
        "first_ts": min(starts) if starts else None,
        "last_ts": max(ends) if ends else None,
        "tasks": tasks,
    }


def attach_reds(summary: dict) -> None:
    """把 cc-light 记录的红灯(needs)事件按任务时间区间归并到各任务的 reds。

    events 仅在 cc-light 运行期间产生，故装灯之前的老会话无数据（reds 保持 0）。
    """
    path = os.path.join(EVENTS_DIR, summary["session_id"] + ".jsonl")
    reds = []
    try:
        for line in open(path, encoding="utf-8"):
            e = json.loads(line)
            if e.get("state") == "needs" and e.get("ts"):
                reds.append(e["ts"])
    except (FileNotFoundError, ValueError):
        pass
    if not reds:
        return
    for t in summary["tasks"]:
        s, e = t["start_ts"], t["end_ts"]
        if s and e:
            t["reds"] = sum(1 for r in reds if s <= r <= e)


def list_sessions() -> list:
    """扫所有项目下的 jsonl，返回按最后活动时间倒序的会话路径。"""
    return sorted(
        glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )


def aggregate(paths=None):
    """解析全部会话并按 日期/项目/模型 聚合 会话数/任务数/输出token/成本，供用量统计窗口。"""
    paths = paths if paths is not None else list_sessions()
    by_day, by_project, by_model = {}, {}, {}
    total = {"sessions": 0, "tasks": 0, "out": 0, "cost": 0.0}

    def _acc(bucket, key, out, tasks, cost):
        b = bucket.setdefault(key, {"sessions": 0, "tasks": 0, "out": 0, "cost": 0.0})
        b["sessions"] += 1
        b["tasks"] += tasks
        b["out"] += out
        b["cost"] += cost

    for p in paths:
        try:
            s = parse_session(p)
        except Exception:
            continue
        out = s["total_usage"].get("output_tokens", 0)
        cost, tasks = s["total_cost"], s["task_count"]
        day = (datetime.fromtimestamp(s["first_ts"]).strftime("%Y-%m-%d")
               if s["first_ts"] else "未知")
        _acc(by_day, day, out, tasks, cost)
        _acc(by_project, s["project"], out, tasks, cost)
        _acc(by_model, s["model"] or "未知", out, tasks, cost)
        total["sessions"] += 1
        total["tasks"] += tasks
        total["out"] += out
        total["cost"] += cost
    return {"total": total, "by_day": by_day, "by_project": by_project, "by_model": by_model}


def quick_meta(path: str) -> dict:
    """只读前若干行拿完整 cwd + 会话创建时间（首个 timestamp），供列表快速渲染。"""
    cwd, created_ts = "", None
    try:
        with open(path, encoding="utf-8") as f:
            for _ in range(40):  # 首行常是不含 cwd/timestamp 的元数据行，扫前若干行
                line = f.readline()
                if not line:
                    break
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not cwd and r.get("cwd"):
                    cwd = r["cwd"]
                if created_ts is None and r.get("timestamp"):
                    created_ts = _ts(r["timestamp"])
                if cwd and created_ts:
                    break
    except OSError:
        pass
    ts = created_ts or os.path.getmtime(path)  # 拿不到创建时间就退回文件 mtime
    return {
        "session_id": os.path.splitext(os.path.basename(path))[0],
        "cwd": cwd or "（未知目录）",
        "project": os.path.basename(cwd) or "（未知）",
        "created": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    import sys
    paths = sys.argv[1:] or list_sessions()[:1]
    for p in paths:
        s = parse_session(p)
        u = s["total_usage"]
        print(f"\n项目={s['project']}  session={s['session_id'][:8]}  model={s['model']}")
        print(f"  任务数={s['task_count']}  "
              f"tokens: in={u.get('input_tokens',0)} out={u.get('output_tokens',0)} "
              f"cache_r={u.get('cache_read_input_tokens',0)} cache_w={u.get('cache_creation_input_tokens',0)}")
        for i, t in enumerate(s["tasks"], 1):
            tu = t["usage"]
            tools = " ".join(f"{k}×{v}" for k, v in t["tools"].items())
            print(f"  #{i} 耗时{t['duration']:.0f}s  "
                  f"out={tu.get('output_tokens',0)}tok  工具[{tools}]  "
                  f"ask×{len(t['asks'])}  prompt={t['prompt'][:40]!r}")
