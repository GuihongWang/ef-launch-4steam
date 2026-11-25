<a id="build"></a>
# 构建

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 构建详情、代码结构地图、文件清单与维护者须知。返回 [目录](../README.md)。

```powershell
> cd <仓库目录>
> python -X utf8 build_shim.py             # shim → shim_build\dist\Endfield\Endfield.exe（默认 onefile）
> python -X utf8 build_exe.py              # 工具 → release\Steam终末地更新器.exe
```

### build_shim.py

```powershell
> python -X utf8 build_shim.py --help
usage: build_shim.py [-h] [--onefile] [--onedir] [--clean]
```

完整 PyInstaller 命令行（`build_shim.py` 的 `main()`）：

```text
python -m PyInstaller
  --noconfirm --clean
  [--onefile]                      # 默认 onefile；--onedir 时不给
  --windowed
  --name Endfield
  --manifest shim\shim.manifest
  --uac-admin                      # 必须：否则 PyInstaller 会把 requireAdministrator 覆盖成 asInvoker
  --distpath shim_build\dist
  --workpath shim_build\build
  --specpath shim_build
  --paths <仓库根>
  shim\endfield_shim.py
```

- `--onefile` / `--onedir` **不能同时给**（给了返回 `1`）。**默认 onefile**。
- `normalize_output()` 把产物统一挪到 `shim_build\dist\Endfield\Endfield.exe`
  （onedir 时 PyInstaller 直接产在那儿；onefile 时在 `shim_build\dist\Endfield.exe`，
  要挪进去）。切到 onefile 时还会把 onedir 遗留的 `_internal\` 和多余文件**清掉**。
- 构建后校验：读产物字节流，确认里面**有** `requestedExecutionLevel level="requireAdministrator"`；
  没有就返回 `1`（整条链会在 `Endfield.exe` 那步 WinError 740）。
- `ensure_pyinstaller()` 优先复用系统 Python 里已装的 PyInstaller，没有才建
  `.venv-build` 虚拟环境并 `pip install pyinstaller`。

### build_exe.py

```powershell
> python -X utf8 build_exe.py --help
usage: build_exe.py [-h] [--onedir] [--dist DIST] [--python PYTHON]
                    [--no-embed-shim]
```

完整 PyInstaller 命令行（`build_exe.py` 的 `build()`）：

```text
python -m PyInstaller
  --noconfirm --clean
  [--onefile]                      # 默认 onefile；--onedir 时不给
  --windowed
  --name Steam终末地更新器
  --distpath <dist根>\release      # **唯一权威位置就是 release\**
  --workpath <dist根>\build
  --specpath <dist根>
  --paths <dist根>
  --hidden-import tkinter
  --hidden-import tkinter.ttk
  --hidden-import tkinter.messagebox
  --add-data <仓库根>\shim_build\dist\Endfield;shim_build/dist/Endfield   # 把 shim 打进包里（分隔符是 os.pathsep）
  <dist根>\endfield_gui.py
```

- `--add-data` 用 `os.pathsep` 分隔源和目标。目标路径必须与
  `endfield_updater.SHIM_SRC_REL` 一致 —— frozen 模式下
  `SHIM_SRC_DIR = sys._MEIPASS / shim_build/dist/Endfield`。
- `shim_data_args()` **只接受 onefile 形态的 shim**：负载里只要有 `_internal\` 的任何文件
  就 `raise SystemExit` 拒绝打包（否则用户点一次「部署 shim」就会把 19 MB 的
  `_internal\` 重新塞回 Steam 库，把刚做好的 onefile 部署推翻）。
- `--no-embed-shim` 不打 shim 进去 —— 工具 exe 就失去「部署 shim」能力。
- `check_temp_space()` 在构建前检查 TEMP 所在磁盘的可用空间，不足 120 MB 会警告
  （可以改用 `--onedir`）。
- 产物**直接**落在 `release\`，不再留 `dist\` 那份拷贝。

> ⚠ **二进制和源码必须配套**：那两个 `.exe` 都是**产物**，不是源码。
> 改了 `endfield_gui.py` / `endfield_updater.py` / `shim\endfield_shim.py` 之后
> **必须**重跑 `build_shim.py` + `build_exe.py`，否则用户双击拿到的还是旧版。
> `zz_final.py` 专门检查这一条。

本机当前产物的实测值（2026-11-25）：

| 产物 | 字节数 | md5 |
|---|---|---|
| `shim_build\dist\Endfield\Endfield.exe` | 9,680,294 | `7efcca8a193880a1811f2e9e4ebe482b` |
| `release\Steam终末地更新器.exe` | 22,323,843 | `85b4dea7b4ce3139e73d3a013a751be4` |

（部署到 Steam 安装目录里的那份，md5 与构建源**逐字节相同** —— `shim status` 里的
「完整性 ✓」就是比这个。）

### zz_final.py 部署产物体检

```powershell
> python -X utf8 zz_final.py
```

查三件事：

1. **三者逐字节相同**：构建源 `shim_build\dist\Endfield\Endfield.exe`
   == 部署品 `<Steam库>\Arknights Endfield\Endfield.exe`
   == 工具内嵌负载 `release\Steam终末地更新器.exe` 的 CArchive 里那份
2. **内嵌负载是 onefile**：CArchive 里 `shim_build/dist/Endfield/` 下**恰好 1 个条目**
   （就是 `Endfield.exe`，不含任何 `_internal` 条目）
3. **工具 exe 不比源码旧**：`endfield_gui.py` / `endfield_updater.py` /
   `shim/endfield_shim.py` 的 mtime 不能比工具 exe 新

> ⚠ 它**顺带删掉部署目录里的 `shim.log`**。跑之前想留证据就先复制一份。

### 运行时残留

仓库里**一个残留都没有**。下面这些是**跑一次源码版**就会重新长出来的东西：

| 路径 | 什么时候长出来 | 说明 |
|---|---|---|
| `shim\shim.log` | 直接跑 `shim\endfield_shim.py` 时 | 已部署的那份在 Steam 安装目录里 |
| `shim\shim_config.json` | 直接跑 `shim\endfield_shim.py` 时 | shim 会把探测到的 `tool_path` 写回配置文件 |
| `__pycache__\` | 跑一次 `python xxx.py` | Python 编译缓存 |
| `build\`、`dist\`、`*.spec` | `python build_exe.py` | PyInstaller 构建中间物 |
| `shim_build\build\`、`shim_build\Endfield.spec` | `python build_shim.py` | shim 的构建中间物 |

> ⚠ `.gitignore` 已经挡住了 `__pycache_\` 和 `shim\shim.log`，但
> **`shim\shim_config.json` 没被挡住**。`git add -A` 之前先 `git status --short` 看一眼。

### .gitignore 怎么用

> **这个目录目前还不是 git 仓库。** `git init` 是**你的决定**，工具不替你做。
> 想开始用版本管理，自己执行：
>
> ```powershell
> cd <仓库目录>
> git init
> git add -A
> git status --short      # 先看清楚 git 到底要收哪些文件
> ```
>
> `.gitignore` 已经把「可再生的产物」全排除了，`git add -A` 之后仓库里应该只剩
> **生产源码 + 文档**。

| 类别 | 忽略了什么 | 为什么 |
|---|---|---|
| Python | `__pycache__/`、`*.py[cod]`、`*.egg-info/`、`.venv/`、`venv/`、`.venv-build/` | 编译缓存 / 本机虚拟环境 |
| PyInstaller | `build/`、`dist/`、`*.spec`、`shim_build/build/`、`shim_build/dist/` | 纯构建中间物，可重建 |
| 生产二进制 | `release/*.exe`、`release/*.exe.bak_*` | 是产物不是源码，可重建 |
| 运行时 / 证据 | `*.log`、`shim/shim.log`、`*.bak_*`、`*.acf.bak`、`*.acf.pre_apply_*`、`gui_*_result.txt` | 跑一次就变的东西 |
| 二进制备份 | `stub_backup/` | 官服游戏本体的独立副本 + 官方完整性基线 |
| 编辑器 / 系统 | `.vscode/`、`.idea/`、`Thumbs.db`、`desktop.ini`、`.DS_Store` | 环境噪音 |

> ⚠ **被忽略 ≠ 可以删**。`shim_build/dist/`、`release/*.exe`、`stub_backup/`
> 三样**必须在本机存在**才能工作：前两样删了要重新构建，
> `stub_backup/` 删了**再也拿不回来**。

---

<a id="map"></a>
## 代码结构地图

### endfield_updater.py 主程序

| 模块 | 关键函数 / 常量 |
|---|---|
| 游戏常量 | `APP_ID`、`DEPOT_ID`、`GAME_NAME`、`GAME_EXE_NAME`、`LAUNCH_DX11`、`OFFICIAL_DIR_HINTS`、`SKIP_DIR_NAMES` |
| 内置快照 | `SNAPSHOT_DATE`、`FALLBACK_BUILDID`、`FALLBACK_MANIFEST`、`FALLBACK_DEPOT_SIZE` |
| 库探测 | `find_steam_roots()`、`_scan_drive_steamlibs()`、`_parse_libraryfolders()`、`discover_steam_libraries()`、`pin_library()` / `unpin_library()` / `_apply_library()`、`resolve_game_paths()`、`detect_official_dir()`、`find_endfield_exe()`、`steam_link_targets()` |
| ACF | `read_acf()`、`write_acf()`、`build_acf_template()`、`create_acf()`、`cmd_apply()`、`cmd_restore()`、`_resolve_manifest_buildid()`、`_size_source_dir()` |
| appinfo | `parse_appinfo()`、`extract_manifest_info()`、`extract_launch_options()`、`_read_bytes_retry()` |
| SteamDB | `parse_steamdb_copy()` |
| 环境 | `is_admin()`、`check_environment()`、`folder_size()`、`junction_info()` / `_is_reparse_point()` / `_read_link_target()` |
| 联接（退役） | `JUNCTION_RETIRED_EXIT_CODE`、`JUNCTION_RETIRED_NOTICE`、`create_junction()`、`remove_junction()` |
| shim 部署 | `shim_install()`、`shim_remove()`、`shim_status()`、`_shim_installed_files()`、`_partition_install_dir()`、`_is_empty_dir()`、`_is_drive_root()`、`_missing_payload_files()`、`clear_internal_dir()`、`_internal_foreign_entries()`、`_is_pyi_runtime_file()`、`pyi_archive_payload_names()`、`_exe_matches()`、`_exe_requires_admin()`、`resolve_shim_src_dir()` / `shim_src_candidates()` / `_default_shim_src_dir()`、`find_tool_exe()` / `_tool_release_dir()`、`find_official_launcher()` / `_launcher_root_from_game_dir()`、`find_deep_link_scheme()`、`lab_impersonate_verdict()`、`_match_depth_of()`、`_describe_launch_mode()` / `_describe_launch_source()` |
| 指纹 | `dir_tree_fingerprint()`、`diff_dir_tree_fingerprint()`、`_md5_of_file()`、`_is_link_like()`、`_link_target_text()`、`FingerprintError` |
| 快捷方式 | `scan_steam_shortcuts()`、`parse_shortcuts_vdf()`、`parse_keyvalues_binary()` / `_kv_binary_block()`、`parse_keyvalues()` / `_kv_tokenize()`、`_norm_win_path()`、`_steam_shortcut_dirs()`、`load_shim_config()` |
| CLI | `build_parser()`、`main()`、`cmd_status()`、`cmd_check()`、`cmd_fetch()`、`cmd_shim()`、`cmd_convert()`、`set_launch_options()` |

### shim/endfield_shim.py 生产 shim

| 模块 | 关键函数 / 常量 |
|---|---|
| 入口 | `main()`、`base_dir()`、`log()` |
| 配置 | `load_config()`、`_as_bool()`、`DEFAULT_CONFIG` |
| 残留清理 | `cleanup_stale_mei()`、`purge_stale_mei()`、`scan_stale_mei()`、`_dir_is_deletable()`、`_is_mapped_image()`、`_can_take_delete_access()`、`_looks_like_mei_dir()`、`_self_meipass()` |
| Win32 层 | `_win32()`、`_pwin32()`、`_process_path()`、`_scan()`、`_is_games_exe()`、`_top_windows()`、`_hide_window()`、`_restore_windows()`、`hide_own_console()` |
| 深链 | `launch()`、`find_deep_link_scheme()`、`deep_link_override()`、`find_launcher()`、`_shell_open()` |
| 藏窗口 | `spawn_hider()`、`run_hider()`、`hide_launcher_windows()`、`_hide_loop()`、`wait_for_game()`、`wants_hide_mode()` |
| 伪装 | `impersonate_staged()`、`run_dispatcher()`、`run_launcher_child()`、`_assert_writable()`、`_same_dir()`、`game_command_line()`、`_create_process_wait()`、`wants_launch_game_mode()` |
| 祖先链 | `decide_route()`、`identify_parent()`、`collect_ancestor_chain()`、`find_whitelist_hit()`、`match_depth()`、`whitelist_names()`、`parent_name_allowed()`、`wants_force_config()` / `wants_force_launch()`、`log_route()`、`AncestorNode` / `ParentInfo`、`_read_proc_state()` / `_process_birth_time()` / `_self_birth_time()` / `_filetime_to_unix()` / `_same_image()` / `read_parent_pid()` |
| 配置页 | `try_open_tool_page()`、`open_config_page()`、`config_page_mode()`、`launch_tool_gui()`、`resolve_tool_path()` / `search_tool_exe()` / `_tool_candidates_in()`、`tool_gui_running()`、`save_config_value()`、`_ConfigUi` / `_config_wndproc()` / `open_config_page()` |
| 挂起父进程 | `run_suspended_parent()`、`SuspendedParent`、`_create_suspended_parent()`、`_create_child_with_parent()`、`_open_image_read_lock()`、`find_official_games_exe()` / `_games_exe_under()` / `version_sort_key()`、`resolve_game_args()`、`_wait_game_exit()`、`_install_parent_guard()` / `_terminate_all_parents()` |

### endfield_gui.py 工具 GUI

| 模块 | 关键成员 |
|---|---|
| 入口 | `main()`、`App`、`APP_TITLE`、`APP_VER` |
| 跨线程 | `run_async()`、`post_ui()`、`_drain_ui()`、`_capture_stdout()` |
| 界面 | `_build_ui()`、`_build_style()`、`set_busy()`、`set_sync()`、`log()` |
| 状态 | `refresh_status()`、`show_shortcut_traps()`、`shortcut_trap_warning_text()` |
| 动作 | `on_apply()`、`on_restore()`、`on_parse_steamdb()`、`on_detect()`、`on_find_exe()`、`on_auto_library()`、`on_target_selected()`、`on_browse_gamedir()`、`on_install_shim()`、`on_remove_shim()`、`on_make_link()`、`on_del_link()`、`on_convert()`、`on_launch_opts()`、`on_close()` |
| 辅助 | `_ns()`、`collect_manual()` |

### 构建脚本与小工具

| 文件 | 作用 | 会写东西吗 |
|---|---|---|
| `build_shim.py` | 构建 Steam 侧 shim | 只写 `shim_build\` |
| `build_exe.py` | 构建工具 exe | 只写 `release\`、`build\`、`*.spec` |
| `zz_final.py` | 部署产物体检（三者比对 / onefile 检查 / 新鲜度） | **会删部署目录里的 `shim.log`** |
| `cleanup_mei.py` | 清理 `%TEMP%` 下 `_MEI*` 残留 | 删 `%TEMP%` 里的 `_MEI*`（24 小时内、没被占用的） |
| `cleanup_proc.py` | 清理本项目留下的进程 | **会 kill 进程**（只碰 `release\` 下的 exe，按 exe 完整路径匹配，不按进程名） |
| `check_gamedir.py` | 检查官服目录布局、比对游戏本体 md5 与 `stub_backup` | 只读 |
| `diag_noresponse.py` | 「拉起来没反应」的排障汇总 | 只读 |
| `run_shim_live.py` | 拉起**真** shim 并盯进程树 + `shim.log` | ⚠ **会真的启动游戏**，仅开发调试用 |

---

<a id="files"></a>
## 文件清单

`<仓库目录>` 下共 **23 个文件**（本文档之外合计 33,326,488 字节 ≈ 31.78 MiB，
其中两个 exe 就占 30.5 MiB）+ **6 个目录**（不计 `__pycache__\`）。
其中 `shim_build\dist\Endfield\shim.log` 和 `shim_build\dist\Endfield\shim_config.json`
是跑源码版 shim 时长出来的**运行时残留**，不算仓库内容。

> ⚠ 本清单统计于 2026-10-22。此后目录新增了 `docs/`（本文档所在，8 个拆分章节）与 `ww/`
> （鸣潮版子项目，自有独立仓库），并清理了 `__pycache__\`；下表字节数以实际为准。

| 路径 | 字节 | 说明 |
|---|---|---|
| `endfield_updater.py` | 158,881 | 主程序（CLI）：ACF 读写、Steam 库探测、appinfo 解析、shim 部署/移除、快捷方式陷阱检测 |
| `endfield_gui.py` | 35,737 | 工具 GUI（Tk），LabelFrame 分区 |
| `shim/endfield_shim.py` | 181,631 | **生产 shim 源码** —— 装到 Steam 库里的就是它 |
| `shim/shim.manifest` | 1,784 | 提权清单（`requireAdministrator`）+ DPI / 长路径声明 |
| `build_shim.py` | 7,313 | 构建 Steam 侧 shim → `shim_build/dist/Endfield/Endfield.exe` |
| `build_exe.py` | 13,232 | 构建工具 exe → `release/Steam终末地更新器.exe`（含内嵌 shim 负载） |
| `release/Steam终末地更新器.exe` | 22,323,843 | **生产工具 GUI 二进制** —— `tool_path` 指向它 |
| `shim_build/dist/Endfield/Endfield.exe` | 9,680,294 | **生产 shim 二进制** —— `shim install` 的**唯一来源** |
| `shim_build/dist/Endfield/shim.log` | 1,877 | 运行时残留：源码直跑 shim 时写的日志 |
| `shim_build/dist/Endfield/shim_config.json` | 79 | 运行时残留：源码直跑 shim 时写的配置 |
| `endfield_updater.bat` | 772 | 命令行包装（纯 ASCII，适配中文 Windows 的 GBK 码页） |
| `endfield_updater.ps1` | 901 | 命令行包装（纯 ASCII，支持 `$env:ENDFIELD_PYTHON`） |
| `zz_final.py` | 7,315 | **部署产物体检**：构建源 / 部署品 / 工具内嵌负载三者逐字节比对 + 内嵌负载是不是 onefile + 工具 exe 比源码新不新。⚠ 跑完会**删掉部署目录里的 `shim.log`** |
| `cleanup_mei.py` | 1,811 | 清理 `%TEMP%` 下 onefile 留下的 `_MEI*` 残留 |
| `cleanup_proc.py` | 2,983 | 清理本项目留下的进程（**会 kill 进程**） |
| `check_gamedir.py` | 2,347 | 检查官服目录布局，比对游戏本体 md5 与 `stub_backup` 里的备份 |
| `diag_noresponse.py` | 3,654 | 「拉起来没反应」的排障：只读汇总 shim.log / Steam 日志 / ACF / SDK 日志 |
| `run_shim_live.py` | 3,304 | 拉起**真** shim 并盯进程树 + `shim.log`。⚠ **会真的启动游戏** |
| `stub_backup/Endfield.exe.real` | 827,960 | **官服游戏本体的唯一独立副本**（md5 `78e282a7431e38ebcd15e64c868c11be`） |
| `stub_backup/verify_files.json.orig` | 70,860 | 官方 `verify_files.json` 基线（对照「现在这份被改过什么」，md5 `f650aedfc4e8126fe11427d8b0e4b077`） |
| `stub_backup/real_exe.json` | 110 | 真实 exe 的元数据 |
| `.gitignore` | 3,084 | 把可再生的产物全排除了 |
| `README.md` | — | **主文档**（快速开始 / 机制 / 排障速查 / 已知限制） |
| `docs/*.md` | — | 从主 README 拆出的 8 个深入章节 |

> **为什么留 `stub_backup/`**：官服目录里那份 `Endfield.exe` 是**启动器管的** ——
> 启动器会比对 `verify_files.json` 的 md5/size，改了就被判成「文件损坏」→
> 提示重新下载几百 MB。想在本机留一份「未经任何改动的原始本体」做对照，**只有这里**。
> **删了就再也拿不回来**，所以它在 `.gitignore` 里（不进版本库），但**本机这份别删**。

---

<a id="maintainer"></a>
## 维护者须知

### 两个目录别搞混

| 概念 | 本机实际值 | 作用 |
|---|---|---|
| **Steam 安装目录** | `C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield` | 放 shim 的地方（**只放 shim，约 9.2 MiB**） |
| **官服目录** | `C:\Program Files\Hypergryph Launcher\games\Endfield Game` | 游戏本体，**工具全程只读不写** |

这两个目录混为一谈是很危险的（联接时代的部署逻辑会去改名你的官服本体）。
`check` 和 `shim status` 会把两者分开显示。

### 游戏常量

全部定义在 `endfield_updater.py` 顶部，改常量即可：

| 项 | 值 |
|---|---|
| App ID | `4732690` |
| Depot ID | `4732691`（终末地只有一个 depot） |
| Steam installdir | `Arknights Endfield` |
| 主程序 | `Endfield.exe` |
| 官服安装器 | Hypergryph 启动器（`Launcher.exe`） |
| 官服游戏目录 | `C:\Program Files\Hypergryph Launcher\games\Endfield Game` |
| DX 切换 | 启动项 `-force-d3d11`（默认 DX12）—— **shim 方案下传不下去** |
| 反作弊 | **ACE**（`...\Endfield Game\AntiCheatExpert\`） |
| 备份目录 | `~\.steam_endfield_updater\` |

### ACE 反作弊

终末地用 ACE。**Steam 里的「卸载游戏」不会清理 ACE 驱动**，需要手动清理，
否则会影响后续游戏/其它反作弊程序。本工具只放 shim 和改写 ACF，不碰反作弊，
也不会动官服目录里的任何文件。`cmd_convert()` 结束时也会打印这条提醒。

### 备份与回滚

- 每次改 ACF 前自动备份到 `~\.steam_endfield_updater\appmanifest_4732690.acf.bak`
  （备份已存在且没加 `--force-backup` 时**跳过**，不会覆盖）
- `restore` 命令 / GUI「恢复备份」按钮回滚
- `shim remove` **逐个文件**删 `Endfield.exe` / `shim_config.json` / `shim.log` /
  `Games.exe` / `_internal\`，其余一律不碰
- **删光 shim 之后 Steam 会去下几十 GB 的真游戏**。安装目录里的 `Endfield.exe` 一没，
  Steam 立刻判定「文件缺失」，跑一次校验，于是给这个 app 排上**真游戏**的下载
  （`BytesToDownload` = 61998877424），并在 ACF 里写下 `StagedDepots`。
  这时候**光把 shim 装回去是不够的** —— Steam 读到 `StagedDepots` 还是会显示
  「更新中」并去续那个下载。`apply` 会连 `StagedDepots` 一起清掉（字节数本来就一直在清），
  这样 ACF 才回到「已安装、无待续更新」的状态。**改完要完全退出重启 Steam**，
  否则它用的还是内存里的旧状态。
- 实在不放心，可以手动删掉 `steamapps\downloading\4732690\`（Steam 自己的暂存目录）。
  删之前先确认没有别的 app 在下东西。

### 保留下来的三层防线（都不是测试）

1. **生产代码里的「拒绝写错地方」逻辑** —— `shim install` 拒绝盘符根目录
   （`_is_drive_root()`）、拒绝在有实体内容的目录里部署（`_partition_install_dir()`，
   **加 `--force` 也不行**）、只逐个文件删自己的东西（`_shim_installed_files()`）、
   `_internal\` 残缺检测（`_missing_payload_files()` / `clear_internal_dir()`）、
   部署前后两次完整性校验（`_exe_matches()`）。
2. **污染保险本体** —— `dir_tree_fingerprint()` / `diff_dir_tree_fingerprint()`：
   整棵目录树的递归指纹（相对路径 → 类型 / 大小 / md5），前后一比，新增 / 删除 /
   改大小 / 改内容 / 目录增删**全都**能报出来，而且能指名道姓地报出「哪一条路径变了」。
   读不动一律抛 `FingerprintError` —— **绝不**把「读不到」当成「没变化」。
3. **`ZMD_DEEP_LINK_SCHEME` 覆盖开关** —— 设了它，深链整条变成 no-op（一个
   `ShellExecuteW` 都不发）。想验深链而不惊动真协议时随时可用。
   注意它**只中和深链那一腿** —— 挂起父进程方案照走。

### 生产代码里故意保留的排障开关（不是测试脚手架，别删）

| 开关 | 位置 | 作用 |
|---|---|---|
| `ZMD_DEEP_LINK_SCHEME` | `shim/endfield_shim.py`（`DEEP_LINK_OVERRIDE_ENV`） | 设了它，深链查找直接返回它、注册表一个字节都不读，整条深链变 no-op。**只中和深链那一腿**，挂起父进程方案照走 |
| `ZMD_SHIM_KEEP_CONSOLE` | `shim/endfield_shim.py`（`hide_own_console()`） | 设成 `1`（`$env:ZMD_SHIM_KEEP_CONSOLE=1`），shim 不藏自己的控制台窗口。⚠ 生产环境里 `hide_own_console()` 取的是 `user32.GetConsoleWindow`（其实在 kernel32），所以永远记一行「[降级] 隐藏控制台窗口失败」 |
| `--force-launch` / `--force-config` | `shim/endfield_shim.py`（命令行） | 绕过父进程判断，强制走某一侧。排障时用来区分「是分流判错了」还是「启动侧坏了」 |

### 没有自动化测试网

仓库里**没有**单元测试、守卫脚本或 CI。测试基础设施在 2026-10-22 已被清理掉，
现在只剩 `zz_final.py` 这一个产物体检脚本。改 `endfield_updater.py` /
`endfield_gui.py` / `shim\endfield_shim.py` 时**没有回归网**，靠人盯：

- 至少跑一遍 `shim status` / `status` / `check` / `fetch` / `targets` / `find`
  （全部只读）
- 改过 `endfield_gui.py` 或 `endfield_updater.py` 之后必须重跑 `build_exe.py`
  （`zz_final.py` 会检查工具 exe 比源码新不新）
- 改过 `shim\endfield_shim.py` 之后必须重跑 `build_shim.py`
  （`shim status` 的「完整性 ✓」比的就是构建源和部署品的 md5）
- 想确认 shim 侧没写错地方，可以手写脚本调
  `dir_tree_fingerprint()` / `diff_dir_tree_fingerprint()` 拍前后快照

### 风险提示

- 本工具让 Steam **误判**游戏已是最新版本。若 Steam 强制校验或官方重新发布，
  仍可能触发重新下载覆盖。
- 账号/反作弊层面的行为不在本工具管辖范围内，请自行评估。
- 操作前请确认已备份游戏目录与 ACF。
