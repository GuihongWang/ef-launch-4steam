# 《明日方舟：终末地》Steam 直启工具

把 **Hypergryph 官方启动器下载的《明日方舟：终末地》** 挂进外区 Steam：
在 Steam 库里点「开始游戏」，**游戏直接起来，官方启动器界面不出现**。

本工具**不碰游戏本体、不改任何官方文件**。Steam 安装目录里只多一份约 **9.2 MiB**
的 shim（`Endfield.exe`，onefile 单文件形态），官服目录全程只读。

本文的每一条技术断言都标注了出处（函数名 / 常量名），出处都在本仓库的源码里。
行号不写 —— 行号会漂，函数名不会。深度内容已拆到 [`docs/`](#docs)：主文档只留
快速开始、机制概要、排障速查与已知限制。

## 目录

- [当前状态](#status)
- [快速开始](#quickstart)
- [它到底做了什么（机制）](#mechanism)
- [排障速查](#troubleshooting)
- [已知限制与未验证项](#limitations)
- [构建](#build)
- [深入文档 docs/](#docs)

---

<a id="status"></a>
## 当前状态

**2026-10-22 在本机完成端到端验证**：三份互不相干的日志 —— 工具自己的 shim 日志、
游戏 SDK 的鉴权日志、Steam 自己的进程日志 —— 对上了同一组 pid。游戏进程的直接父进程
就是那份**挂起的官方 `Games.exe`**（pid 37604 同时出现在 shim.log 的「挂起的 Games.exe」
和 SDK 的 `Parent process check success:37604` 里），父进程检查通过，ACE 正常拉起，
**官方启动器界面全程没出现**。

- 三份日志原文与 pid 闭环表：[`docs/verification.md`](docs/verification.md)
- 如实记录的一处瑕疵：那次运行 shim 最终返回 **rc=3**（`parent_wait_ms` 10 分钟超时），
  游戏**并没有**被杀，原因见 [已知限制](#limitations)

---

<a id="quickstart"></a>
## 快速开始

### 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 x64 |
| 官服 | 已装 Hypergryph 启动器，且游戏本体已下载完（`C:\Program Files\Hypergryph Launcher\games\Endfield Game\Endfield.exe`，实测 66.90 GiB） |
| Steam | 外区 Steam 客户端；库里有 `Arknights: Endfield`（AppID 4732690）这个条目 |
| Python | 3.10+，**仅当你要跑源码版 CLI / GUI 时需要**；用打包好的 exe 不需要 |
| 权限 | 管理员。shim 的清单是 `requireAdministrator`（`shim/shim.manifest`），游戏本体自己也是 |

### 三步装上

**第 1 步** — 打开工具。打包好的 exe（推荐，从 [Releases](https://github.com/GuihongWang/ef-launch-4steam/releases) 下载 `endfield-steam-updater.exe`，即《Steam终末地更新器》—— GitHub 不允许资产名含中文，下载后可随意改名）：

```powershell
> <下载目录>\endfield-steam-updater.exe
```

或本机构建（仓库目录下）：

```powershell
> <仓库目录>\release\Steam终末地更新器.exe
```

或者跑源码版：

```powershell
> cd <仓库目录>
> python -X utf8 endfield_gui.py
```

**第 2 步** — 在 **「官服 → Steam：部署 shim（启动器接管）」** 分区里：

1. 点 **自动检测 Steam 库**，确认选中的库是对的（多库机器上选错就白搭，见 [多库机器必须选对目标库](#multilib)）
2. 填 **官服目录**（含 `Endfield.exe` 的那层），或点 **查找 Endfield.exe…** 让它自动找
3. 点 **一键完整转换** —— 写 ACF + 部署 shim，一步到位

命令行等价写法（**需要管理员权限的终端**）：

```powershell
> cd <仓库目录>
> python -X utf8 endfield_updater.py convert --game-dir "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
```

只想装 shim、不动 ACF：`python -X utf8 endfield_updater.py shim install`。
全部子命令见 [`docs/cli.md`](docs/cli.md)。

**第 3 步** — **完全退出并重启 Steam**（托盘里右键退出，不是关窗口）。
Steam 内存里还留着旧状态，不重启的话 ACF 改了也不生效。

### manifest 和 buildid 从哪来

第 2 步写进 `appmanifest_4732690.acf` 的那两个数（depot manifest id、buildid）
是让 Steam 以为「你已经是最新版本」的关键。真实优先级
（`endfield_updater.py` 的 `_resolve_manifest_buildid()`）：

1. `--from-steamdb` 粘贴的 SteamDB 复制串
2. `--manifest` / `--buildid` 命令行手填
3. 本机 `appcache\appinfo.vdf`（**Steam 联网启动一次**才会有）
4. 内置快照 `SNAPSHOT_DATE = "2026-10-15"`（**大概率已过期，仅兜底**）

> ⚠ 注意顺序：代码里 `--from-steamdb` 是**最后**套用的，所以**粘贴串会覆盖命令行
> 手填的值**。`endfield_updater.py` 的模块 docstring 把 1、2 写反了 —— 以
> `_resolve_manifest_buildid()` 的实际执行顺序为准。

第 3 条拿不到时，去 [SteamDB 的 depot manifest 列表](https://steamdb.info/depot/4732691/manifests/)
取最上面那条的 ManifestID（复制按钮给的是 `-app 4732690 -depot 4732691 -manifest <id>`），
贴进 GUI 的「SteamDB粘贴」框 —— **SteamDB 有 Cloudflare 人机验证**，用无痕窗口访问，
或手工粘贴。app 页：[SteamDB App 4732690](https://steamdb.info/app/4732690/)。
`fetch` 子命令可直接看自动解析结果（输出示例见 [`docs/cli.md`](docs/cli.md)）。

### 日常怎么用

- **玩游戏**：Steam 库 → `Arknights: Endfield` → **开始游戏**。游戏直接起来，官方启动器界面不出现。
- **改配置**：双击 Steam 安装目录里的 `Endfield.exe`（就是 shim 本身）→ 工具 GUI 打开。
  shim 认得出「不是 Steam 拉的我」，会自动开配置页而不启动游戏。

### 怎么确认装对了

```powershell
> cd <仓库目录>
> python -X utf8 endfield_updater.py shim status
```

期望看到的关键行（完整输出示例见 [`docs/cli.md`](docs/cli.md)）：

```text
shim           : 已部署  9,680,294 bytes
  完整性      : 与构建源逐字节一致 ✓（大小与 md5 都一致）
  启动方式    : 挂起父进程优先（auto），失败回落官方启动器深链
  分流方式    : 按父进程判断（parent）—— 祖先链上任意一级的文件名是 steam.exe 就启动游戏
appmanifest   : ...\appmanifest_4732690.acf ✓
```

再确认一次 Steam 侧没在排队下真游戏：

```powershell
> python -X utf8 endfield_updater.py status
```

关键三行：`StateFlags 4`（已完整安装）、`BytesToDownload 0`、ACF 里**没有** `StagedDepots` 段。

<a id="multilib"></a>
### 多库机器必须选对目标库

自动探测（`endfield_updater.py` 的 `resolve_game_paths()`）按这个顺序挑目标库：

1. 已 `pin_library()` 锁定的库（`--library` 或 GUI 下拉框）
2. 第一个**已有 appmanifest** 的库
3. 第一个**已有 common 安装目录**的库
4. `discover_steam_libraries()` 返回的第一个库

写错库 Steam 根本看不到游戏。命令行下用 `--library` 指定（**必须放在子命令前面**）：

```powershell
> python -X utf8 endfield_updater.py --library "E:\SteamLibrary" convert --game-dir "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
```

GUI 里在 **Steam库** 下拉框选目标即可（点「自动」恢复自动选择，调用 `unpin_library()`）。

---

<a id="mechanism"></a>
## 它到底做了什么（机制）

### 问题 官方启动器会抢走游戏

《终末地》的游戏进程启动时要做一道 **U8 SDK 启动鉴权**。官方渠道下，
游戏必须由鹰角官方启动器的 `Games.exe` 直接拉起，否则拒绝启动。

而 Steam 只会拉起自己安装目录里的 exe。所以要同时满足两件事：

1. Steam 点「开始游戏」能拉起东西；
2. 被拉起的**那个游戏进程**的直接父进程必须是官方 `Games.exe`。

### 鉴权机制 只查父进程路径

SDK 的鉴权日志（`OneChannelLC.log`）每一会话开头都写着：

```text
Add launch check:Launcher, 1, 2
Total launch check size:1        ← 只有一个检查项，就是父进程
```

失败时：`Not found matched name`（父进程名字不在白名单里），
`filePath` 就是它拿去比对的**父进程路径**。

**判定的是「指定的那个父进程」，不是「进程表里有没有 Games.exe」。**
依据：SDK 打的日志是 `Parent process check success:<pid>`，那个 pid 精确等于
本工具挂起的那个 `Games.exe` 的 pid（37604）；扫全进程表不需要、也不该打印某个具体 pid。
失败现场与完整日志原文见 [`docs/tech.md`](docs/tech.md)。

### 解法 挂起一份官方 Games.exe 当父进程

既然 SDK 只看父进程是谁，那就给它一个**真的官方 `Games.exe`** 当父进程 —— 但不让它跑。

```text
steam.exe
  └─ <Steam安装目录>\Endfield.exe          shim（调度器）
       ├─ ① CreateProcessW(官方 Games.exe, CREATE_SUSPENDED)
       │     ← 真正的官方 Games.exe，建出来后**永不恢复**
       └─ ② 真正启动游戏，并把 ① 的进程指定为游戏的父进程
            （PROC_THREAD_ATTRIBUTE_PARENT_PROCESS）
                └─ 官服目录里的 Endfield.exe
```

四个关键点（全部在 `endfield_shim.py` 的 `run_suspended_parent()` 里）：

1. `_create_suspended_parent()` 用 `CREATE_SUSPENDED` 建一个**真正的官方 `Games.exe`**，
   进程存在但**从未执行任何代码**（主线程句柄建完就 `CloseHandle`，永不 `ResumeThread`）
2. `_create_child_with_parent()` 用 `InitializeProcThreadAttributeList` +
   `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_PARENT_PROCESS)` +
   `CreateProcessW(EXTENDED_STARTUPINFO_PRESENT)` 把它指定为游戏进程的父进程
3. `SuspendedParent.__exit__()` 保证**任何**路径（包括异常）都会 `TerminateProcess` 掉它
4. 于是游戏进程的**直接父进程**就是官方 `Games.exe`，父进程检查通过，
   而启动器那一层界面**根本不会出现**

shim 在建它之前先把官方 `Games.exe` 以只读共享方式锁住（`_open_image_read_lock()`，
`CreateFileW(GENERIC_READ, FILE_SHARE_READ)`），防止启动器升级期间文件被替换；
建完之后自查一次父进程（`read_parent_pid()`），对不上就判失败。

### 分流 按父进程祖先链决定走哪一侧

shim 自己就叫 `Endfield.exe`，装在 Steam 安装目录里。它**不能无脑启动游戏** ——
用户从资源管理器双击它的时候，那显然不是「想玩游戏」，应该开配置界面。

**判定的是「祖先链」，不是只看直接父进程。** 链上**任意一级**的文件名命中白名单
（默认 `steam.exe`，只比文件名、大小写不敏感）就算「从 Steam 来的」：

| 祖先链 | 行为 |
|---|---|
| 链上任意一级命中白名单 | 走「启动游戏」那一侧 |
| 一级都没命中（含双击） | 打开**配置主页**，不启动游戏、不调深链、不建任何进程 |
| 认不出祖先链（已退出 / PID 被复用） | 落到**安全默认**：配置主页 |

为什么必须看祖先链而不是直接父进程：真 Steam **可能经 `steamwebhelper.exe` 中转**
再拉起游戏进程。只看直接父进程一旦认错，shim 会走配置主页，**游戏永远起不来**。

三道硬闸（每一级都要过，`endfield_shim.py` 的 `collect_ancestor_chain()`）：

| 闸 | 规则 | 不满足时 |
|---|---|---|
| PID 复用防护 | 该祖先的**创建时间必须早于**它的下一级子进程（容差 `PARENT_BIRTH_TOLERANCE_S = 0.01` 秒） | 停止向上遍历 |
| 深度上限 | `parent_match_depth`，默认 **4**（**含直接父进程**，上限 16） | 走满就停 |
| 有环保护 | 已访问过的 pid 再次出现 | 停止遍历 |

**已终止的祖先**（Steam 先退再拉起）不参与白名单匹配（`AncestorNode.matchable`
要求 `terminated` 为假），但**可以继续往上**找它的父进程。PyInstaller onefile 的
**引导进程**（同一个 exe 的另一个实例）照旧跳过（`AncestorNode.same_exe`）：
它永远不是启动者，不参与匹配，但链继续往上走。

### 为什么 shim 要提权

`Endfield.exe` 自己就是 `requireAdministrator`（ACE 反作弊要求）。
如果 shim 不提权，链路 `Launcher.exe` / `Games.exe` 都是普通权限，
到 `Endfield.exe` 那一步 `CreateProcess` 直接 `WinError 740`
（`_create_suspended_parent()` 对 740 专门有提示文案）。
shim 提权后整条链继承，只弹**一次** UAC。

> PyInstaller 必须加 `--uac-admin`（`build_shim.py` 的命令行里就有），否则它会把自己
> manifest 里的 `asInvoker` 覆盖回来。`shim install` 部署后会读 PE 字节校验这一点
> （`endfield_updater.py` 的 `_exe_requires_admin()`）：读到 `asInvoker` 直接返回码 **2**；
> **读不出来**时只打印「请人工确认是否提权」并继续。

### 参考实现 Xel-Launcher

本项目的实现思路来自 [Xel-Launcher](https://github.com/lTinchl/Xel-Launcher)
（`Helpers/ParentProcessLauncher.cs` 里的 `StartSuspended`）：它同样用 `CREATE_SUSPENDED`
建一份 `Games.exe` 当父进程、并且**永不恢复它**。

---

<a id="troubleshooting"></a>
## 排障速查

### 日志在哪

| 日志 | 路径 | 看什么 |
|---|---|---|
| **shim 日志** | `C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\shim.log` | 分流决策、建挂起父进程、游戏进程 pid、退出码 |
| **SDK 鉴权日志** | `C:\Users\<你>\AppData\LocalLow\Hypergryph\Endfield\sdklogs\OneChannelLC.log` | `Parent process check success:<pid>` / `Not found matched name` / `Total launch check size` |
| **SDK 详细日志** | `C:\Users\<你>\AppData\LocalLow\Hypergryph\Endfield\sdklogs\u8sdk_pc.log` | `U8ChannelSdkQueryProductList` 等 |
| **官方启动器日志** | `C:\Users\<你>\AppData\LocalLow\Hypergryph\33a0a6296a20400d503c59ac0fd6341e\logs\launcher.log` | 深链进来时的 `--reason=6` |
| **Steam 进程日志** | `C:\Program Files (x86)\Steam\logs\gameprocess_log.txt` | Steam 把哪些 pid 登记成 AppID 4732690 |
| **Steam 下载日志** | `C:\Program Files (x86)\Steam\logs\content_log.txt` | 是不是被排上了更新队列 |

**shim.log 里最该看的三行**：

```text
[分流] 祖先链 = pid:…(…) -> …            ← 从直接父进程开始、由近到远
[分流] … 命中层级=N 命中文件名=steam.exe … → 走「启动游戏」侧
游戏进程 pid=… 的直接父进程 = … ← 挂起的 Games.exe ✓（SDK 要的就是这一条）
```

一键打包所有排障信息（**只读，不动任何东西**）：

```powershell
> cd <仓库目录>
> python -X utf8 diag_noresponse.py
```

（依次打印 shim.log 末尾 25 行、Steam 侧目录内容、相关进程、Steam 最近 6 小时日志里
的 4732690 相关行、ACF 现状、游戏侧最近一次启动的 SDK 日志）

### 症状速查

| 症状 | 原因 | 怎么办 |
|---|---|---|
| 点开始游戏没反应 | shim 没被 Steam 拉起来 | 看 shim.log 有没有新行；没有就是 Steam 没找到这个 app，看 [多库机器必须选对目标库](#multilib) |
| 官方启动器界面弹出来了 | 挂起父进程方案失败并回落了深链 | 看 shim.log 有没有 `[挂起父进程失败]`；`launch_mode=auto` 时这是设计内的回落 |
| 弹启动器 + SDK 报 10100 | 点的是 Steam 里的**非 Steam 快捷方式** `Endfield` | 改从 `Arknights: Endfield` 启动，见 [快捷方式陷阱](docs/troubleshooting.md#traps) |
| Steam 显示「更新中」并要下 62 GB | ACF 里被写进了 `StagedDepots` | 跑 `apply`（会连 `StagedDepots` 一起清），然后**完全退出重启 Steam** |
| `Failed to load Python DLL 'E:\_internal\python314.dll'` | onedir 形态的 exe 被单独拷走，缺 `_internal\` | 整个目录一起搬；生产用的是 onefile，不会有这问题 |
| 双击 shim 弹出了游戏 | `launch_source` 被改成了 `always_launch` | 改回 `parent` |
| 双击 shim 什么都没发生 | 工具 GUI 拉不起来 | 看 shim.log 有没有「已拉起工具 GUI」；没有就把 `tool_path` 填对 |
| `check` 报「建联接前需要先改名让位」 | **联接时代遗留的误报**，与 shim 方案无关 | 忽略；`check` 在非管理员终端上恒返回 2，用 `shim status` 看真实状态 |
| shim 退出的码是 3 而不是 0 | `parent_wait_ms`（默认 600000 ms）超时 | 属正常收尾；游戏**并没有**被杀，见 [已知限制](#limitations) |
| 弹过 UAC 之后游戏起不来 | 权限不够，官方 `Games.exe` 建不出来 | 用管理员终端跑部署命令 |

> 快捷方式陷阱的完整解释、实际踩过的坑（含逐条排查过程）→
> [`docs/troubleshooting.md`](docs/troubleshooting.md)

---

<a id="limitations"></a>
## 已知限制与未验证项

**Steam 启动项传不下去。** shim 收到的命令行参数会被**忽略** —— 真正启动游戏的
不是 shim，而是官服链路里的 `Games.exe`。游戏进程的命令行由
`resolve_game_args()` 决定（默认 `OFFICIAL_GAME_ARGS` =
`("-launcher_lang=zh-cn", "-launcher_sub_channel=1")`），而 shim 只认
`--hide` / `--launch-game` / `--force-config` / `--force-launch` 四个开关，
其余一律忽略。所以在 Steam 属性里填 `-force-d3d11` **不会有效果**。

**`parent_wait_ms` 超时时游戏其实不会被杀掉。** `_wait_game_exit()` 用
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)` 打开句柄 ——
**没有 Win32 的 `PROCESS_TERMINATE` 权限**，所以那行 `TerminateProcess(h, 1)` 不会成功
（返回值也没检查），函数照样返回 `3`。本机 2026-10-22 13:35 那次运行正是如此：
shim 在 13:45:27 返回 rc=3，而 Steam 侧直到 14:32:54 才记录游戏退出。
**后果**：挂机超过 10 分钟时，shim 会先于游戏退出并把 `3` 透传给 Steam，
而游戏还在跑。

**藏窗口功能是实验性的，默认关闭。** `hide_launcher` **不要打开**，除非你明确知道自己在做什么。
实测失败过两次，根因至今不明（代码注释里的记录）：一次阻断了启动（`Games.exe` 卡在
`OnRecvWebPageLoaded` 再不执行点击启动，游戏压根没被创建），一次让窗口藏住之后无法关闭。
代码里有几道防线（观察期、启动宽限期、失败时还原窗口），但那些防线只是**降低**风险，
**不构成「它能用」的证据**。官方启动器界面本来就已经被挂起父进程方案绕开了，藏窗口对它没有价值。

**`ZMD_SHIM_KEEP_CONSOLE` 实际上是空转的**（原因见 [环境变量](docs/shim.md#shimflow) 一节）。

**`ZMD_DEEP_LINK_SCHEME` 只中和深链那一腿**（原因见同节），不是启动侧总开关。

**`--keep-files` 不阻止删除**（原因见 [shim remove](docs/cli.md#cli) 一节）。

**`shim --force` 与 `convert --force` 都没有作用**（原因见 [shim install](docs/cli.md#cli) 一节）；
这两处 CLI 帮助文案是过时的。

**Steam overlay（Shift+Tab）**在本方案下**没有任何实测记录**。游戏进程不是 Steam 直接启动的
（Steam 启动的是 shim），overlay 是否还能注入、能否正常显示，**未验证**。

**多库机器**必须选对目标库，否则 Steam 看不到游戏。

**Steam 强制校验或官方重新发布**时，仍可能触发重新下载覆盖 —— 本工具让 Steam
**误判**游戏已是最新版本，不是真的把真游戏装上了。

### 三个未验证项

| 未验证项 | 说明 |
|---|---|
| **ACE 是否接受挂起父进程** | 反作弊是独立的一层。本方案完全绕开了启动器，而 ACE 有没有单独校验父进程 / 启动链，没人试过 |
| **token 从哪来** | 现在已知启动鉴权只有父进程一项、且不登录启动器也能进游戏，但「游戏后续的账号态怎么建立」**仍然没有实测结论**，不要当成已经解决 |
| **Steam「停止」硬杀会不会留下孤儿** | Steam 的「停止」是硬杀（`TerminateProcess`）。如果游戏被杀而挂起的父进程没被一起带走，它会变成一个**永远挂起**的孤儿进程。正常退出路径（游戏退出后回收父进程）是处理掉的，硬杀路径未验证 |

---

<a id="build"></a>
## 构建

```powershell
> cd <仓库目录>
> python build_shim.py     # -> shim_build\dist\Endfield\Endfield.exe（onefile，默认）
> python build_exe.py      # -> release\Steam终末地更新器.exe（内嵌 shim 负载）
```

- `build_shim.py` 必须带 `--uac-admin`（脚本里已带），构建完校验 PE 里有
  `requireAdministrator`，没有直接返回 1
- `build_exe.py` **拒绝打包 onedir 形态的 shim 负载**（会把 19 MB `_internal\` 塞回 Steam 库）
- 改了代码必须重跑对应构建；部署产物体检：`python -X utf8 zz_final.py`
  （⚠ 跑完会**删掉部署目录里的 `shim.log`**）
- 构建细节、代码结构地图、文件清单、维护者须知 → [`docs/maintainer.md`](docs/maintainer.md)

---

<a id="docs"></a>
## 深入文档（docs/）

| 文档 | 内容 |
|---|---|
| [`docs/verification.md`](docs/verification.md) | 端到端验证记录：三份日志原文、pid 闭环 |
| [`docs/cli.md`](docs/cli.md) | `endfield_updater.py` 全部子命令、参数与输出示例 |
| [`docs/config.md`](docs/config.md) | `shim_config.json` 全部配置项、坏值回落、生效方式 |
| [`docs/gui.md`](docs/gui.md) | 工具 GUI 分区、按钮行为、线程模型 |
| [`docs/shim.md`](docs/shim.md) | shim 运行时行为：分流优先级、开关、环境变量、shim.log 行式 |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | 快捷方式陷阱、实际踩过的坑 |
| [`docs/tech.md`](docs/tech.md) | 底层鉴权机制技术结论、已废弃方案 |
| [`docs/maintainer.md`](docs/maintainer.md) | 构建详情、代码结构地图、文件清单、维护者须知 |

> 同系列：鸣潮版 [ww_shim](https://github.com/GuihongWang/ww_shim) —— 同一套架构
> （祖先链分流 shim + ACF 欺骗 + tkinter GUI），直接启动游戏、无需挂起父进程。
