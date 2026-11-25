<a id="cli"></a>
# 命令行 endfield_updater.py

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 全部子命令、参数与行为。返回 [目录](../README.md)。

```powershell
> python -X utf8 endfield_updater.py --help
usage: endfield_updater.py [-h] [--library LIBRARY]
                           {status,check,apply,restore,fetch,junction,shim,convert,find,targets,d3d11,d3d12} ...
```

全局选项只有一个：`--library <库根目录>`（**必须放在子命令前面**），落到
`pin_library()`，锁定目标 Steam 库。

### 子命令总表

| 子命令 | 作用 | 退出码 |
|---|---|---|
| `status` | 打印 ACF 现状、目录体积、appinfo 解析结果、可用库 | `0` |
| `check` | 环境自检（Steam / appinfo / 权限 / 联接状态） | `0` 无阻塞 / `2` 有阻塞 |
| `apply` | 写入 manifest / buildid 等字段，阻止 Steam 更新覆盖 | `0` |
| `restore` | 从备份恢复 ACF | `0` 成功 / `1` 没有备份 |
| `fetch` | 只打印 appinfo 的自动解析结果（JSON） | `0` 读到 / `1` 读不到 |
| `junction` | **[已退役]** 目录联接方案 | 恒 `3` |
| `shim` | 部署 / 移除 / 查看 Steam 侧 shim | 见下 |
| `convert` | 一键完整转换（ACF + shim） | `0` / `1` / `2` / `3` |
| `find` | 定位 `Endfield.exe` | `0` 找到 / `1` 没找到 |
| `targets` | 列出可用的 Steam 库目标位置 | `0` |
| `d3d11` / `d3d12` | 记录 DX 启动项（写一份备忘，不改 Steam） | `0` |

### status

`cmd_status()`。读 `ACF_PATH`，打印 `StateFlags` / `buildid` / `TargetBuildID` /
`SizeOnDisk` / `LastUpdated` / `AutoUpdateBehavior` / `ScheduledAutoUpdate` /
`InstalledDepots[4732691].manifest` / `.size`；再打印两个目录的实测体积
（`folder_size()` 用 `os.scandir` 递归统计，联接按 `realpath` 去重）；
再打印 appinfo 的 `public buildid` / `depot manifest` / `depot size` / 启动参数，
并把 ACF 与 appinfo 比对，给出「已一致」或「建议执行 apply」。

`--json` 会在末尾附一段 JSON。

### check

`cmd_check()` → `check_environment()`。分 **阻塞项（problems）** 和 **警告（warnings）**：

- 阻塞：Steam 根 / `steamapps` 不存在；appmanifest 只读（需管理员）；`steamapps` 不可写；
  `INSTALL_DIR` 是**真实文件夹**且当前非管理员
- 警告：找不到 `appinfo.vdf`；appinfo 里没有 4732690 的 depot 信息；
  只在 Steam 库里找到 `Endfield.exe`（没有独立的官服目录）；没找到官服游戏目录；
  `INSTALL_DIR` 已经是目录联接

> **注意**：上面最后一条阻塞项（「建联接前需要先改名让位」）是**联接时代的遗留文案**，
> 现在 `shim install` 根本不会改名。它会让 `check` 在非管理员终端上恒返回 **2**，
> 但与 shim 方案本身无关。本机实跑 `check` 返回的就是 `2`。

### apply

`cmd_apply()`。这是「阻止 Steam 更新覆盖」的核心命令。逐字段说明：

| 字段 | 谁写 | 写成什么 | 为什么 |
|---|---|---|---|
| `InstalledDepots["4732691"].manifest` | `cmd_apply()` | 解析出的 manifest id | 让 Steam 以为这个 depot 已是最新版 |
| `InstalledDepots["4732691"].size` | `cmd_apply()` | 实测目录体积（`folder_size()`），取不到才用 depot size | 体积必须是实数，填 0 会被判安装不完整 |
| `buildid` | `cmd_apply()` | `target_build`（见下） | 与 `TargetBuildID` 对齐 |
| `TargetBuildID` | `cmd_apply()` | 同上 | 不对齐会被判「有待更新」 |
| `LastUpdated` | `cmd_apply()` | appinfo 的 `timeupdated` 或当前 unix 秒 | |
| `SizeOnDisk` | `cmd_apply()` | 实测体积 | |
| `StateFlags` | `cmd_apply()` | `"4"` | 4 = 已完整安装 |
| `AutoUpdateBehavior` | `cmd_apply()` | `--auto-update-behavior` 或 `1` | 1 = 仅启动时更新 |
| `ScheduledAutoUpdate` | `cmd_apply()` | `"0"` | 取消排队中的自动更新 |
| `BytesToDownload` / `BytesDownloaded` / `BytesToStage` / `BytesStaged` | `cmd_apply()` | 全 `"0"` | 字节数清零 |
| `UpdateResult` | `cmd_apply()` | `"0"` | |
| `StagedDepots` | `cmd_apply()` | **整段删除**（`state.pop()`） | 存量更新记录不清，Steam 读到它照样显示「更新中」并续那个几十 GB 的下载 |

`target_build` 的取值顺序：`--buildid` > 解析出的 buildid > ACF 里的 `TargetBuildID` >
ACF 里的 `buildid`；加了 `--use-target-as-build` 则以 ACF 的 `TargetBuildID` 为准。

> `StagingSize` **不由 `apply` 写**。只有 `build_acf_template()` 在**新建** ACF 时写 `"0"`；
> 之后这个字段由 Steam 自己维护。

其它行为：ACF 不存在时先按模板 `create_acf()` 建一份；改之前备份到
`~\.steam_endfield_updater\appmanifest_4732690.acf.bak`（**备份已存在且没加
`--force-backup` 时跳过**）。

### restore

`cmd_restore()`。把 `~\.steam_endfield_updater\appmanifest_4732690.acf.bak`
拷回 `ACF_PATH`。没有备份时返回 `1`。

### fetch

`cmd_fetch()`。只打印 `parse_appinfo()` + `extract_manifest_info()` 的结果（JSON）。
`--json` 参数被接受，但两条分支打印的都是 JSON。

自动解析（走第 3 条）出来是什么样（本机 2026-10-22 实跑）：

```powershell
> python -X utf8 endfield_updater.py fetch
```

```text
{
  "buildid": "25728898",
  "manifest": "3024011413268238677",
  "depot_size": "68276329686",
  "timeupdated": 1792016149,
  "source": "appinfo.vdf",
  "appinfo_path": "C:\\Program Files (x86)\\Steam\\appcache\\appinfo.vdf",
  "launch_options": [
    "-force-d3d11  (DirectX 11)"
  ],
  "snapshot_date": "2026-10-15"
}
```

第 4 条的兜底值（`25728898` / `3024011413268238677` / `68276329686`）就是上面这三个数
（`endfield_updater.py` 的 `FALLBACK_BUILDID` / `FALLBACK_MANIFEST` / `FALLBACK_DEPOT_SIZE`）。
终末地版本迭代很快，这个兜底值过几周就废了。真要手动指定，去
[SteamDB 的 depot manifest 列表](https://steamdb.info/depot/4732691/manifests/)
取最上面那条的 ManifestID，复制按钮给的是
`-app 4732690 -depot 4732691 -manifest <id>`。

app 页在 [SteamDB App 4732690](https://steamdb.info/app/4732690/)。

> **SteamDB 有 Cloudflare 人机验证**。用无痕窗口访问，或把复制串手工贴进
> GUI 的「SteamDB粘贴」框。粘贴串由 `parse_steamdb_copy()` 解析：认
> `-manifest <数字>` / `-buildid <数字>` / `-depot <数字>`，也认**纯数字串**（当成 manifest）。

### junction（已退役）

```powershell
> python -X utf8 endfield_updater.py junction info
```

`create_junction()` 已经**不写任何一个字节**，只打印 `JUNCTION_RETIRED_NOTICE`
并返回 `JUNCTION_RETIRED_EXIT_CODE = 3`。`junction` 的三个 action（`info` / `create` /
`remove`）在 `main()` 里根本不分发，**一律**走退役分支返回 3。

真正还有用的是 `remove_junction()`（`shim unlink` 用它）：它只 `rmdir` 联接本身，
不动目标目录；不是联接返回 `1`，删不掉返回 `2`。

### shim

```powershell
> python -X utf8 endfield_updater.py shim --help
usage: endfield_updater.py shim [-h] [--json] [--src SRC]
                                [--launcher LAUNCHER] [--force] [--keep-files]
                                [--impersonate]
                                {status,install,remove,unlink}
```

**`shim status`**（`shim_status()`）：返回 `0` 表示已部署、`1` 表示未部署。
`--json` 输出完整状态字典（`shim_mode` / `shim_payload_ok` / `deep_link` /
`launch_mode` / `launch_source` / `parent_whitelist` / `parent_match_depth` /
`config_page` / `tool_path` / `game_dir` / `src_candidates` 等）。

**`shim install`**（`shim_install()`）按四步走：

| 步 | 做什么 | 失败返回 |
|---|---|---|
| 0/4 | 路径自检：`_is_drive_root(INSTALL_DIR)` 为真就拒绝（盘符根目录永远不可能是 Steam 安装目录） | `2` |
| 1/4 | 拆旧联接（`remove_junction()`）；目录已存在且**有实体内容**时直接拒绝 | `2` |
| 2/4 | 复制：源目录里**没有** `_internal` 就是 onefile，只拷一个 `Endfield.exe`；有 `_internal` 就整目录拷 | — |
| 2.4/4 | onefile 时清掉上一版遗留的 `_internal/`（`clear_internal_dir()`），**保留目录本身** | `2` |
| 2.5/4 | 校验：onedir 查 `_missing_payload_files()`；onefile 查 `_exe_matches()`（大小 + md5） | `2` |
| 3/4 | 写 `shim_config.json` | — |
| 4/4 | 校验 exe 存在、`_exe_requires_admin()`、appmanifest 存在 | `2` |

- **找不到构建产物**返回 `1`（并把**每一个**试过的候选路径都列出来）。
- `_internal/` 里只要有**不是本工具产物**的文件（`_internal_foreign_entries()` 判定），
  **一个字节都不删**，部署直接返回 `2`。
- 「目录里有实体内容」这一条**加 `--force` 也不行** —— 代码里那段整目录改名的逻辑
  已经删掉了（2026-10-19 起）。`--force` 参数被接受但**完全没有作用**，
  `shim --help` 里「安装目录非空时改名为备份后部署」这句帮助文案是**过时的**。
- `--impersonate` 会把配置里的 `impersonate` 打开；它**需要 `game_dir`**，
  探测不到时返回 `2`。

**`shim remove`**（`shim_remove()`）：**逐个文件**删 `_shim_installed_files()` 列出的
东西 —— `Endfield.exe` / `shim_config.json` / `shim.log` / `Games.exe`（伪装模式生成的）/
`_internal\`，**其余一律不碰**。返回 `0`（成功或目录不存在）/ `1`（盘符根目录、或目标是联接）/
`2`（部分删除失败）。

> ⚠ **`--keep-files` 不起作用**。`shim_remove()` 的删除循环在函数开头就跑完了，
> `delete_files` 只在**最后**用来打印一句「已跳过实际删除（仅列出）」——
> 文件其实已经被删了。别依赖这个开关。

**`shim unlink`**：调 `remove_junction(INSTALL_DIR)`，只删联接本身。

期望看到（这是本机 2026-10-22 的真实输出）：

```text
Steam 安装目录 : C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield
shim           : 已部署  9,680,294 bytes
  部署形态    : 单文件版（onefile，自包含：只有 Endfield.exe，单独拷走也能用）
  完整性      : 与构建源逐字节一致 ✓（大小与 md5 都一致）
  shim 配置   : C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\shim_config.json
  深链 scheme : hypergryphlauncher-abYeZZ16BPluCFyT
  深链 URL    : hypergryphlauncher-abYeZZ16BPluCFyT://start?ta=endfield
  启动器      : C:\Program Files\Hypergryph Launcher\Launcher.exe
  启动方式    : 挂起父进程优先（auto），失败回落官方启动器深链
  分流方式    : 按父进程判断（parent）—— **祖先链**上任意一级的文件名是 steam.exe 就启动游戏，其它情况打开配置主页
  父进程白名单: ['steam.exe']（祖先链上任意一级的文件名命中这些名字就启动游戏，否则打开配置主页）
  祖先链深度  : 4（含直接父进程；链上任意一级命中白名单都算命中）
  配置侧页面  : tool（tool = 双击 shim 时拉起工具 GUI；拉不起来才回落内置简易页）
  工具 GUI    : <仓库目录>\release\Steam终末地更新器.exe
  game_dir    : C:\Program Files\Hypergryph Launcher\games\Endfield Game
官服游戏目录  : C:\Program Files\Hypergryph Launcher\games\Endfield Game
官方启动器    : C:\Program Files\Hypergryph Launcher\Launcher.exe
appmanifest   : C:\Program Files (x86)\Steam\steamapps\appmanifest_4732690.acf ✓
构建产物      : <仓库目录>\shim_build\dist\Endfield ✓
```

### convert

`cmd_convert()`：写/更新 ACF（步骤 1/2）→ 部署 shim（步骤 2/2）。
`--force-junction` 会打印退役说明并返回 `3`；shim 还没构建返回 `1`；
`--game-dir` 指向不存在的目录返回 `2`；正常完成返回 `0`。
末尾会打印 ACE 反作弊的提醒。

### find / targets / d3d11 / d3d12

- `find`：`find_endfield_exe()` 定位 `Endfield.exe`，按「离起始目录越浅越靠前」排序，
  跳过 `SKIP_DIR_NAMES`（`windows` / `system32` / `ace` / `backup` / `崩溃` / `日志` …）
  以及名字里带 `备份` / `bak` / `旧` / `old` 的目录。`--start` 指定起始目录。
- `targets`：`steam_link_targets()` 列出每个库的 appmanifest / 安装目录及其存在性。
- `d3d11` / `d3d12`：`set_launch_options()` **只写一份备忘**
  `~\.steam_endfield_updater\launch_options.txt`，然后提示你手动去
  Steam「属性 → 启动选项」里填。**它不改 Steam 的 `localconfig.vdf`**。

### 命令行包装

`endfield_updater.bat` / `endfield_updater.ps1` 是纯 ASCII 的启动包装
（cmd.exe / PowerShell 5.1 按 ANSI 码页读脚本，中文会破坏解析）。
`.ps1` 支持 `$env:ENDFIELD_PYTHON` 指定解释器。
