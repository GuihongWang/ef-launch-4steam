<a id="shimflow"></a>
# Steam 库里的 shim 做了什么

> 本文是 [主 README](../README.md) 拆出的深入章节 —— shim 的运行时行为：分流优先级、命令行开关、环境变量、shim.log 行式。返回 [目录](../README.md)。

装在 Steam 库里的那个 `Endfield.exe` 就是 `shim/endfield_shim.py` 编译出来的。
下面是 `main()` 的**实际执行顺序**：

```text
base_dir()                     ① exe 所在目录（frozen 时用 sys.executable，非 frozen 用 __file__）
  │
  ├─ wants_hide_mode(argv)? ──── 是 → run_hider() → 退出          （--hide 分身，只藏窗口）
  │
  ├─ log("--- shim 启动 … ---")
  ├─ load_config(here)          ② 读 shim_config.json，坏值回落默认
  ├─ cleanup_stale_mei(cfg)     ③ 后台线程清 %TEMP% 的陈旧 _MEI*（不拖慢启动）
  │
  ├─ wants_launch_game_mode(argv)? ─ 是 → run_launcher_child() → 退出   （--launch-game，伪装出来的 Games.exe）
  │
  ├─ decide_route(cfg, argv)    ④ 分流：祖先链 → launch / config
  ├─ log_route(...)             ⑤ 把分流决策写进 shim.log
  │
  ├─ side == "config" ───────────→ try_open_tool_page() → 成功就退出（rc=0）
  │                              └→ 否则 open_config_page() → 内置 Win32 页
  │
  ├─ cfg["impersonate"]? ───────→ run_dispatcher() → 退出
  │
  ├─ launch_mode ∈ {auto, suspended_parent}?
  │     └→ run_suspended_parent() ── 成功 → 退出码 = 游戏的退出码
  │        └─ SuspendedParentError:
  │             launch_mode == suspended_parent → 弹错误框 → rc=2
  │             launch_mode == auto            → 继续走深链
  │        └─ 其它异常 → 弹错误框 → rc=2（**不回落**）
  │
  ├─ find_launcher(here)        ⑥ 仅诊断用
  ├─ launch(here)               ⑦ 深链（auto 的回落目标 / deep_link 的唯一路径）
  │     └─ 成功 → reload cfg → spawn_hider(cfg) → keep_shim_until_game 时 wait_for_game(cfg)
  └─ 退出码 = launch() 的返回值
```

### 分流优先级

`decide_route()` 的实际顺序（**从高到低**）：

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | 命令行 `--force-config` | 配置主页 |
| 2 | 命令行 `--force-launch` | 启动游戏 |
| 3 | `launch_source = always_launch` | 启动游戏 |
| 4 | `launch_source = always_config` | 配置主页 |
| 5 | 祖先链上**任意一级**命中白名单 | 启动游戏 |
| 6 | 认不出直接父进程（`ParentInfo.ok` 为假） | 配置主页（安全默认） |
| 7 | 其余 | 配置主页 |

`--force-config` 的判断在 `--force-launch` **之前** —— 两个都给时 `--force-config` 赢。
无论走哪条分支，`decide_route()` 都会**先认一次祖先链**，保证 `shim.log` 里
永远有「我是被谁拉起来的」这条证据。

### 命令行开关

shim 自己的开关**必须精确匹配**（`str(arg).strip().lower()` 后全等），
未知参数一律忽略 —— Steam 可能带一堆自己的启动参数过来，猜错一次就会在
完全不该启动的时候启动游戏。

| 开关 | 精确语义 |
|---|---|
| `--force-config` | 强制走「配置主页」那一侧 |
| `--force-launch` | 强制走「启动游戏」那一侧 |
| `--hide` | 进入**藏窗口分身**模式（`run_hider()`），只藏窗口，绝不碰启动链路 |
| `--launch-game` | 进入**伪装出来的 Games.exe** 模式（`run_launcher_child()`），只 fork 游戏，绝不碰启动器 |

### 环境变量

| 变量 | 精确语义 |
|---|---|
| `ZMD_DEEP_LINK_SCHEME` | `deep_link_override()` 认它。设了它，`find_deep_link_scheme()` **直接返回它、注册表一个字节都不读**；`launch()` **整条变成 no-op** —— 一个 `ShellExecuteW` 都不发，也不去定位 `Launcher.exe`，直接当成功返回 |
| `ZMD_SHIM_KEEP_CONSOLE` | 设为 `1` 时 `hide_own_console()` 不藏自己的控制台窗口 |

> ⚠ **`ZMD_DEEP_LINK_SCHEME` 只管深链那一段**。`main()` 里挂起父进程方案是在
> `launch()` **之前**试的，所以设了这个变量，**游戏照样会由挂起父进程拉起来**。
> 它不是「启动侧的总开关」，只是「深链腿」的 no-op 开关。

> ⚠ **`ZMD_SHIM_KEEP_CONSOLE` 在生产环境其实是空转的**。`hide_own_console()` 只在
> `run_suspended_parent()` 里被调用（而且是在游戏进程建好之后），它取的 API 是
> `user32.GetConsoleWindow` —— 这个函数其实由 **kernel32** 导出，取不到就抛
> `AttributeError`，被捕获后记一行 `[降级] 隐藏控制台窗口失败: function
> 'GetConsoleWindow' not found`。本机 shim.log 里每一次运行都有这一行。
> 加上 shim 是用 `--windowed` 打的（本来就没有控制台），这个开关没有任何实际作用。

### 内置 Win32 配置页

`config_page` 不走工具 GUI 时，落到这个**纯 Win32 自绘**窗口（`_ConfigUi`）。
控件全部用 Win32 **内置类**（STATIC / EDIT / COMBOBOX / BUTTON），窗口类自建
（`CONFIG_WINDOW_CLASS = "ZmdShimConfigWindow"`）—— 内置类的窗口过程是系统给的，
收不到 `WM_COMMAND`，按钮点了没反应。

| 控件 | 改哪个配置键 |
|---|---|
| 游戏目录 game_dir | `game_dir` |
| 启动器 launcher | `launcher` |
| 深链 scheme | `scheme` |
| 启动方式 launch_mode（下拉） | `launch_mode` |
| 分流方式 launch_source（下拉） | `launch_source` |
| 父进程白名单 | `parent_whitelist`（按 `,` `;` 空白切） |
| 祖先链深度 | `parent_match_depth`（不是正整数就**沿用原值**） |
| 完整工具路径 | `tool_path`（同时同步一份到老字段 `tool_exe`） |

四个按钮：**保存**（`do_save()`，保留不认识的旧字段）、**测试启动**
（`do_test_launch()`：先保存，再 `Popen` 自己并带 `--force-launch`）、
**打开完整工具**（`do_open_tool()`）、**关闭**。

`config_page` 的三种取值（`config_page_mode()`）：`tool`（默认）/ `auto`（语义同 tool）/
`builtin`（强制内置页）。坏值一律当 `tool`。

### 藏窗口那套（实验性，默认关）

`hide_launcher` 默认 `false`，**别开**。代码注释里记着两次实测失败
（2026-10-17 19:41 / 19:49：`Games.exe` 的 `DoMainBtnClicked` / `cgsu create game process`
一次都没出现，游戏进程压根没被创建）。

流程（`_hide_loop()`）：三段式 —— 观察期（`hide_settle_ms`）只看不藏 →
藏窗口期（最多 `hide_ms`）→ 宽限期（`hide_grace_ms`）内游戏没出现就**还原窗口**。
轮询节奏：前 10 秒 50 ms 一次，之后 200 ms；进程重扫 0.5 s 一次。
`hide_taskbar` 为真时额外改 `EXSTYLE`，加 `WS_EX_TOOLWINDOW`、清 `WS_EX_APPWINDOW`
（两者都设时前者会被系统忽略，所以必须一起改）。还原时按位恢复 `EXSTYLE`，
再 `SW_RESTORE` + `SW_SHOW`。

`spawn_hider()` 只在 `hide_launcher` 为真、且**是打包后的 exe**（`frozen`）时才派生
（源码直跑不派生 —— 那会立刻开始藏这台机器上真实的启动器窗口）。
`wait_for_game()` 是 `keep_shim_until_game` 的实现：最多等 `hide_ms` 秒，
每 250 ms 扫一次进程。

### Games.exe 伪装（实验性，默认关）

`impersonate` 默认 `false`，而且**只在命令行显式给了 `--impersonate` 时**才写进配置。

链路：`run_dispatcher()` 把自身复制成 `<Steam安装目录>\Games.exe`
（`impersonate_staged()`，先写 `.tmp` 再 `os.replace`），再用 `--launch-game` 拉起它；
那个副本（`run_launcher_child()`）再去 fork 官服目录里的真游戏。

两道硬闸：`_assert_writable()` 拦截任何落在
`PROTECTED_DIR_NAMES`（`hypergryph launcher` / `gryphlink`）里的路径；
`_same_dir()` 拦截「`game_dir` 就是 shim 自己所在目录」这种会无限 fork 的配置。

> 判定实验的脚本与报告已随清理删除，`endfield_updater.py` 的
> `lab_impersonate_verdict()` **恒返回 `None`** —— 是「没有结论」，不是「结论是 FAIL」。
> 结论出来之前别开：没过就是 `errorCode 10100`。

### `_MEI*` 残留清理

`cleanup_stale_mei()` 在**分流判断之前**、**后台线程**里做（不能拖慢 Steam 拉起这条链）。

判定刻意保守，`purge_stale_mei()` 的三道闸缺一不可：

| 闸 | 条件 | 不满足时 |
|---|---|---|
| 陈旧 | 名字以 `_MEI` 开头 + 含 PyInstaller 标志物（`python3` / `base_library.zip` / `.pyz`）+ mtime 超过门槛（默认 1 小时） | 不列出来 |
| 占用 | `_dir_is_deletable()`：映像探针（`.dll/.pyd/.exe` 以 `O_RDWR` 打开失败 = 那个进程还活着）+ 删除权探针（`CreateFileW(DELETE)` 拿不到 = 别人以拒绝删除的共享模式握着） | **整个目录一个字节都不删** |
| 隔离 | 改名成 `_ZMDMEITRASH_<原名>_<pid>` 再整棵删；删不掉就**改回原名** | 留下并记日志 |

本进程自己的 `_MEIPASS` 永远排除在外。上一轮改名后被硬杀留下的**陈旧隔离区**
（`_ZMDMEITRASH_*`）也会被 `scan_stale_mei()` 一起列出来回收 —— 它带本工具的专属
前缀，只可能是本工具自己建的，不需要标志物判定。

### shim.log 里会出现哪些行

`log()` 往 `base_dir() / "shim.log"` 追加，格式是 `[YYYY-MM-DD HH:MM:SS] <消息>`，
带 `_LOG_LOCK` 串行化。调用点收齐如下（按用途分组）：

- 启动：`--- shim 启动 pid=… (frozen=…) 目录=… ---`、`--- shim 退出 rc=… ---`
- 配置降级：`[降级] …`（`game_args` 类型不对、白名单类型不对、深度不是正整数、
  `config_page` 认不出、`tool_path` 类型不对、配置不是对象、读配置失败）
- 清理：`[清理] 已在后台启动 _MEI* 残留清理（门槛 … 小时）`、`[清理] _MEI* 残留清理完成：扫描 … 删除 … 释放 … 跳过(还在用) … 失败 … 耗时 … ms`、`[清理] 已删除陈旧残留: …`、`[清理] 跳过（还在用，删不掉）: …`
- 分流：`[分流] 跳过同 exe 的引导进程实例: …`、`[分流] 祖先链 = …`、
  `[分流] 祖先链遍历到此为止: …`、`[分流] 父进程 pid=… 映像=… 文件名=… 命中层级=… 白名单=… 匹配=… → 走「…」侧（…）`
- 配置侧：`走配置主页这一侧：不启动游戏、不调深链、不建任何进程`、
  `[配置侧] 已拉起工具 GUI: …`、`[配置侧] 工具 GUI 已打开，shim 随即退出`、
  `[配置侧] 找不到工具 GUI，这里是内置简易配置页（…）`、`[工具GUI] …`、`[配置] 已把 tool_path 写回 …`、`[配置] 已保存 …`
- 启动链路：`launch_mode=…`、`官方 Games.exe: …（…）`、`已锁住 …（只读共享）…`、
  `CreateProcessW(Games.exe, CREATE_SUSPENDED) cwd=…`、`挂起的 Games.exe 已创建 pid=…（本进程**永不恢复**它）`、
  `CreateProcessW(游戏, 父进程=挂起的 Games.exe) …`、`游戏进程 pid=… 的直接父进程 = … ← 挂起的 Games.exe ✓（SDK 要的就是这一条）`、
  `游戏进程 pid=… 退出，退出码=…`、`挂起父进程方案启动完成，游戏退出码 rc=…（透传给 Steam）`
- 失败与回滚：`[挂起父进程失败] …`、`[回滚] 启动失败，已终止半途建出来的游戏进程…`、
  `[降级] 终止半途建出来的游戏进程失败: …`、`[注意] 等游戏退出超时（…ms），终止它`、
  `[注意] 打不开游戏进程 pid=…（可能已经退出），按退出码 3 处理`
- 深链：`协议来源: … scheme=…`、`用了深链: …`、`ShellExecuteW 成功 (hinst=…)`、
  `兜底执行: "…" --game=endfield`、`[深链] 覆盖生效（…）`
- 藏窗口：`观察期 …ms（不碰窗口）→ 藏窗口至多 …ms → 启动宽限期 …ms`、
  `启动器已开着的窗口，本次不动: …`、`隐藏了 hwnd=… 标题=…`、`游戏进程已出现，停止轮询 (pid=…)`、
  `启动失败：…ms 宽限期内没等到游戏进程，还原启动器窗口`、`已还原 … 个启动器窗口…`
- 其它：`[降级] 隐藏控制台窗口失败: …`、`ZMD_SHIM_KEEP_CONSOLE=1，不藏自己的控制台窗口`
