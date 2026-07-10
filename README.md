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

## 安装（一键）

```bash
cd ~/proj/cc-light
./install.sh
```

`install.sh` 会自动：装 rumps 依赖、把 hooks 幂等合并进 `~/.claude/settings.json`、生成并加载
一个 LaunchAgent（开机自启、崩溃自愈、装完立即启动）。装完菜单栏右上角就有圆点，之后再也不用手动
启动。改了代码后**再跑一次 `./install.sh`** 即可重启用上新代码。

> 分享给他人：把本目录拷过去 / clone，对方 `./install.sh` 即可（路径按其本机自动解析）。
> 新增或改动 hooks 后，需重启对应的 Claude Code 会话才生效。

## 日常操作

- 临时退出：点菜单栏圆点 →「退出 cc-light」（干净退出，不会被自愈拉起）。
- 退出后再启动 / 改代码后重启：`./install.sh`。
- 卸载：`./uninstall.sh`（停止、移除自启、清掉本工具的 hooks 与状态文件，保留你其它配置）。

## 菜单栏不显示图标怎么办

Anaconda 等非 framework 版 Python 跑菜单栏程序，有时图标不出现。装上 GUI 启动器再重装即可
（`install.sh` 会自动优先用 `pythonw`）：

```bash
conda install -y python.app
./install.sh
```
