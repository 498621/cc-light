# cc-light

Claude Code 的 Mac 菜单栏状态灯。灵感来自 Windows 版 [claude-code-traffic-light](https://github.com/weilizhe8-del/claude-code-traffic-light)，
把里面的 PowerShell 钩子换成纯 Python 钩子、tkinter 悬浮窗换成 macOS 菜单栏图标。

菜单栏右上角显示一个圆点，聚合所有活跃 Claude Code 会话的状态；点开下拉能看到每个会话
（按项目目录名区分）各自的状态。

## 状态含义

| 图标 | 状态 | 触发时机 |
| ---- | ---- | -------- |
| 🔴 | 等待确认 | Claude 需要你关注（请求工具权限 / 空闲等待输入的通知） |
| 🟡 | 运行中 | 你提交了提问、或正在执行工具 |
| 🟢 | 已完成 | 本轮回答结束（5 秒后自动熄为空闲） |
| ⚪ | 空闲 | 等待输入 |

多个会话时，菜单栏图标取优先级最高的：等待确认 > 运行中 > 已完成 > 空闲。

## 工作原理

- Claude Code 的 hooks（`~/.claude/settings.json`）在事件发生时调用 `hook.py`，把该会话状态
  原子写入 `~/.claude/cc-light/status/<session_id>.json`。
- `cc_light.py`（rumps 菜单栏程序）每 500ms 轮询这些文件，更新菜单栏图标与下拉列表。
- 钩子用系统 `/usr/bin/python3` 跑（只用标准库，与 Anaconda / PATH 解耦，绝不阻断 Claude Code）；
  菜单栏程序需要 rumps。

## 安装

```bash
# 1. 装依赖（装进你的 anaconda python）
pip3 install rumps

# 2. hooks 已写入 ~/.claude/settings.json（本仓库随附的 settings 片段仅供参考）。
#    改了 hooks 后需重启 Claude Code 会话才生效。

# 3. 启动菜单栏灯
cd ~/proj/cc-light
./start.sh          # 后台常驻，日志见 cc-light.log
./stop.sh           # 停止
```

## 菜单栏不显示图标怎么办

Anaconda 等非 framework 版 Python 跑菜单栏程序，有时图标不出现。解决办法：

```bash
# 安装 anaconda 的 GUI 启动器，得到 pythonw；start.sh 会自动优先用它
conda install -y python.app
./stop.sh && ./start.sh
```

## 开机自启（可选）

把 `com.cc-light.plist` 复制到 `~/Library/LaunchAgents/` 并 `launchctl load` 即可（模板里已注明
需要把路径改成本机绝对路径）。

## 卸载

- 停止：`./stop.sh`
- 移除 hooks：编辑 `~/.claude/settings.json` 删掉 `hooks` 段
- 删状态文件：`rm -rf ~/.claude/cc-light`
