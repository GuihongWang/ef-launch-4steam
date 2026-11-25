<a id="troubleshooting"></a>
# 排障

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 非 Steam 快捷方式陷阱与实际踩过的坑；症状速查表和日志位置在主 README。返回 [目录](../README.md)。

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

`diag_noresponse.py` 会依次打印 6 段：shim.log 末尾 25 行、Steam 侧目录内容、
相关进程（`tasklist`）、Steam 最近 6 小时的 `gameprocess_log.txt` / `content_log.txt` /
`console_log.txt` / `bootstrap_log.txt` / `connection_log.txt` 里的 4732690 相关行、
ACF 现状、游戏侧最近一次启动的 SDK 日志。

### 症状速查

| 症状 | 原因 | 怎么办 |
|---|---|---|
| 点开始游戏没反应 | shim 没被 Steam 拉起来 | 看 shim.log 有没有新行；没有就是 Steam 没找到这个 app，看 [多库机器必须选对目标库](../README.md#multilib) |
| 官方启动器界面弹出来了 | 挂起父进程方案失败并回落了深链 | 看 shim.log 有没有 `[挂起父进程失败]`；`launch_mode=auto` 时这是设计内的回落 |
| 弹启动器 + SDK 报 10100 | 点的是 Steam 里的**非 Steam 快捷方式** `Endfield` | 改从 `Arknights: Endfield` 启动，见 [快捷方式陷阱](#traps) |
| Steam 显示「更新中」并要下 62 GB | ACF 里被写进了 `StagedDepots` | 跑 `apply`（会连 `StagedDepots` 一起清），然后**完全退出重启 Steam** |
| `Failed to load Python DLL 'E:\_internal\python314.dll'` | onedir 形态的 exe 被单独拷走，缺 `_internal\` | 整个目录一起搬；生产用的是 onefile，不会有这问题 |
| 双击 shim 弹出了游戏 | `launch_source` 被改成了 `always_launch` | 改回 `parent` |
| 双击 shim 什么都没发生 | 工具 GUI 拉不起来 | 看 shim.log 有没有「已拉起工具 GUI」；没有就把 `tool_path` 填对 |
| `check` 报「建联接前需要先改名让位」 | **联接时代遗留的误报**，与 shim 方案无关 | 忽略；`check` 在非管理员终端上恒返回 2，用 `shim status` 看真实状态 |
| shim 退出的码是 3 而不是 0 | `parent_wait_ms`（默认 600000 ms）超时 | 属正常收尾；游戏**并没有**被杀，见 [已知限制](../README.md#limitations) |
| 弹过 UAC 之后游戏起不来 | 权限不够，官方 `Games.exe` 建不出来 | 用管理员终端跑部署命令 |

<a id="traps"></a>
### 非 Steam 快捷方式绕过 shim

Steam 里终末地有**两个**条目，**只有一个是对的**：

| 条目 | AppID | 指向 | 后果 |
|---|---|---|---|
| `Arknights: Endfield` | `4732690` | `…\steamapps\common\Arknights Endfield\Endfield.exe`（= 我们的 shim） | 游戏直接起来，**不弹启动器界面** |
| **`Endfield`**（非 Steam 快捷方式） | `2422059311` | **直接指向官方游戏本体** `…\Hypergryph Launcher\games\Endfield Game\Endfield.exe` | **完全绕过 shim** |

点第二个会怎样：

1. 父进程是 `steam.exe`（或资源管理器），**不是** `Games.exe` → U8 SDK 固定返回 `errorCode 10100`
2. 游戏自己走深链重起第二实例 → **启动器界面照样弹出来**，跟「没装 shim」时一模一样

也就是说：**装没装 shim 都会弹启动器，但原因完全不同** —— 装了 shim 还弹，多半就是你点错条目了。

### 实际踩过的坑

#### 坑一 官方启动器界面还是弹出来了

**现象**：Steam 点开始游戏，官方启动器的界面弹了出来，游戏要等用户在启动器里点一下才起来。

**原因**：挂起父进程方案某一步失败，`launch_mode=auto` 回落到了深链。看 shim.log 有没有
`[挂起父进程失败] …`；再看 SDK 日志：

```text
Not found matched name
Record check result:{"code":2003,"msg":{"code":1,"msg":{"errorCode":10100,"filePath":"c:\\program files (x86)\\steam\\steam.exe"}}},
```

`Not found matched name` = 父进程的名字没在白名单里；`errorCode 10100` = 拒绝。

**怎么办**：

1. 看 shim.log 的分流行。`匹配=未命中 → 走「配置主页」侧` 说明白名单没命中；
   `匹配=命中` 但 SDK 还是 10100，说明**游戏不是这个 shim 拉起来的**。
2. 确认白名单里有 `steam.exe`，`parent_match_depth` 够大（默认 4）。
3. 确认游戏进程的直接父进程确实是挂起的 `Games.exe`：

   ```text
   游戏进程 pid=… 的直接父进程 = … ← 挂起的 Games.exe ✓（SDK 要的就是这一条）
   ```

#### 坑二 errorCode 10100 配 Not found matched name

**现象**：同坑一，但日志里同时出现 `Not found matched name` 和 `errorCode 10100`。

**原因**：这就是父进程检查失败的**标准现场**。`filePath` 是被比对的父进程路径。
本机历史上出现过的 `filePath` 值：

| filePath | 说明 |
|---|---|
| `c:\program files (x86)\steam\steam.exe` | Steam 直接拉起了游戏（没经过 shim） |
| `c:\windows\explorer.exe` | 从资源管理器双击启动的 |
| `c:\windows\system32\cmd.exe` | 从命令行启动的 |
| `c:\users\<你>\appdata\local\programs\python\python314\python.exe` | 从 python 脚本启动的 |
| `c:\program files\hypergryph launcher\1.6.0\games.exe` | ✅ 命中白名单，`code 0` |

**怎么办**：对照这张表。命中白名单那行才是对的；出现上面四行中的任何一行，
都说明游戏**不是**由挂起的 `Games.exe` 拉起来的。

#### 坑三 点开始游戏没反应

**现象**：Steam 点开始游戏，**什么都没发生**，连官方启动器都没弹。

**原因**：shim 走的是「配置主页」那一侧 —— 也就是它认为**你不是从 Steam 点进来的**，
于是只开配置界面、不启动游戏。或者 Steam 根本没把 shim 拉起来。

**怎么办**：

1. 看 shim.log 有没有新行。**没有新行** = Steam 压根没拉起 shim：
   - 确认 Steam 库里那个条目的路径指向 `…\steamapps\common\Arknights Endfield\Endfield.exe`；
   - 多库机器上确认 ACF 和安装目录在**同一个库**里。
2. **有**新行但写的是「走配置主页这一侧」：
   - 看「祖先链」那一行，链上有没有 `steam.exe`；
   - 链断在 `explorer.exe` 或 `<读不到>` = 白名单没命中；
   - 确认 `parent_whitelist` 里确实是 `steam.exe`（**只比文件名**，大小写不敏感）。
3. 想跳过判断直接试：`Endfield.exe --force-launch`。

一键把上面这些信息全打出来（只读）：

```powershell
> cd <仓库目录>
> python -X utf8 diag_noresponse.py
```

#### 坑四 Steam 显示「更新中」并要下 62 GB

**现象**：装完 shim 后，Steam 里终末地变成「更新中」，要下 **62 GB**
（`BytesToDownload` = 61998877424）。

**原因**：Steam 安装目录里的 `Endfield.exe` 一没，Steam 立刻判定「文件缺失」，
跑一次校验，发现游戏文件一个都没有，于是给这个 app 排上**真游戏**的下载，
并在 ACF 里写下 `StagedDepots`。Steam 的 `content_log.txt` 里能看到这个状态迁移：

```text
[2026-10-20 19:57:52] AppID 4732690 state changed : Update Required,Fully Installed,Update Queued,Files Missing,Update Running,Update Started,
[2026-10-20 19:57:52] AppID 4732690 App update changed : Running Update,Staging,
```

**怎么办**：

1. **光把 shim 装回去是不够的** —— Steam 读到 `StagedDepots` 还是会显示「更新中」并续那个下载。
2. 跑 `apply`，它会连 `StagedDepots` 段整个清掉，ACF 才回到「已安装、无待续更新」：

   ```powershell
   > python -X utf8 endfield_updater.py apply
   ```

3. **完全退出并重启 Steam**，否则它用的还是内存里的旧状态。
4. 实在不放心，可以手动删掉 `steamapps\downloading\4732690\`（Steam 自己的暂存目录）。
   删之前先确认没有别的 app 在下东西。

#### 坑五 Failed to load Python DLL 'E:\_internal\python314.dll'

**现象**：双击 shim 弹出这个框，游戏完全起不来。

```text
Failed to load Python DLL 'E:\_internal\python314.dll'.
LoadLibrary: 找不到指定的模块。
```

**原因**：**不是 shim 坏了，是少了 `_internal\`**。这是 **onedir 形态**
（exe + `_internal\`）的规矩：exe 启动时去**自己所在目录**找 `_internal\python314.dll`，
只把 exe 单独拷走就必然报这个。

**怎么办**：

- **不要单独拷 `Endfield.exe`**，要移动就整个目录一起搬。
- 生产用的是 **onefile**（自包含单文件），**不会有**这个问题。确认部署的是哪个形态：

  ```powershell
  > python -X utf8 endfield_updater.py shim status
  ```

  输出里 `部署形态 : 单文件版（onefile，自包含：只有 Endfield.exe，单独拷走也能用）` 就对了。
  部署目录里的 `_internal\` 应该是**空目录**（0 个文件）。

- `shim install` 有两道防线：onedir 形态下用 `_missing_payload_files()` 核对
  `_internal\`（缺文件或大小对不上就**部署中止，返回 2** 并列出缺了哪几个）；
  onefile 形态下用 `_exe_matches()` 比大小 + md5。

#### 坑六 非 Steam 快捷方式绕过 shim

见 [非 Steam 快捷方式绕过 shim](#traps)。
