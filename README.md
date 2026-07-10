# cc-light

> 给 Claude Code 装一盏菜单栏交通灯 —— 一眼看清每个会话在忙、在等你、还是空着。
>
> A menu-bar traffic light for the Claude Code CLI.

![platform](https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white)
![python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![shell](https://img.shields.io/badge/shell-zsh%20%7C%20bash%20%7C%20fish-4EAA25?logo=gnubash&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-blue)

同时开好几个 Claude Code 会话时，你很难知道哪个在跑、哪个卡在等你确认、哪个已经空闲。cc-light 把这些状态
收进 macOS 菜单栏的一个圆点里：绿=工作中、红=等待你、灰=空闲。点开还能看到每个会话是哪个项目，一键跳回它
所在的 iTerm2 标签页。

它由 Claude Code 的 hooks 驱动，不侵入、不联网、无第三方服务，纯本地文件通信。

---

## 预览

```
菜单栏   🔴2 🟢1 ⚪3          ← 分状态计数模式（或合并为一个灯：🔴）
         │
         ▼  点击展开
   ┌──────────────────────────────────┐
   │ 🔴 api-gateway — 等待中   ·a1b2c3 │   ← 点任意会话，跳到它的 iTerm2 标签页
   │ 🔴 web         — 等待中   ·d4e5f6 │
   │ 🟢 std         — 工作中   ·04c8b6 │
   │ ⚪ docs        — 空闲     ·7788aa │
   │ ──────────────────────────────── │
   │ ☐ 菜单栏分状态计数                │
   │ ☑ 开机自启动                     │
   │ ──────────────────────────────── │
   │ 退出 cc-light                    │
   └──────────────────────────────────┘
```

---

## 特性

- **三态一目了然** —— 🟢 工作中 / 🔴 等待你 / ⚪ 空闲，250ms 刷新，跟手不迟滞。
- **多会话聚合** —— 菜单栏可选「合并为一个灯」（按优先级取最高）或「分状态计数」（`🔴2 🟢1 ⚪3`），偏好会记住。
- **一键跳转** —— 下拉里点某个会话，直接把 iTerm2 切到它所在的标签页。
- **开机自启** —— 菜单里勾选即可，下次登录自动亮灯，取消不影响当前运行。
- **零打扰** —— 不占 Dock、不进 Cmd+Tab，只在菜单栏留一个圆点；单实例锁保证永远只有一盏灯。
- **不侵入 Claude Code** —— 钩子用系统 Python、只读写本地状态文件、异常静默吞掉，绝不阻断你的会话。
- **开箱即用** —— 一条命令装好依赖、配好钩子、写好 shell 别名。

---

## 快速开始

```bash
# 克隆或拷贝到任意目录后，跑一次（把下面换成你的实际路径）：
/path/to/cc-light/start.sh
```

> 放哪个目录都行——安装时 hook 会自动指向该目录下的 `hook.py`，无需固定路径。

这一条命令会自动完成：

1. 安装依赖 `rumps`（若缺失）；
2. 把 hooks 幂等合并进 `~/.claude/settings.json`（只加自己的，不动你其它配置）；
3. 识别你当前的 shell（zsh / bash / fish / 其它），把 `alias cc-light` 写入对应的 profile；
4. 启动菜单栏灯。

之后**新开一个终端，直接输入 `cc-light` 即可启动**，无需再进目录。

> 改动了 hooks 后，需要重启对应的 Claude Code 会话才会生效。

---

## 使用

| 操作 | 方式 |
| --- | --- |
| 启动 | 终端输入 `cc-light`（首次装好别名后任意终端可用） |
| 退出 | 点菜单栏圆点 →「退出 cc-light」 |
| 切换显示模式 | 点圆点 → 勾选/取消「菜单栏分状态计数」 |
| 开机自启 | 点圆点 → 勾选/取消「开机自启动」（下次登录生效） |
| 跳到某会话 | 点圆点 → 点该会话行（首次会弹 macOS 自动化授权，允许控制 iTerm2 即可） |

---

## 状态说明

| 灯 | 含义 | 触发时机 |
| :-: | --- | --- |
| 🟢 | 工作中 | 你提交了提问，或 Claude 正在执行工具 |
| 🔴 | 等待你 | Claude 需要你关注：请求工具权限、或向你提问 |
| ⚪ | 空闲 | 等待输入，或本轮回答已结束 |

- **合并为一个灯**（默认）：多个会话时按优先级取最高显示，`🔴 > 🟢 > ⚪`。
- **分状态计数**：如 `🔴2 🟢1 ⚪3`，直接数清几个在等、几个在跑、几个空闲。

> 空闲满 60 秒时 Claude Code 会发一条「等待输入」的系统通知，cc-light 会把它按空闲（灰）处理，不误报成红。

> 会话只要 Claude 进程还开着就常驻菜单（哪怕长时间不操作），可随时点击跳转。绿灯是有心跳的活动态：若超过 120 秒无活动（如任务被 Esc 中断，Claude Code 不发结束事件），会自动降级为空闲，不会一直卡绿。

---

## 工作原理

```
   Claude Code 会话                              cc-light
 ┌────────────────────┐    事件(hook)         ┌────────────────────────┐
 │ 提交 / 用工具 / 通知 │ ───────────────────▶ │ hook.py                │
 │ / 结束 / 启动 / 退出 │                       │ 写该会话状态 JSON       │
 └────────────────────┘                        └───────────┬────────────┘
                                                            │
                                   ~/.claude/cc-light/status/<session>.json
                                                            │
                                                            ▼
                                                ┌────────────────────────┐
                                                │ cc_light.py            │  每 250ms 轮询
                                                │ 菜单栏图标 + 下拉列表    │  点击 → 跳 iTerm2
                                                └────────────────────────┘
```

- Claude Code 在关键事件上调用 `hook.py`，把 `{会话, 状态, 项目目录, iTerm 标识}` 原子写入状态文件。
- `cc_light.py` 是一个 [rumps](https://github.com/jaredks/rumps) 菜单栏程序，轮询状态文件并渲染。
- 钩子刻意用系统 `/usr/bin/python3`（仅标准库，与你的 Anaconda / venv / PATH 完全解耦），且无论如何都
  `exit 0`、不向 stdout 输出，从机制上保证**永远不会拖慢或打断 Claude Code**。

---

## 系统要求

- macOS
- Python 3.9+，以及 `rumps`（`start.sh` 会自动安装）
- iTerm2 —— 仅「点击跳转」功能需要；不用 iTerm2 也能正常看灯

---

## 项目结构

```
cc-light/
├── cc_light.py           # 菜单栏程序（rumps）：轮询状态、渲染、跳转、自启开关
├── hook.py               # Claude Code 钩子处理器：把会话状态写成 JSON
├── start.sh              # 安装依赖 + 配 hooks + 写 shell 别名 + 启动
├── uninstall.sh          # 卸载：停止、关自启、清 hooks / 状态 / 别名
├── scripts/
│   └── install_hooks.py  # 幂等地增删 ~/.claude/settings.json 里本工具的 hooks
├── settings.hooks.json   # hooks 参考片段（异机复现用）
├── requirements.txt
└── README.md
```

---

## 常见问题

**菜单栏没出现圆点？**
Anaconda 等非 framework 版 Python 跑菜单栏程序偶尔不显示图标。装上 GUI 启动器后重启即可（`start.sh` 会自动优先用 `pythonw`）：

```bash
conda install -y python.app
cc-light
```

**只显示了一个会话，其它没出现？**
只有触发过事件的会话才会写状态文件。在 hooks 装好之前就开着、且一直空闲的老会话不会出现——去那个会话里发一句话，或重启它即可注册。

**点击会话没跳转？**
该会话不是在 iTerm2 里启动的（拿不到 iTerm 标识），或首次点击时未授予自动化权限。到「系统设置 → 隐私与安全性 → 自动化」里允许控制 iTerm2。

---

## 卸载

```bash
/path/to/cc-light/uninstall.sh
```

停止程序、关闭开机自启、移除本工具在 `~/.claude/settings.json` 里的 hooks、清掉状态文件与各 shell profile 里的
别名——你的其它配置原样保留。

---

## 致谢

灵感来自 Windows 版的 [claude-code-traffic-light](https://github.com/weilizhe8-del/claude-code-traffic-light)，
本项目把它的思路搬到 macOS：PowerShell 钩子换成纯 Python 钩子，tkinter 悬浮窗换成原生菜单栏。

## 许可

MIT
