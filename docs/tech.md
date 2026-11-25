<a id="tech"></a>
# 底层机制与技术结论

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 鉴权机制的技术结论，以及已废弃方案的完整记录。返回 [目录](../README.md)。

### 鉴权只有父进程检查这一项

这是本项目最重要的一条实测结论。每一次会话开头，SDK 都写：

```text
Add launch check:Launcher, 1, 2
Total launch check size:1        ← 只有一个检查项，就是父进程
```

`Total launch check size:1` 是决定性的 —— 检查项**只有一项**。如果存在 token / 渠道
之类的第二项，这个计数不会是 1。

已确认**无关**的因素：

- **命令行参数无关** —— `-launcher_lang=zh-cn -launcher_sub_channel=1` 照传不影响判定
  （这两个参数由 `resolve_game_args()` 原样传给游戏）

### 目录联接方案为什么不行

最初用 `mklink /j` 把 Steam 安装目录指向官服目录。**实测行不通**：

1. **鉴权过不去**。联接下 Steam 直接拉起游戏，父进程是 `steam.exe`，不匹配，
   SDK 固定返回 `errorCode 10100`。
2. **更危险**：建联接前要把已存在的真实目录**整目录改名**让位
   （`INSTALL_DIR` → `INSTALL_DIR.bak_<时间戳>`）。在真装了游戏的 Steam 库上，
   这一步会把你整个游戏安装搬走。

**归因要说准**：联接失败的直接原因是「**父进程变成了 `steam.exe`，
而 `steam.exe` 不在白名单里**」。这跟「SDK 比的是全路径还是文件名」
是**两个互相独立的问题** —— 前者已被实测数据证实，后者**至今仍未确定**。

现在 `junction` 子命令只打印退役说明并返回 **3**（`JUNCTION_RETIRED_EXIT_CODE`），
**一个文件都不碰**；那段会搬走你游戏安装的代码**已从实现里删掉**
（`create_junction()` 的函数体只有打印）。替代命令是 `shim install`。

### 深链兜底路径的实测

`launch_mode=auto` 时，挂起父进程任何一步失败就回落到**调起官方启动器深链**。
这条路是**通的**（代价是启动器界面会弹出来）。

`endfield_shim.py` 的 `launch()` 里，深链的 scheme 来源有三级优先级：

1. 环境变量 `ZMD_DEEP_LINK_SCHEME`（覆盖生效时整条变 no-op）
2. `shim_config.json` 里的 `scheme`
3. 注册表 `HKCR\hypergryphlauncher-*`（第一个命中的键名）

URL 一律是 `<scheme>://start?ta=endfield`（`DEEP_LINK_TAIL`）。都失败时回落到
内置 scheme `hypergryphlauncher-abYeZZ16BPluCFyT`；再失败就找 `Launcher.exe`
用 `--game=endfield` 启动（`FALLBACK_ARGS`）—— 那只会打开界面，要人再点一次「启动游戏」。

> `--reason=6` **不是 shim 传的**。`endfield_shim.py` 里搜不到这个串
> （`FALLBACK_ARGS` 只有 `--game=endfield`）。官方启动器侧的 `launcher.log` 里
> 记着 `cmd_line:--game=endfield --reason=6` —— 那是**启动器自己**在收到深链后
> 拼出来的命令行。

SDK 侧的完整链路（父进程检查失败 → 走深链）：

```text
Not found matched name
Record check result:{"code":2003,"msg":{"code":1,"msg":{"errorCode":10100,"filePath":"c:\\program files (x86)\\steam\\steam.exe"}}},
ResolveLaunchErrorMsgBoxTexts
Try run launcher??1
Get app code:abYeZZ16BPluCFyT, game:endfield
Try run launcher, isCN:1, protocol:hypergryphlauncher-
launcher exe:C:\Program Files\Hypergryph Launcher\Launcher.exe
open deep link success:hypergryphlauncher-abYeZZ16BPluCFyT://start?ta=endfield
Try run launcher success
```

**这条兜底路径的实测结论是：它能启动游戏，但官方启动器界面一定会弹出来** ——
所以它是兜底，不是主方案。

---

<a id="deprecated"></a>
## 已废弃的方案

三套死方案的**代码、脚本、报告全部已删除**，但**结论一条没丢** —— 它们都写在本文档里。

### 目录联接

见 [目录联接方案为什么不行](#tech)。退役的代码已删干净，
`junction` 子命令现在只打印说明并返回退出码 3。

### Games.exe 伪装（实验中，尚未定论）

**曾经想回答什么**：SDK 比对父进程时，比的是**文件名**（`Games.exe`）还是
**完整路径**（`C:\...\Hypergryph Launcher\1.6.0\Games.exe`）？

**怎么做的**：造一个 `cmd.exe` 的副本改名成 `Games.exe`，用它当父进程拉起真游戏。

**结果**：**没有得出任何结论** —— A 组阳性对照（官服 `Games.exe` 作父进程，必须 `code 0`）
**自己就失效了**：40 秒内 SDK 日志没有任何新增区块，游戏根本没起来。闸门不通过 → 实验中止。

**留下的结论**：

- 这个问题**至今仍未确定**。联接失败的直接原因是「父进程变成了 `steam.exe`，
  而 `steam.exe` 不在白名单里」，这跟「比全路径还是文件名」是**两个互相独立的问题**。
  参考项目 Xel-Launcher 用的是 payload 目录里的 `Games.exe` 且能正常工作，**倾向于「比文件名」**，
  但没有实测过 —— 只作记录，不作结论。
- 因为**没有结论**，`impersonate`（`Games.exe` 改名 spawn）**默认 `false`**，
  且只在命令行显式给了 `--impersonate` 时才开。
  `endfield_updater.py` 的 `lab_impersonate_verdict()` 恒返回 `None`
  （是「没有结论」，不是「结论是 FAIL」）。

### stub 注入观测

**曾经怎么做**：把官服目录里的 `Endfield.exe` 换成一份「观测 stub」（游戏进程本体），
再把官方 `verify_files.json` 里入口项的 md5/size 一起改掉，让启动器的完整性校验放行。

**为什么死**：要往**官服目录**写东西，与本工具「不碰官服目录」的底线冲突；
改 `verify_files.json` 的 md5 会被启动器判成「文件损坏」，提示重新下载几百 MB，把 stub 冲掉。

**留下的东西**：`stub_backup/` 里还留着**官服游戏本体的唯一独立副本**
（`Endfield.exe.real`，827,960 字节，md5 `78e282a7431e38ebcd15e64c868c11be`）
和官方 `verify_files.json` 的基线（`verify_files.json.orig`，md5
`f650aedfc4e8126fe11427d8b0e4b077`）。`check_gamedir.py` 就是用来比对
「官服目录里的本体 / stub / 备份」这三者的（它认得 stub 的 md5
`cca2ebe25dfb94ce54e5b7db3ea072b5`）。
**这些副本删了再也拿不回来**，所以 `.gitignore` 挡掉了整个目录，但**本机那份别删**。

> ⚠ **复活死方案时，请自己记得只留下结论**（就像本文档这样）。
