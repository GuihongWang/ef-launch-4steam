<a id="gui"></a>
# 工具界面 endfield_gui.py

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 界面分区、每个按钮的行为、线程模型与状态刷新。返回 [目录](../README.md)。

工具 GUI = `endfield_gui.py` → 打包产物 `release\Steam终末地更新器.exe`。
单窗口 tkinter，**没有 tab**，用 `ttk.LabelFrame` 分区。

### 界面分区

| 分区 | 控件 | 干什么 |
|---|---|---|
| **当前状态** | 7 个标签 + 1 条黄底警告 | 显示 ACF 状态、`InstalledDepots`、Steam 安装目录、官服目录、shim / 联接状态、appinfo 解析结果、同步结论 |
| **操作区**（无边框按钮行） | **一键伪装（阻止更新）** / **恢复备份** / **刷新** | 见下 |
| **官服 → Steam：部署 shim（启动器接管）** | **自动检测 Steam 库** / **查找 Endfield.exe…** / 官服目录输入框 + **浏览…** / Steam库下拉框 + **自动** + **刷新** / **部署 shim** / **移除 shim** / **目录联接（已退役）** / **拆掉遗留联接** / **一键完整转换** / 启动项输入框 + **记录 DX 启动项** | 见下 |
| **手动覆盖（可选，一般不用）** | `manifest` / `buildid` / `SteamDB粘贴` 三个输入框 + **解析并填入** | 正常情况都用不到 |
| **日志** | 黑色等宽文本区 + 滚动条 | 工具自己的操作日志 |

### 每个按钮点了做什么

| 按钮 | 回调 | 副作用 |
|---|---|---|
| 一键伪装（阻止更新） | `on_apply()` | → `cmd_apply()`，**改写 ACF**（写之前自动备份） |
| 恢复备份 | `on_restore()` | 先弹确认框 → `cmd_restore()`，**用备份覆盖 ACF** |
| 刷新 | `refresh_status()` | 只读 |
| 自动检测 Steam 库 | `on_detect()` | 只读：扫描库、填下拉框 |
| 查找 Endfield.exe… | `on_find_exe()` | 弹目录选择框 → 只读查找 → 填「官服目录」输入框 |
| 浏览… | `on_browse_gamedir()` | 弹目录选择框 → 填输入框 |
| 自动 | `on_auto_library()` | `unpin_library()`，恢复自动选库 |
| 刷新（Steam库 行） | `on_detect()` | 同「自动检测 Steam 库」 |
| 部署 shim | `on_install_shim()` | 先弹确认框 → `core.shim_install()`，**往 Steam 安装目录写文件** |
| 移除 shim | `on_remove_shim()` | 先弹确认框 → `core.shim_remove()`，**删 Steam 安装目录里的 shim 文件** |
| 目录联接（已退役） | `on_make_link()` | **只弹说明框**，一个文件都不碰 |
| 拆掉遗留联接 | `on_del_link()` | 先弹确认框 → `remove_junction()`，只删链接本身 |
| 一键完整转换 | `on_convert()` | 先弹确认框 → `cmd_convert()`，**写 ACF + 部署 shim** |
| 记录 DX 启动项 | `on_launch_opts()` | `set_launch_options()` → **写一份备忘文件** + 弹提示框 |
| 解析并填入 | `on_parse_steamdb()` | `parse_steamdb_copy()` → 填 manifest / buildid 输入框 |

### 线程模型

- `run_async()` 把耗时工作丢进 **daemon 后台线程**，界面立刻解禁。
- worker 线程**不能直接碰 Tk**。所有 UI 更新通过 `post_ui()` 塞进 `queue.Queue`，
  主线程的 `_drain_ui()` 每 **40 ms** 轮询一次并执行。
- 原因（代码注释里写明了）：tkinter 的 `after()` 跨线程调用要求主线程正处于
  `mainloop()`，否则抛 `RuntimeError("main thread is not in main loop")`；
  一旦 worker 里抛了异常，`run_async()` 的 `except` 又会调 `log()` → 再抛 →
  `set_busy(False)` 永远执行不到，界面直接卡死、所有按钮变灰。
- 副作用：`core` 的 `print` 被 `_capture_stdout()` 接管（`_Tee` 类），全部导进日志框
  —— 打包成 `--windowed` 之后没有控制台，没有它就什么都看不到。
- `set_busy(True/False)` 会把 10 个按钮一起置灰/解禁。

### 状态刷新

`refresh_status()` 在三种时机触发：启动后 80 ms、点「刷新」、每个动作做完之后
（`self.post_ui(self.refresh_status)`）。它做的是**只读**检查：
`check_environment()` → 读 ACF → 量两个目录体积 → `scan_steam_shortcuts()`
→ `junction_info()` / `shim_installed_marker()` → `parse_appinfo()`，然后把结果
`post_ui` 回界面。

### 快捷方式陷阱警告

文案由纯函数 `shortcut_trap_warning_text(traps)` 生成（抽成纯函数是为了能不开窗口就测），
显示由 `show_shortcut_traps()` 控制：**只在有陷阱时** `pack` 出来，没有陷阱就
`pack_forget()` —— 检测不到就什么都不显示，不报错、不留痕迹。

检测由 `endfield_updater.py` 的 `scan_steam_shortcuts()` 完成，**全程只读**：
扫所有 Steam 库的 `userdata\*\config\shortcuts.vdf`，凡是 `Exe` 指向
`game_dir\Endfield.exe` 的都算陷阱。工具**不会**替你删那个快捷方式。

想自己看一眼（只读，不改任何东西）：

```powershell
> cd <仓库目录>
> python -X utf8 -c "import endfield_updater as c; print(c.scan_steam_shortcuts())"
```

本机当前的真实输出（说明这台机器上**确实存在**这个陷阱）：

```text
{'game_dir': 'C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game', 'traps': [{'name': 'Endfield', 'appid': '2422059311', 'exe': '"C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game\\Endfield.exe"', 'exe_path': 'C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game\\Endfield.exe', 'startdir': 'C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game\\', 'launch_options': '-launcher_lang=zh-cn  -launcher_sub_channel=1', 'vdf': 'C:\\Program Files (x86)\\Steam\\userdata\\975535778\\config\\shortcuts.vdf', 'user': '975535778'}], 'skipped': [], 'scanned': 1, 'formats': {'二进制': 1}}
```
