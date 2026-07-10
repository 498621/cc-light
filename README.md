# cc-light

Claude Code 的 Mac 菜单栏状态灯。灵感来自 Windows 版 [claude-code-traffic-light](https://github.com/weilizhe8-del/claude-code-traffic-light)，
把里面的 PowerShell 钩子换成纯 Python 钩子、tkinter 悬浮窗换成 macOS 菜单栏图标。

菜单栏右上角显示一个圆点，聚合所有活跃 Claude Code 会话的状态；点开下拉能看到每个会话
（按项目目录名区分）各自的状态。

## 状态含义（三态）

| 图标 | 状态 | 触发时机 |
| ---- | ---- | -------- |
| 🟢 | 工作中 | 你提交了提问、或正在执行工具 |
| 🔴 | 等待中 | Claude 需要你关注（请求工具权限 / 向你提问） |
| ⚪ | 空闲 | 等待输入、或本轮回答结束 |

空闲 60 秒的系统提醒会按空闲(灰)处理，不误判成红。

## 菜单栏两种显示模式

下拉里有开关「菜单栏分状态计数（不勾选则合并为一个灯）」，选择会被记住：

- 合并为一个灯（默认）：按优先级取最高，只显示一个圆点。红 > 绿 > 灰。
- 分状态计数：如 `🔴2 🟢1 ⚪3`，一眼看清几个等待、几个在跑、几个空闲。

点开下拉能看到每个会话（按项目目录名区分）各自的状态；点击某个会话可跳回它所在的 iTerm2 tab
（首次点击 macOS 会弹自动化授权，允许控制 iTerm2 即可）。

## 工作原理

- Claude Code 的 hooks（`~/.claude/settings.json`）在事件发生时调用 `hook.py`，把该会话状态
  原子写入 `~/.claude/cc-light/status/<session_id>.json`。
- `cc_light.py`（rumps 菜单栏程序）每 250ms 轮询这些文件，更新菜单栏图标与下拉列表。
- 钩子用系统 `/usr/bin/python3` 跑（只用标准库，与 Anaconda / PATH 解耦，绝不阻断 Claude Code）；
  菜单栏程序需要 rumps。

## 安装 / 启动

首次运行（用完整路径跑一次）：

```bash
~/proj/cc-light/start.sh
```

`start.sh` 会自动：装 rumps 依赖、把 hooks 幂等合并进 `~/.claude/settings.json`、把
`alias cc-light="…/start.sh"` 写进你的 shell profile，然后启动菜单栏灯。之后**新开终端直接输入
`cc-light` 即可启动**（不用再进目录）。

> 分享给他人：把本目录拷过去 / clone，对方跑一次 `start.sh` 即可（路径按其本机自动解析）。
> 新增或改动 hooks 后，需重启对应的 Claude Code 会话才生效。

## 日常操作

- 启动：`cc-light`（首次装好 alias 后，任意终端可用）。
- 退出：点菜单栏圆点 →「退出 cc-light」。
- 开机自启：点菜单栏圆点 → 勾选「开机自启动」（下次登录生效；取消勾选即关闭，不影响当前正在跑的灯）。
- 卸载：`./uninstall.sh`（停止、关自启、清 hooks / 状态 / profile 里的 alias，保留你其它配置）。

单实例锁保证同时只有一个灯：手动启动与开机自启不会叠成两个图标。

## 菜单栏不显示图标怎么办

Anaconda 等非 framework 版 Python 跑菜单栏程序，有时图标不出现。装上 GUI 启动器再重启即可
（`start.sh` 会自动优先用 `pythonw`）：

```bash
conda install -y python.app
cc-light
```
