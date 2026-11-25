#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 侧 shim —— 终末地（App 4732690）

Steam 的 appinfo 把启动目标固定为 <installdir>\\Endfield.exe，没法改成别的 exe。
本 shim 就叫 Endfield.exe，部署在 Steam 的安装目录里，被 Steam 点「开始游戏」拉起后：

    shim(Endfield.exe)  ->  hypergryphlauncher-<appcode>://start?ta=endfield
                             ->  Launcher.exe
                                 ->  Games.exe
                                     ->  Endfield.exe（官服目录本体）

为什么必须绕这一圈：U8 SDK 的启动鉴权（OneChannel 渠道校验）要求
Endfield.exe 的**直接父进程**是 Hypergryph Launcher 的 Games.exe，
否则报 errorCode 10100「请通过官方启动器打开游戏」。账号 token 也由
Games.exe 经共享内存注入给游戏，绕不过去。

所以本 shim 不碰游戏本体、不改任何官方文件，只是把启动链路接回官方启动器。

必须用协议 URL，**不能用命令行参数** —— 实测对比：

    Launcher.exe --game=endfield                    只打开启动器界面，要人点「启动游戏」
    Launcher.exe --game=endfield --reason=6         同上，不自动开始
    hypergryphlauncher-<appcode>://start?ta=endfield 游戏 14 秒后自己起来，无点击

scheme 里那段 app_code 是启动器应用码（国服 abYeZZ16BPluCFyT）。
不硬编码，从注册表 HKCR\\hypergryphlauncher-* 读 —— 启动器换版本/换渠道也不会失效。

刻意不做「已经在运行就别启动」的判断：官方启动器常驻后台是常态，
那种 guard 会让每一次点开始游戏都变成空操作；而且 shim 自己就叫
Endfield.exe，按进程名查必然把自己也查进去。重复点击交给官方启动器去重。

shim_config.json 里记录的启动器路径仅用于诊断和兜底。

--------------------------------------------------------------------------
藏窗口（--hide）
--------------------------------------------------------------------------
深链会把启动器界面弹出来，用户点「开始游戏」时看到的是那一层而不是游戏。
进程链**不能去掉**（SDK 硬性要求 Games.exe 是游戏的直接父进程），
所以只能把 Games.exe 的主窗口按下去：

    shim(Endfield.exe)  -- 调完深链后派生 -->
    shim(Endfield.exe) --hide   DETACHED_PROCESS | CREATE_NO_WINDOW
                              -> 每 50ms（前 10 秒）/ 200ms 之后枚举一次
                                 Games.exe 的顶层可见窗口 -> ShowWindow(SW_HIDE)

两档节奏是分开算的：窗口枚举很便宜，50ms 一遍；进程扫描要开一千多个进程句柄
（实测 ~12ms），所以只每 0.5s 一次，且一趟同时问出「Games.exe 在哪」和
「游戏起来没」。

时机是关键，而且比原设计想的还要晚。games.log / launcher.log 实测的顺序：

    19:41:10.734  Launcher.exe 收到深链
    19:41:10.905  DeepLink detected, resolve result:2
    19:41:11.129  Games.exe enter main
    19:41:12.110  OnMainWndCreated                          窗口此时才创建
    19:41:12.301  OnWebLoadFinished -> _DoDelayShowMainWnd   窗口在这里才被 show
    19:41:12.368  OnRecvWebPageLoaded                        网页内容加载完毕
    19:49:25.385  DoMainBtnClicked                           ← 真正拉起游戏的那一步

**2026-10-17 19:41 / 19:49 两次实测：hider 从 0 秒开始藏窗口，两次都把启动器
按死了 —— DoMainBtnClicked 一次都没出现，cgsu create game process 一次都没出现，
游戏进程从头到尾没被创建（OneChannelLC.log 里连鉴权记录都没有）。**
19:49 那次窗口被藏了 4 下之后，Games.exe 干脆退出了。

反过来对照不藏窗口的会话（15:48:33 那次旧的 shim），DoMainBtnClicked 出现在
启动后第 52 秒，然后 cgsu 正常拉起游戏。所以「藏窗口」和「启动器不再拉起游戏」
之间是强相关的，不是巧合。

至于启动器内部到底为什么被按死（日志里看不到 WebEngine 那一侧），**无法确定**：
日志只到 OnRecvWebPageLoaded，之后就没有下一行了，看不到 JS bridge 有没有把
点击事件送进来。可能的机制有「窗口不可见导致 WebEngine 渲染/JS 调度被节流」
和「_DoDelayShowMainWnd 之后还有一步依赖窗口可见」两种，日志不足以区分。
这里不编一个听起来合理的原因。

修复方向因此定成**保守**的：

    1. 不再从 0 秒开始藏。先等启动器自己把首次显示做完（观察期），
       期间只记录不隐藏。
    2. 更重要的：**启动宽限期**。这段时间内游戏进程没出现 = 判定启动失败，
       把藏掉的窗口**还原**。只藏不还等于给用户留一个看不见也关不掉的
       Games.exe —— 这比不藏严重得多。
    3. hide_launcher 默认改成 **false**。藏窗口是外观，坏掉的启动器是灾难；
       默认必须是不藏。

硬规则：藏窗口是尽力而为，绝不影响游戏启动。它在深链调完之后才派生，
任何失败只写 shim.log。游戏能起来这件事已经验证过，不为它冒险。

--------------------------------------------------------------------------
Games.exe 伪装（--impersonate，实验性，**默认关闭**）
--------------------------------------------------------------------------
上一节只是把启动器窗口按住，进程链仍然得经过官方启动器。用户要的是
把这一层**整个去掉**。做法是让游戏自己的父进程就叫 Games.exe：

    steam.exe
      └─ <Steam安装目录>\\Endfield.exe    调度器（就是本 shim，Steam 拉起的就是它）
           └─ <Steam安装目录>\\Games.exe  同一个二进制改名后的副本
                └─ 官服游戏目录\\Endfield.exe

全程不碰官方启动器，它的界面根本不存在。

能不能成**取决于一个还没测的前提**：SDK 比对父进程时比的是文件名还是
完整路径。当年的判定实验（脚本已删除，结论见 README）就是测这个的
（B 组 PASS = 只比文件名）—— 但它**没有得出任何结论**（阳性对照自己失效了）。

所以 impersonate 默认 false。B 组没过还开着，必然 errorCode 10100 ——
那是在拿一个已经能用的方案去赌一个没验证的假设。两个进程都要**同步等待**
子进程：父进程一退出，SDK 就查不到它的映像路径，判定直接空掉。

--------------------------------------------------------------------------
挂起父进程（launch_mode，本方案的主路径）
--------------------------------------------------------------------------
上面两节都不理想：深链会把启动器界面弹出来（藏窗口实测失败两次，原因不明），
impersonate 是拿没验证的假设赌一个已经能用的方案。

真正把启动器界面**整个去掉**的办法，是 Windows 本身就有的机制 ——
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS。开源项目 Xel Launcher 的
Helpers/ParentProcessLauncher.cs 已经把这条路走通了，本节照它的思路实现：

    1. CreateProcessW(Games.exe, CREATE_SUSPENDED=0x4) —— 创建**真正的官方**
       Games.exe，然后**永远不恢复它**。它一行代码都不执行，所以启动器界面
       永远不会出现；但进程存在、PID 有效、PEB 里的映像路径已填好，SDK 查得到。
    2. InitializeProcThreadAttributeList(1 个属性) +
       UpdateProcThreadAttribute(0x00020000 /* PARENT_PROCESS */, 父句柄)
    3. CreateProcessW(Endfield.exe, EXTENDED_STARTUPINFO_PRESENT=0x80000
       | CREATE_UNICODE_ENVIRONMENT=0x08000000, STARTUPINFOEX)
       —— 游戏进程的直接父进程就是那个挂起的 Games.exe
    4. 游戏退出（或 shim 退出）时 TerminateProcess 掉挂起的父进程，不泄漏

对照 Xel 的实现，几个细节是照抄的、每一个都有理由：

  * **父进程永远不 ResumeThread。** 恢复它就等于把启动器跑起来，本方案的意义
    归零。主线程句柄用不上（永远不 Resume），创建完就关掉。
  * **用 STARTUPINFOEX 而不是 STARTUPINFO**，且 StartupInfo.cb 必须等于
    sizeof(STARTUPINFOEX) 而不是 sizeof(STARTUPINFO)。写错了 CreateProcessW
    会失败或者父进程属性被忽略。
  * **创建挂起父进程之前先拿一个只读锁**（CreateFileW + FILE_SHARE_READ）：
    共享模式只允许别人读，不允许写/改名 —— 启动器升级正好是「替换 Games.exe」。
    锁只覆盖「定位到文件」到「子进程建好」这一小段，之后就释放，
    和 Xel 的 `using var imageLock` 作用域一致。
  * **生命周期由一个 SuspendedParent 持有**：游戏退出 or shim 自己退出 →
    TerminateProcess。任何异常路径也必须走它，否则会留下一个挂死的 Games.exe。

**已知未验证的风险**（不能靠读代码消除）：
  README 记录 token 由 Games.exe 经共享内存注入。挂起的 Games.exe 一行代码都不跑，
  不会建那块共享内存。Xel 对官服渠道也是这样干的且能玩，说明 token 大概率另有来源
  （游戏读本地 sdk_data_* 里缓存的登录态），但**本机没实测过**，不编理由。

launch_mode 三档：
    "auto"（默认）      先试挂起父进程，任何一步失败就回落深链
    "suspended_parent"  只走挂起父进程，失败就报错弹框（不回落）
    "deep_link"         只走深链，等价于本节之前的行为，保留作兜底

--------------------------------------------------------------------------
按父进程分流（入口处的一道闸）—— 沿**祖先链**匹配
--------------------------------------------------------------------------
shim 自己就叫 Endfield.exe，装在 Steam 的安装目录里。它**不能无脑启动游戏**：

    祖先链上**任意一级**的文件名命中白名单
                          -> 走上面那套启动逻辑（launch_mode 原样生效）
    链上没有任何一级命中  -> 打开配置主页，不启动游戏
    认不出祖先链          -> 落到安全默认（配置主页）

为什么是「祖先链」而不是「直接父进程」：

**「Steam 拉起时 shim 的直接父进程到底叫不叫 steam.exe，在这台机器上从未被
验证过。」** 2026-10-19 那次成功启动用的旧构建没有分流闸门、也没记录父进程
名；测试套件里的 steam 用例用的是改名的假 exe，不是真 Steam。

真 Steam 有可能经 steamwebhelper.exe 之类中转再拉起游戏进程。**只查直接父
进程的话，认错了就永远走配置主页，游戏根本起不来。**

所以改成：从直接父进程开始，**逐级向上**取每个祖先的映像全路径，取文件名
与白名单比较（大小写不敏感），**链上任意一级命中就算命中**。

    steam.exe -> steamwebhelper.exe -> Endfield.exe(本 shim)
                  ^^^^^^^^^^^^^^^^^^^ 命中层级=2

每一级都必须过三道闸（详见 collect_ancestor_chain 的注释）：

    1. **PID 复用防护**（每一级都做）：该祖先的创建时间必须**早于**它的下一级
       子进程（时间链严格递减）。不满足就**停止向上遍历** —— 不能把一个
       不相关的进程当成祖先。
    2. **深度上限**：parent_match_depth，默认 4（**含直接父进程**）。
    3. **有环保护**：已访问过的 pid 再次出现就停。

已终止的祖先**不参与**白名单匹配（身份已不可验证），但**可以**继续往上找它
的父进程 —— 进程对象在句柄全关之前仍然存在，NtQueryInformationProcess 照样
读得到父 pid（2026-10-20 实测）；返回 0 才说明进程已完全销毁，那时停止遍历。

**双击不会误触发**：用户从资源管理器双击 shim 时，祖先链是

    explorer.exe -> <explorer 的父进程> -> ...

实测（2026-10-20，本机）这条链里**没有 steam.exe** —— explorer.exe 的父进程
要么已经销毁，要么不可读，链在 2~3 级内就断了。因此双击仍然走配置主页。

白名单只比**文件名**且大小写不敏感（Steam 装在哪个盘、哪个目录都不影响），
默认 ["steam.exe"]。配置项 launch_source = parent / always_launch /
always_config 可以整体旁路这个判断；命令行 --force-launch / --force-config
优先级更高（给测试与排障用）。

分流决策一定写进 shim.log —— 那是用户唯一能自查的证据：

    [分流] 祖先链 = pid:41022(steamwebhelper.exe) -> pid:9004(steam.exe) -> ...
    [分流] 父进程 pid=41022 映像=... 文件名=steamwebhelper.exe 命中层级=2 ...

--------------------------------------------------------------------------
配置侧开哪个页面（config_page）
--------------------------------------------------------------------------
分流到「不是 steam 拉起」那一侧时，用户要看到的是**工具自己的页面**
（Steam终末地更新器.exe），不是 shim 里另外造的那个纯 Win32 自绘窗口。
用户实测的原话：「为什么直接启动的配置页面，不是工具的页面，而是另外一个
配置页面」。

所以默认行为是：

    配置侧 -> 先拉起工具 GUI（ShellExecuteW）-> shim 随即退出
             工具 GUI 拉不起来（文件不在 / 启动失败）
                  -> 才回落到内置 Win32 简易配置页，并**明说**自己是简易页

配置项 config_page：
    tool     （默认）同 auto
    builtin  强制用内置 Win32 简易配置页（不拉起工具 GUI）
    auto     工具 GUI 优先，失败回落内置页
坏值一律当 **tool** —— 静默变成 builtin 会在用户明确要工具 GUI 的时候
悄悄换一个功能更少的窗口。

工具 exe 的定位（配置项 tool_path 优先，其次从 shim 目录向上找）：

    1. tool_path —— shim install 时写进部署目录 shim_config.json 的绝对路径
       （shim 装在 Steam 库里，和工具所在的目录八竿子打不着，
        **只能**靠这条）
    2. tool_exe —— 上一版配置的老字段，兼容
    3. 从 shim 所在目录向上最多 5 级里的
       <祖先>\\release\\Steam终末地更新器.exe（文件名大小写不敏感）
    找到之后**记回配置**（缓存），下次不用再找。
    找不到时日志里会列出**探测过的每一个路径**。

已经在跑就不重复拉起：按 exe 的**映像全路径**匹配（不按进程名 ——
只比进程名的话别的目录下同名的东西会被误认成它）。

"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

LOG_NAME = "shim.log"
CONFIG_NAME = "shim_config.json"

DEEP_LINK_SCHEME_PREFIX = "hypergryphlauncher-"
DEEP_LINK_TAIL = "://start?ta=endfield"
# 注册表读不到时的兜底（国服应用码）
FALLBACK_SCHEME = "hypergryphlauncher-abYeZZ16BPluCFyT"
# 协议完全不可用时的次选：只打开启动器，需要人点「启动游戏」
FALLBACK_ARGS = ["--game=endfield"]

# ---------------------------------------------------------------------------
# 藏窗口的常量
# ---------------------------------------------------------------------------
HIDE_ARG = "--hide"

# ---------------------------------------------------------------------------
# 按父进程分流的常量
# ---------------------------------------------------------------------------
#
# shim 自己就叫 Endfield.exe，装在 Steam 的安装目录里。从 Steam 点「开始游戏」
# 进来时，父进程是 steam.exe；用户从资源管理器**双击**它时，父进程是
# explorer.exe —— 那种场合绝不该启动游戏，而应该打开配置界面。
#
#     父进程文件名命中白名单 -> 走现有的启动游戏逻辑（launch_mode 原样生效）
#     其它任何情况           -> 打开配置主页
#
# 认不出父进程时走配置主页（更安全的默认）：认错方向可能凭空启动一次游戏，
# 而双击的用户想要的是配置界面。
LAUNCH_SOURCE_PARENT = "parent"
LAUNCH_SOURCE_ALWAYS_LAUNCH = "always_launch"
LAUNCH_SOURCE_ALWAYS_CONFIG = "always_config"
LAUNCH_SOURCES = (LAUNCH_SOURCE_PARENT, LAUNCH_SOURCE_ALWAYS_LAUNCH,
                  LAUNCH_SOURCE_ALWAYS_CONFIG)
# 白名单**只比文件名**且大小写不敏感：Steam 装在哪个盘、哪个目录都不影响。
DEFAULT_PARENT_WHITELIST = ("steam.exe",)
# 祖先链要往上走几级。**含直接父进程**：1 = 只查直接父进程（= 老行为）。
# 默认 4：够盖住「Steam -> steamwebhelper -> onefile 引导进程 -> 本进程」
# 这种真实链路，又不至于在链条异常长时乱扫。
DEFAULT_PARENT_MATCH_DEPTH = 4
PARENT_MATCH_DEPTH_MIN, PARENT_MATCH_DEPTH_MAX = 1, 16
# 命令行强制开关。给测试与排障用，优先级**高于**父进程判断。
FORCE_CONFIG_ARG = "--force-config"
FORCE_LAUNCH_ARG = "--force-launch"
# 父进程的创建时间必须早于本进程（防 PID 复用）。留一点容差：
# 父子进程可能在同一个时钟刻度上先后创建。
PARENT_BIRTH_TOLERANCE_S = 0.01
# 完整工具（GUI 版）的 exe 名。找不到时「打开完整工具」按钮置灰。
# 文件名比对**大小写不敏感**（release\\ 里那个是「Steam终末地更新器.exe」，
# 但用户自己改名成「steam终末地更新器.exe」也应当能认出来）。
TOOL_EXE_NAME = "Steam终末地更新器.exe"
TOOL_EXE_NAME_LOWER = TOOL_EXE_NAME.lower()
# 从 shim 所在目录**向上**找工具 exe 时最多往上走几级。
# 有限深度是刻意的：全盘扫会拖慢每一次双击，还可能碰到不相干的 exe。
TOOL_SEARCH_MAX_DEPTH = 5

# ---------------------------------------------------------------------------
# 测试覆盖开关：深链 scheme 由环境变量决定
# ---------------------------------------------------------------------------
#
# 存在的唯一理由：测试里有一条路径会「故意不带任何配置」地跑 shim。
# 没有配置 -> launch_mode=auto -> 挂起父进程必然失败 -> 回落到协议深链 ->
# find_deep_link_scheme 去注册表读**真** scheme -> ShellExecuteW 打开**真**
# 协议 -> 官方启动器 + 官方 Games.exe 在用户桌面上弹出来，官方目录被写。
# 那等于「跑一次测试就动一次用户机器」。
#
# 设了这个环境变量之后：
#   * find_deep_link_scheme **直接返回它**，一个字节的注册表都不读；
#   * launch() 整条深链变成 no-op —— 什么都不执行，直接当成功返回。
#
# 为什么不设就没事：Steam 拉起 shim 时**不会**带这个变量（Steam 不设它），
# 所以生产路径的行为与改动前**逐字节一致**：没设这个变量时，
# find_deep_link_scheme 仍然读配置、仍然读注册表，launch() 的回落顺序、
# 参数拼法、ShellExecuteW 的调用方式全都一个字没动。
DEEP_LINK_OVERRIDE_ENV = "ZMD_DEEP_LINK_SCHEME"

# ---------------------------------------------------------------------------
# 配置侧（分流到「不是 steam 拉起」那一侧）开哪个页面
# ---------------------------------------------------------------------------
#
# 用户要的是**工具自己的页面**（Steam终末地更新器.exe），不是 shim 里另外
# 造的那个纯 Win32 自绘窗口。自绘窗口降级成兜底：工具 GUI 拉不起来时
# 才用它，并且必须**明说**自己是简易页。
#
#   tool    （默认）优先拉起工具 GUI；找不到/拉不起来 -> 回落内置页
#   builtin 强制用内置 Win32 配置页（不拉起工具 GUI）
#   auto    语义与 tool 一样（工具优先，失败回落），写出来是为了表意清楚
#
# 坏值一律当 tool —— **不能**静默变成 builtin：那会在用户明确要工具 GUI
# 的时候悄悄换一个窗口，而 builtin 覆盖的配置项更少。
CONFIG_PAGE_TOOL = "tool"
CONFIG_PAGE_BUILTIN = "builtin"
CONFIG_PAGE_AUTO = "auto"
CONFIG_PAGES = (CONFIG_PAGE_TOOL, CONFIG_PAGE_BUILTIN, CONFIG_PAGE_AUTO)
DEFAULT_CONFIG_PAGE = CONFIG_PAGE_TOOL

# ---------------------------------------------------------------------------
# %TEMP%\_MEI* 残留清理（onefile 的代价）
# ---------------------------------------------------------------------------
#
# 打包成 onefile 之后，PyInstaller 每次启动都会往 %TEMP% 解包一整份运行时
# （本 shim 约 19 MB，工具 GUI 约 48 MB），目录名形如 _MEI000043b02。
#
# 正常情况下 PyInstaller 会在进程退出时把它删掉。但 shim 在「挂起父进程」
# 模式下会**活到游戏退出**（Steam 那边要一直看到 shim 还在跑），而用户点
# Steam 的「停止」是**硬杀** —— PyInstaller 来不及清理，_MEI* 就留在那儿了。
# 每停一次漏一份，本机历史上清出过 2.16 GB。
#
# 所以：onefile 换来的「拷走也能用」必须配上「启动时顺手清掉陈旧残留」。
#
# 判定刻意**保守**，一条都不放宽：
#   * 只看 mtime 明显陈旧的（默认 1 小时，见 MEI_MIN_AGE_HOURS）；
#   * 正在被占用的**绝不删** —— 判据是「能不能把整个目录改名」：
#     Windows 上只要目录里有任何一个文件被进程以「拒绝删除」的共享模式打开
#     （活着的 Python 进程必然如此：python3xx.dll 被映射成映像、
#     base_library.zip 被 zipimport 握着），目录改名就会失败。
#     实测：占用中 -> os.rename 失败；
#     没人占用 -> os.rename 成功。所以「改名成功」= 「可以安全删」。
#   * 本进程自己的 _MEIPASS 永远跳过；
#   * 删失败绝不影响 shim 正常启动（全程 try/except）。
#
# 删不掉就不删 —— 宁可漏，不可把别人正在用的运行时删了。
MEI_PREFIX = "_MEI"
# 清理出来的临时名。**不能**以 _MEI 开头，否则会被自己的扫描当成残留。
MEI_TRASH_PREFIX = "_ZMDMEITRASH_"
# mtime 超过这个小时数才算「陈旧」。默认 1 小时：实测一次 glob + stat
# 全部候选只要 ~2 ms，而陈旧残留一次能回收 19~48 MB。
MEI_MIN_AGE_HOURS = 1.0
# 判定「这个目录确实是 PyInstaller 解包出来的」的标志物。
# 少一个都不算 —— 免得把别人恰好叫 _MEIxxx 的目录给删了。
MEI_MARKERS = ("python3", "base_library.zip", ".pyz")
# 配置项名（写进 shim_config.json）。
CFG_CLEANUP_MEI = "cleanup_stale_mei"
CFG_CLEANUP_MEI_AGE = "cleanup_mei_min_age_hours"

# 全部有默认值：升级后 shim_config.json 里没有这些字段也不能整段失效
DEFAULT_CONFIG = {
    # 默认**不藏**。2026-10-17 实测：从 0 秒开始藏窗口会让 Games.exe 起不来
    # （DoMainBtnClicked / cgsu 一次都没出现）。外观是锦上添花，启动是生死，
    # 所以缺省必须是最保守的那一档，要藏得显式写 true。
    "hide_launcher": False,
    "hide_ms": 60000,
    # 观察期：这段时间内**只看不藏**，等启动器把首次显示做完。
    # 实测 DoMainBtnClicked 在启动后第 3~52 秒之间出现，藏窗口必须发生在它之后。
    "hide_settle_ms": 15000,
    # 启动宽限期：从派生 hider 起算，这段时间内游戏进程没出现就判定启动失败，
    # 把藏掉的窗口还原回去。必须 > 实测的游戏启动耗时（3~52s）。
    "hide_grace_ms": 90000,
    "hide_taskbar": True,
    "hide_if_preexisting": False,
    "keep_shim_until_game": False,
    # impersonate 默认 false：伪装能否通过鉴权还没实测（当年的判定实验脚本
    # 已删除，结论见 README），
    # 缺省必须是「不开启」，否则一次配置升级就能把现役可用的深链方案弄坏。
    "impersonate": False,
    # 官服游戏目录，由部署工具探测写入；伪装模式下从这里取真正要拉起的 exe
    "game_dir": "",
    # --- 挂起父进程（launch_mode）---
    # auto：先试挂起父进程，任何一步失败就回落深链（默认，保守）
    # suspended_parent：只走挂起父进程，失败就报错弹框，**不**回落
    # deep_link：只走深链，等价于本节之前的行为
    "launch_mode": "auto",
    # 显式指定启动器根目录（<版本>\Games.exe 的上一级）。
    # 留空时按 launcher / game_dir 推导，推导不出来才扫默认安装位置。
    "launcher_root": "",
    # 游戏启动参数。None = 用 OFFICIAL_GAME_ARGS（真启动器实测值）。
    # 写成 "" = 一个参数都不传（Xel Launcher 对官服渠道就是这么做的）。
    "game_args": None,
    # 挂起父进程方案：游戏进程建好后必须**活过**这段时间，否则判定启动失败。
    # 硬上限 60s：对应「游戏进程没在 60 秒内出现」这条回落条件。
    "parent_settle_ms": 5000,
    # 挂起父进程方案：等游戏退出最多等这么久，超时就终止游戏并收尾。
    "parent_wait_ms": 600000,
    # --- 按父进程分流 ---
    # parent        = 按父进程判断（默认）：父进程是 steam.exe 就启动游戏，
    #                 其它情况（含双击、含父进程已退出）打开配置主页
    # always_launch = 永远走启动游戏那一侧（旁路父进程判断）
    # always_config = 永远走配置主页那一侧（旁路父进程判断）
    "launch_source": LAUNCH_SOURCE_PARENT,
    # 允许走「启动游戏」侧的父进程文件名。**只比文件名**，大小写不敏感。
    "parent_whitelist": list(DEFAULT_PARENT_WHITELIST),
    # 祖先链往上走几级（**含直接父进程**）。1 = 只看直接父进程（老行为）。
    # 链上任意一级命中白名单都算命中 —— 真 Steam 可能经 steamwebhelper 中转。
    "parent_match_depth": DEFAULT_PARENT_MATCH_DEPTH,
    # --- 配置侧开哪个页面 ---
    # tool（默认）/ builtin / auto。坏值一律当 tool。
    "config_page": DEFAULT_CONFIG_PAGE,
    # 完整工具（GUI 版）exe 的**绝对路径**。留空时按规则向上找，
    # 找到后记回这里（缓存），下次不用再找。
    "tool_path": "",
    # 完整工具（GUI 版）exe 的路径。**老字段**，保留只为兼容上一版配置；
    # 新代码一律用 tool_path。
    "tool_exe": "",
    # --- %TEMP%\_MEI* 残留清理（onefile 的代价）---
    # 默认**开**：onefile 每次启动都在 %TEMP% 解包 ~19 MB，用户硬杀游戏时
    # 会留下残留（历史上清出过 2.16 GB）。清不掉只是漏，清错了会毁掉
    # 别人正在跑的运行时 —— 所以判定很保守，详见上面那段注释。
    # 关掉它就把这个字段写成 false。
    CFG_CLEANUP_MEI: True,
    # 判定「陈旧」的门槛（小时）。默认 1.0。写 0 或负数按默认算。
    CFG_CLEANUP_MEI_AGE: MEI_MIN_AGE_HOURS,
}
LAUNCH_MODE_AUTO = "auto"
LAUNCH_MODE_SUSPENDED_PARENT = "suspended_parent"
LAUNCH_MODE_DEEP_LINK = "deep_link"
LAUNCH_MODES = (LAUNCH_MODE_AUTO, LAUNCH_MODE_SUSPENDED_PARENT,
                LAUNCH_MODE_DEEP_LINK)
SETTLE_MS_MIN, SETTLE_MS_MAX = 0, 60000
PWAIT_MS_MIN, PWAIT_MS_MAX = 1000, 24 * 3600 * 1000
HIDE_MS_MIN, HIDE_MS_MAX = 1000, 600000
SETTLE_MS_MIN, SETTLE_MS_MAX = 0, 300000
GRACE_MS_MIN, GRACE_MS_MAX = 1000, 900000
# 前 10 秒密集轮询：启动器在这段时间里会反复把同一个窗口 show 回来
HIDE_FAST_SECONDS = 10.0
HIDE_FAST_INTERVAL = 0.05
HIDE_SLOW_INTERVAL = 0.2
# Games.exe 的 pid 集合不用每轮窗口枚举都重算（一次要开两三百个进程句柄）。
# 一个启动器会话只起一个 Games.exe，半秒刷新足够跟上。
HIDE_RESCAN_SECONDS = 0.5

# ---------------------------------------------------------------------------
# Games.exe 伪装的常量
# ---------------------------------------------------------------------------
# 同一个二进制两种模式，靠命令行参数区分：Steam 拉起时没有这个参数，
# 伪装出来的 Games.exe 则带它。参数名必须与 updater 侧写入的保持一致。
LAUNCH_GAME_ARG = "--launch-game"
# 调度器改名后自己要 spawn 的文件名 —— SDK 只认这个名字
IMPERSONATED_EXE_NAME = "Games.exe"
# 真启动器拉起游戏时用的参数（实测 games.log 里的原样命令行）。
# 一字不改地照抄：SDK 可能对参数有额外校验，少一个都不行。
OFFICIAL_GAME_ARGS = ("-launcher_lang=zh-cn", "-launcher_sub_channel=1")
# 官服目录名。伪装出来的 Games.exe 绝不能落进去 —— 那个位置由官方启动器
# 的版本目录占用，版本号还会随更新变。撞上去等于和官方文件抢名字。
PROTECTED_DIR_NAMES = ("hypergryph launcher", "gryphlink")
# 等待子进程的上限。两个进程同步等，所以这里一个值够用。
# 取 10 分钟：必须**大于**游戏启动耗时（实测 14~20s），否则父进程先退、
# 鉴权直接空掉；同时要有限，防止子进程卡死把 Steam 永久吊住。
IMPERSONATE_WAIT_MS = 600_000

SW_HIDE = 0
SW_RESTORE = 9
SW_SHOW = 5
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Games.exe 形如 <...>\Hypergryph Launcher\<版本>\Games.exe。
# 版本目录（1.6.0 之类）会随启动器更新变成别的，只能匹配它**上一层**的固定名。
LAUNCHER_DIR_NAMES = ("hypergryph launcher", "gryphlink")
GAME_EXE_NAME = "Endfield.exe"
# 官方启动器版本目录里的那个可执行文件 —— 挂起父进程方案要的就是它
GAMES_EXE_NAME = "Games.exe"
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

# ---------------------------------------------------------------------------
# 挂起父进程用的 Win32 常量（值全部照 Win32 头文件，不凭印象写）
# ---------------------------------------------------------------------------
CREATE_SUSPENDED = 0x00000004
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x08000000
# PROC_THREAD_ATTRIBUTE_PARENT_PROCESS
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000
MAX_COMMAND_LINE = 32767
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
# SYNCHRONIZE：WaitForSingleObject 要求这个访问权
SYNCHRONIZE = 0x00100000
# CreateFileW 失败返回的不是 NULL 而是这个，必须显式判
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# ProcessBasicInformation（NtQueryInformationProcess 的 0 号信息类）
PROCESS_BASIC_INFORMATION_CLASS = 0

_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def base_dir() -> Path:
    """shim 所在的目录 —— **配置和日志都必须落在这里**。

    这是 onefile 化之后最容易出错的一处。PyInstaller 两种形态下：

        形态      sys.executable              sys._MEIPASS
        onedir    <部署目录>\\Endfield.exe     <部署目录>\\_internal
        onefile   <部署目录>\\Endfield.exe     %TEMP%\\_MEIxxxxxx

    注意 **onefile 的 sys.executable 仍然是原来那个 exe**（引导进程把
    argv[0] 传下来了），变的只有 sys._MEIPASS —— 它指向临时解包目录。

    所以「exe 所在目录」在两种形态下都是 ``Path(sys.executable).parent``，
    一个公式通吃。**绝对不能**用 sys._MEIPASS：
      * onedir 下 _MEIPASS 是 ``_internal``，它的 parent 才是部署目录；
      * onefile 下 _MEIPASS 在 %TEMP% 里，它的 parent 是 %TEMP% —— 配置
        和日志会跑到临时目录去，部署目录里既读不到配置也看不到日志。

    老实现还有一个更隐蔽的坑：它先看「exe 目录里有没有 shim_config.json」，
    没有就回落到 _MEIPASS 的 parent。onefile + 缺配置时，那个 parent 就是
    %TEMP% —— 于是配置和日志全跑到 %TEMP% 去了。现已去掉这个回落。

    非 frozen（源码直跑）时没有 sys.executable 可用，仍然按 __file__ 推。
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except (OSError, ValueError):
            return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# log() 会被后台清理线程调用，主线程也可能同时写 —— 必须串行化，
# 否则两行日志会互相插队、写出半行。
_LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with _LOG_LOCK:
            with (base_dir() / LOG_NAME).open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# %TEMP%\_MEI* 残留清理
# ---------------------------------------------------------------------------

def _self_meipass() -> Path | None:
    """本进程自己的 _MEIPASS（onefile 解包目录）。没有（onedir）返回 None。"""
    mp = getattr(sys, "_MEIPASS", None)
    if not mp:
        return None
    try:
        return Path(mp).resolve()
    except (OSError, ValueError):
        return Path(mp)


def _looks_like_mei_dir(p: Path) -> bool:
    """这个目录是不是 PyInstaller onefile 解包出来的。

    只认标志物：python3xx.dll / base_library.zip / *.pyz。
    少一个都不算 —— 免得把别人恰好叫 _MEIxxx 的目录给删了。
    """
    try:
        names = [x.name.lower() for x in p.iterdir()]
    except OSError:
        return False
    for n in names:
        for mk in MEI_MARKERS:
            if mk in n:
                return True
    return False


def _dir_size(p: Path) -> int:
    """目录里所有文件的字节总数。读不到就当 0（不影响清理成败判定）。"""
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for fn in files:
                try:
                    total += (Path(root) / fn).stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


# DeleteFile / MoveFile 用的 Win32 常量
_DELETE_ACCESS = 0x00010000
_FILE_SHARE_ALL = 0x00000007
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# 会被「映射成映像」的扩展名：活着的 Python 进程必然占着其中的 python3xx.dll
_IMAGE_SUFFIXES = (".dll", ".pyd", ".exe")


def _is_mapped_image(f: Path) -> bool:
    """这个文件是不是被某个进程**映射成映像**了（= 那个进程还活着）。

    判据：以 O_RDWR 打开它。映像映射的文件不允许别人以写方式打开
    （Windows 会直接拒绝），而空闲文件随便开。

    实测：
        被 ctypes.WinDLL 映射的 python314.dll -> O_RDWR 失败（拒绝访问）
        空闲的同名文件                        -> O_RDWR 成功
    """
    try:
        fd = os.open(str(f), os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return True          # 打不开 = 当成被占着（保守）
    try:
        os.close(fd)
    except OSError:
        pass
    return False


def _can_take_delete_access(f: Path) -> bool:
    """能不能拿到这个文件的 DELETE 权限。拿不到 = 别的进程正占着它。

    判据：CreateFileW(DELETE, FILE_SHARE_ALL)。别的进程如果以「拒绝删除」
    的共享模式开着它（Python 的 open() 就是这样），这里会拿到
    ERROR_SHARING_VIOLATION(32)。

    实测：
        被 fopen 握着的 base_library.zip -> 拿不到（err=32）
        空闲文件                         -> 拿得到
    """
    try:
        k32 = ctypes.windll.kernel32
        fn = k32.CreateFileW
    except Exception:
        return False         # 取不到 API = 无法确认 = 不删（保守）
    try:
        fn.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p]
        fn.restype = ctypes.c_void_p
    except Exception:
        pass
    try:
        h = fn(str(f), _DELETE_ACCESS, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
               _FILE_ATTRIBUTE_NORMAL, None)
    except Exception:
        return False
    if not h or h == _INVALID_HANDLE_VALUE:
        return False
    try:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        pass
    return True


def _dir_is_deletable(d: Path) -> tuple[bool, str]:
    """这个目录里的东西**现在**能不能整个删掉。

    两个探针一起用，缺一不可（实测数据见上面两个函数的注释）：

      1. 映像探针（.dll/.pyd/.exe 以 O_RDWR 打开）
         -> 抓「活着的 Python 进程把 python3xx.dll 映射成映像」这一类占用。
         **光靠目录改名抓不到这一类**（实测：映射着的 dll 照样能改名）。

      2. 删除权探针（全部文件 CreateFileW(DELETE)）
         -> 抓「别的进程以拒绝删除的共享模式开着文件」这一类占用
         （zipimport 握着的 base_library.zip 就是这一类）。

    任一探针说不 -> 整个目录**一个字节都不删**。
    """
    files: list[Path] = []
    try:
        for root, _dirs, fs in os.walk(d):
            for fn in fs:
                files.append(Path(root) / fn)
    except OSError as e:
        return False, f"读不了目录: {e}"
    for f in files:
        if f.suffix.lower() in _IMAGE_SUFFIXES and _is_mapped_image(f):
            return False, f"{f.name} 被映射成映像（那个进程还活着）"
    for f in files:
        if not _can_take_delete_access(f):
            return False, f"{f.name} 被别的进程占用"
    return True, ""


def scan_stale_mei(min_age_hours: float = MEI_MIN_AGE_HOURS,
                   tmp_dir: Path | None = None) -> list[tuple[Path, float]]:
    """列出「明显陈旧」的 _MEI* 目录。

    返回 [(目录, 年龄小时), ...]。**只读**，一个字节都不删。

    三个条件都要满足才算「陈旧」：
      1. 名字以 _MEI 开头（PyInstaller 的解包目录）；
      2. 里面有 PyInstaller 的标志物（不是别人恰好同名的目录）；
      3. mtime 超过 min_age_hours 小时。
    本进程自己的 _MEIPASS 永远排除在外。

    这里**不算目录大小** —— 算大小要遍历整棵树（1000 个文件），
    而绝大多数候选最后会被「还占着」挡掉，白算。
    """
    out: list[tuple[Path, float]] = []
    try:
        base = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
        cands = sorted(base.glob(MEI_PREFIX + "*"))
    except OSError:
        return out
    mine = _self_meipass()
    now = time.time()
    for p in cands:
        try:
            if not p.is_dir():
                continue
            rp = p.resolve()
        except OSError:
            continue
        if mine is not None:
            try:
                if rp == mine:
                    continue
            except OSError:
                pass
        try:
            age_h = (now - p.stat().st_mtime) / 3600.0
        except OSError:
            continue
        if age_h <= min_age_hours:
            continue
        if not _looks_like_mei_dir(p):
            continue
        out.append((p, age_h))
    # --- 自己留下的「隔离区」也要回收 ---------------------------------
    # purge_stale_mei 删之前会先把目录改名成 _ZMDMEITRASH_*（隔离 + 第二道
    # 占用判据）。万一进程在改名之后、rmtree 之前被硬杀（Steam 的「停止」
    # 就是硬杀），那个目录就会**永远留在 %TEMP% 里**：_MEI* 的扫描看不见
    # 它（名字不以 _MEI 开头），于是这 19~48 MB 再也没人回收 ——
    # 那等于换了个名字继续漏。
    #
    # 所以这里把**陈旧的**隔离区一起列出来。名字带本工具的专属前缀，
    # 它**只可能**是本工具自己建的，不是别人的东西 —— 不需要标志物判定。
    # 门槛同样是 min_age_hours：刚改名还没删完的（另一个实例正在删）
    # 年龄只有几秒，压根不会被列进来。
    try:
        for p in sorted(base.glob(MEI_TRASH_PREFIX + "*")):
            try:
                if not p.is_dir():
                    continue
                age_h = (now - p.stat().st_mtime) / 3600.0
            except OSError:
                continue
            if age_h <= min_age_hours:
                continue
            out.append((p, age_h))
    except OSError:
        pass
    return out


def purge_stale_mei(min_age_hours: float = MEI_MIN_AGE_HOURS,
                    tmp_dir: Path | None = None) -> dict:
    """删掉陈旧的 _MEI* 目录。返回统计结果（不抛异常）。

    三道闸，缺一不可：
      1. mtime 超过门槛（明显陈旧）；
      2. **占用探测**（_dir_is_deletable）：目录里有任何文件被占着就
         **整个目录一个字节都不删**；
      3. 改名隔离：把目录改名成 _ZMDMEITRASH_*，再整棵删掉。
         改名这一步既是「隔离」也是第二道占用判据（Windows 上目录里有
         拒绝删除的文件时，改名也会失败）。

    另外：上一轮改名后没删完的**隔离区**（_ZMDMEITRASH_*）也会被回收 ——
    否则那等于换了个名字继续漏。

    删不掉就不删 —— 宁可漏，不可把别人正在用的运行时删了。
    """
    result = {"scanned": 0, "removed": 0, "freed": 0, "skipped_busy": 0,
              "failed": 0, "elapsed_ms": 0.0}
    t0 = time.perf_counter()
    try:
        stale = scan_stale_mei(min_age_hours, tmp_dir)
    except Exception:
        stale = []
    result["scanned"] = len(stale)
    base = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    for p, age_h in stale:
        try:
            deletable, why = _dir_is_deletable(p)
        except Exception as e:
            deletable, why = False, f"探测异常 {type(e).__name__}: {e}"
        size = _dir_size(p)
        if not deletable:
            # 正在被占用 —— 绝不删
            result["skipped_busy"] += 1
            log(f"[清理] 跳过（还在用，删不掉）: {p.name}"
                f"（{age_h:.1f} 小时前，{size:,} 字节）—— {why}")
            continue
        trash = base / f"{MEI_TRASH_PREFIX}{p.name}_{os.getpid()}"
        # 已经是隔离区（上一轮改名后被硬杀留下的）-> 直接整棵删，
        # 不要再改名一次（改名没有意义，还多一次失败机会）。
        already_trash = p.name.startswith(MEI_TRASH_PREFIX)
        if not already_trash:
            try:
                os.rename(p, trash)
            except OSError as e:
                result["skipped_busy"] += 1
                log(f"[清理] 跳过（改名失败 = 还被占着）: {p.name}: {e}")
                continue
        else:
            trash = p
        try:
            shutil.rmtree(trash)
        except OSError as e:
            result["failed"] += 1
            if already_trash:
                log(f"[清理] 删除失败（隔离区留着，下次再试）: {trash.name}: {e}")
                continue
            # 改名之后没删掉 -> **改回原名**。不改回去的话，这个目录
            # 就再也没人管了（_MEI* 的扫描只认 _MEI 前缀），等于白漏。
            try:
                os.rename(trash, p)
                log(f"[清理] 删除失败（已改回原名，下次启动再试）: {p.name}: {e}")
            except OSError as e2:
                log(f"[清理] 删除失败且改回原名也失败（留待人工清理）: "
                    f"{p.name} -> {trash.name}: {e} / {e2}")
            continue
        result["removed"] += 1
        result["freed"] += size
        log(f"[清理] 已删除陈旧残留: {p.name}"
            f"（{age_h:.1f} 小时前，{size:,} 字节）")
    result["elapsed_ms"] = (time.perf_counter() - t0) * 1000.0
    return result


def cleanup_stale_mei(cfg: dict) -> None:
    """启动早期清掉 %TEMP% 里陈旧的 _MEI* 目录。

    刻意放在**后台线程**里做：Steam 拉起时用户盯着的是「正在启动」，
    不能让清理把这条链拖慢（实测扫描本身只要 ~2 ms，但删 19~48 MB 的
    目录要上百毫秒）。

    线程是 daemon 的：主流程结束进程退出时它会被砍掉，最坏情况是留下一个
    `_ZMDMEITRASH_*` 目录 —— 那不会永久残留：scan_stale_mei 也会把**陈旧的**
    隔离区列出来回收（见那里的注释）。

    **任何异常都不许冒到主流程上** —— 清理失败绝不能影响 shim 正常启动。
    """
    try:
        enabled = _as_bool(cfg.get(CFG_CLEANUP_MEI), True)
    except Exception:
        enabled = True
    if not enabled:
        log("[清理] %s=false，不清 _MEI* 残留" % CFG_CLEANUP_MEI)
        return
    try:
        age = float(cfg.get(CFG_CLEANUP_MEI_AGE, MEI_MIN_AGE_HOURS))
    except (TypeError, ValueError):
        age = MEI_MIN_AGE_HOURS
    if age <= 0:
        age = MEI_MIN_AGE_HOURS

    def _worker() -> None:
        try:
            r = purge_stale_mei(age)
            log(f"[清理] _MEI* 残留清理完成：扫描 {r['scanned']} 个，"
                f"删除 {r['removed']} 个，释放 {r['freed']:,} 字节，"
                f"跳过(还在用) {r['skipped_busy']} 个，失败 {r['failed']} 个，"
                f"耗时 {r['elapsed_ms']:.0f} ms")
        except Exception as e:                      # 绝不影响主流程
            log(f"[清理] _MEI* 清理异常（已忽略）: {type(e).__name__}: {e}")

    t = threading.Thread(target=_worker, name="zmd-mei-clean", daemon=True)
    try:
        t.start()
        log(f"[清理] 已在后台启动 _MEI* 残留清理"
            f"（门槛 {age:.1f} 小时）")
    except Exception as e:
        log(f"[清理] 起后台清理线程失败（已忽略）: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _as_bool(value, default: bool) -> bool:
    """bool 字段容错：JSON 里的 0/1、"true"/"on" 都认，认不出才用默认值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes", "on", "y"):
            return True
        if s in ("false", "0", "no", "off", "n", ""):
            return False
    return default


def load_config(cfg_dir: Path) -> dict:
    """读 shim_config.json 里的藏窗口 / 伪装设置。读不出来就整套用默认值。"""
    cfg = dict(DEFAULT_CONFIG)
    # 「配置在哪儿」是 onefile 化之后最要紧的一件事，日志里必须写清楚：
    # 读的是**exe 所在目录**下的那一份，不是 %TEMP% 里的解包目录。
    try:
        _cfg_path = Path(cfg_dir) / CONFIG_NAME
    except (TypeError, ValueError):
        _cfg_path = None
    if _cfg_path is not None and not _cfg_path.is_file():
        log(f"没有找到 {CONFIG_NAME}（在 {cfg_dir}），"
            f"用默认值继续（缺 game_dir / launcher_root 等）")
    try:
        path = cfg_dir / CONFIG_NAME
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("hide_launcher", "hide_taskbar",
                            "hide_if_preexisting", "keep_shim_until_game",
                            "impersonate", CFG_CLEANUP_MEI):
                    if key in data:
                        cfg[key] = _as_bool(data[key], DEFAULT_CONFIG[key])
                if CFG_CLEANUP_MEI_AGE in data:
                    try:
                        cfg[CFG_CLEANUP_MEI_AGE] = max(
                            0.1, float(data[CFG_CLEANUP_MEI_AGE]))
                    except (TypeError, ValueError):
                        pass            # 坏值一律用默认值
                if "hide_ms" in data:
                    ms = int(data["hide_ms"])          # 非数字会抛，由下面统一兜住
                    cfg["hide_ms"] = max(HIDE_MS_MIN, min(HIDE_MS_MAX, ms))
                if "hide_settle_ms" in data:
                    cfg["hide_settle_ms"] = max(
                        SETTLE_MS_MIN, min(SETTLE_MS_MAX, int(data["hide_settle_ms"])))
                if "hide_grace_ms" in data:
                    # 宽限期必须不短于观察期，否则刚观察完就该判失败了。
                    cfg["hide_grace_ms"] = max(
                        GRACE_MS_MIN, min(GRACE_MS_MAX, int(data["hide_grace_ms"])))
                    cfg["hide_grace_ms"] = max(cfg["hide_grace_ms"],
                                               cfg["hide_settle_ms"] + 1000)
                if "game_dir" in data:
                    # 只取字符串形式。写成数字/列表/对象都当没配，交给调用方报错，
                    # 绝不在这里悄悄拼一个路径出来 —— 那会拉起错误的 exe。
                    gd = data["game_dir"]
                    cfg["game_dir"] = gd.strip() if isinstance(gd, str) else ""
                # --- 挂起父进程相关 ---
                if "launch_mode" in data:
                    lm = data["launch_mode"]
                    lm = lm.strip().lower() if isinstance(lm, str) else ""
                    # 认不出的取值一律当 auto。静默改成 suspended_parent 会让
                    # 一次手滑直接改变启动路径，而 auto 至少还能回落。
                    cfg["launch_mode"] = (lm if lm in LAUNCH_MODES
                                          else LAUNCH_MODE_AUTO)
                for key in ("launcher_root",):
                    if key in data:
                        v = data[key]
                        cfg[key] = v.strip() if isinstance(v, str) else ""
                if "game_args" in data:
                    ga = data["game_args"]
                    # None 与 "" 语义不同：None = 用官方实测参数，"" = 一个都不传。
                    # 写成数字/列表一律当没配，绝不 str() 拼出一个奇怪命令行。
                    if ga is None:
                        cfg["game_args"] = None
                    elif isinstance(ga, str):
                        cfg["game_args"] = ga.strip()
                    elif isinstance(ga, (list, tuple)) and all(
                            isinstance(x, str) for x in ga):
                        cfg["game_args"] = list(ga)
                    else:
                        log(f"[降级] game_args={ga!r} 类型不对，按官方实测参数处理")
                if "parent_settle_ms" in data:
                    cfg["parent_settle_ms"] = max(
                        SETTLE_MS_MIN, min(SETTLE_MS_MAX,
                                           int(data["parent_settle_ms"])))
                if "parent_wait_ms" in data:
                    cfg["parent_wait_ms"] = max(
                        PWAIT_MS_MIN, min(PWAIT_MS_MAX, int(data["parent_wait_ms"])))
                # --- 按父进程分流 ---
                if "launch_source" in data:
                    ls = data["launch_source"]
                    ls = ls.strip().lower() if isinstance(ls, str) else ""
                    # 认不出的取值一律当 parent（按父进程判断）。
                    # 静默改成 always_launch 会在用户双击时凭空启动游戏 ——
                    # 那是**更危险**的方向，绝不能猜。
                    cfg["launch_source"] = (ls if ls in LAUNCH_SOURCES
                                            else LAUNCH_SOURCE_PARENT)
                if "parent_whitelist" in data:
                    wl = data["parent_whitelist"]
                    if isinstance(wl, str):
                        # 允许写成 "steam.exe, steamwebhelper.exe" 这样的字符串
                        wl = [x for x in re.split(r"[,;\s]+", wl) if x]
                    if isinstance(wl, (list, tuple)):
                        names: list[str] = []
                        for n in wl:
                            if not isinstance(n, str):
                                continue
                            n = n.strip()
                            if not n:
                                continue
                            if n.lower() in {x.lower() for x in names}:
                                continue
                            names.append(n)
                        if names:
                            cfg["parent_whitelist"] = names
                        else:
                            log("[降级] parent_whitelist 是空列表，沿用默认值")
                    else:
                        log(f"[降级] parent_whitelist={wl!r} 类型不对，沿用默认值")
                if "parent_match_depth" in data:
                    # 祖先链深度。认不出的值一律沿用默认（4）：
                    # 写大了会乱扫，写成 0/负数没有意义。
                    try:
                        d = int(data["parent_match_depth"])
                    except (TypeError, ValueError):
                        d = 0
                    if d >= PARENT_MATCH_DEPTH_MIN:
                        cfg["parent_match_depth"] = min(PARENT_MATCH_DEPTH_MAX, d)
                    else:
                        log(f"[降级] parent_match_depth="
                            f"{data['parent_match_depth']!r} 不是正整数，"
                            f"沿用默认值 {DEFAULT_PARENT_MATCH_DEPTH}")
                if "tool_exe" in data:
                    te = data["tool_exe"]
                    cfg["tool_exe"] = te.strip() if isinstance(te, str) else ""
                # --- 配置侧开哪个页面 ---
                if "config_page" in data:
                    cp = data["config_page"]
                    cp = cp.strip().lower() if isinstance(cp, str) else ""
                    # 认不出的取值一律当 tool。**静默改成 builtin 是不行的**：
                    # 那会在用户明确要工具 GUI 的时候悄悄换一个功能更少的窗口。
                    if cp in CONFIG_PAGES:
                        cfg["config_page"] = cp
                    else:
                        log(f"[降级] config_page={data['config_page']!r} 认不出，"
                            f"按 {DEFAULT_CONFIG_PAGE} 处理")
                # --- 工具 GUI 的路径 ---
                if "tool_path" in data:
                    tp = data["tool_path"]
                    if isinstance(tp, str):
                        cfg["tool_path"] = tp.strip().strip('" ')
                    else:
                        log(f"[降级] tool_path={tp!r} 类型不对，当没配")
            else:
                log("[降级] shim_config.json 不是对象，藏窗口全用默认值")
    except (OSError, ValueError, TypeError) as e:
        log(f"[降级] 读藏窗口配置失败，全用默认值: {e}")
    return cfg


# ---------------------------------------------------------------------------
# Win32 层
# ---------------------------------------------------------------------------

_WIN32 = None


def _win32():
    """首次调用时把需要的原型声明好，返回 (user32, kernel32, psapi, get_long, set_long)。

    ctypes 默认按 c_int 收返回值，64 位下会把 HWND/HANDLE 截断成 32 位，
    句柄值几乎都落在高位 —— 枚举出来全是垃圾。这里逐个声明 restype/argtypes。
    """
    global _WIN32
    if _WIN32 is not None:
        return _WIN32
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    # LONG_PTR：64 位是 8 字节。Win32 没导出 SetWindowLongPtrW 的 32 位环境要退回 SetWindowLongW
    long_ptr = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    get_long = getattr(user32, "GetWindowLongPtrW", None)
    set_long = getattr(user32, "SetWindowLongPtrW", None)
    if get_long is None or set_long is None:
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = long_ptr
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
    set_long.restype = long_ptr

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL

    _WIN32 = (user32, kernel32, psapi, get_long, set_long)
    return _WIN32


def _self_exe() -> str | None:
    """打包后的 exe 路径；源码直跑（开发/测试）返回 None。"""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable))
    return None


def _process_path(pid: int) -> str | None:
    """进程完整映像路径；打不开（已退出/权限不足）返回 None。

    用 QueryFullProcessLimitedInformation 级别的权限就够了，取的是
    映像路径而非常驻内存，别的会话里的进程也能读。
    """
    kernel32 = _win32()[1]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(32768)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                  ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        kernel32.CloseHandle(handle)


def _is_games_exe(path: str) -> bool:
    """<...>\\Hypergryph Launcher\\<版本>\\Games.exe —— 版本目录当通配符处理。"""
    try:
        p = Path(path)
        if p.name.lower() != "games.exe":
            return False
        return p.parent.parent.name.lower() in LAUNCHER_DIR_NAMES
    except (OSError, ValueError):
        return False


def _scan() -> tuple[set[int], int | None]:
    """一次进程枚举同时给出两样东西：

        (Games.exe 的 pid 集合, 游戏的 Endfield.exe pid 或 None)

    合成一次是有原因的：一次要开一千多个进程句柄，每轮都扫两遍纯属浪费。

    认游戏**只能靠路径**：shim 本尊和 --hide 分身都叫 Endfield.exe，按名字认
    会把自己当成游戏，轮询第一轮就收手，窗口反而藏不住。本 exe（分身与本尊
    同路径）一律排除，剩下的同名 exe 就是官服目录里那个。

    这里刻意**不用** Toolhelp（CreateToolhelp32Snapshot + Process32FirstW）：
    实测本机上那条路被 ACE 反作弊挡掉了，Process32FirstW 恒返回 0，而
    psapi.EnumProcesses + QueryFullProcessImageNameW 完全正常。
    少依赖一个会被拦的接口，比多一个「按父进程认亲」的值钱。
    """
    psapi = _win32()[2]
    buf = (wintypes.DWORD * 1024)()
    returned = wintypes.DWORD()
    if not psapi.EnumProcesses(buf, ctypes.sizeof(buf), ctypes.byref(returned)):
        raise OSError(ctypes.get_last_error(), "EnumProcesses 失败")

    me = os.getpid()
    self_exe = _self_exe()
    games: set[int] = set()
    game: int | None = None
    for i in range(min(returned.value, len(buf))):
        pid = int(buf[i])
        if pid <= 0:
            continue
        path = _process_path(pid)
        if not path:
            continue
        if _is_games_exe(path):
            games.add(pid)
            continue
        if game is None and pid != me \
                and os.path.basename(path).lower() == GAME_EXE_NAME.lower() \
                and not (self_exe
                         and os.path.normcase(path) == os.path.normcase(self_exe)):
            game = pid
    return games, game


def _window_title(hwnd: int) -> str:
    user32 = _win32()[0]
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _top_windows(pids: set[int]) -> list[int]:
    """pids 里那些进程的顶层主窗口 hwnd。

    三个过滤条件缺一不可：
      IsWindowVisible        —— 已经自己藏起来的就别去动它
      标题非空               —— 滤掉零尺寸的隐形辅助窗口
      GW_OWNER == NULL      —— **只要主窗口**。更新提示、错误弹窗这些模态
                               对话框 owner 是主窗口，必须留着可见，
                               否则真实错误会被一起吞掉。
    """
    out: list[int] = []
    if not pids:
        return out
    user32 = _win32()[0]

    @_ENUMPROC
    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return True
        if pid.value not in pids:
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        out.append(int(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return out


def _hide_window(hwnd: int, taskbar: bool, styled: set[int],
                 orig_style: dict[int, int]) -> None:
    win = _win32()
    user32, get_long, set_long = win[0], win[3], win[4]
    user32.ShowWindow(hwnd, SW_HIDE)
    # 记下原始 EXSTYLE —— 必须在**任何提前 return 之前**记。
    # hide_taskbar=False 时下面直接返回，但窗口已经被 SW_HIDE 藏掉了；
    # 那时若没记下来，超时就还原不了，用户又拿到一个关不掉的窗口。
    # 不用 setdefault 是为了不覆盖首次值：反复隐藏时原始值不能被写成中间态。
    if hwnd not in orig_style:
        try:
            orig_style[hwnd] = int(get_long(hwnd, GWL_EXSTYLE))
        except Exception as e:
            log(f"[降级] 读 EXSTYLE 失败 {hwnd:#x}: {e}")
            orig_style[hwnd] = 0
    if not taskbar or hwnd in styled:
        return
    # 从任务栏和 Alt-Tab 里摘掉。只做一次：改 EXSTYLE 会触发窗口重绘，
    # 每轮 50ms 都改一遍纯属和启动器打架。
    try:
        cur = int(get_long(hwnd, GWL_EXSTYLE))
        # 还原时按位恢复，不凭印象拼 —— 猜错一位就是给用户留下一个关不掉的窗口。
        # WS_EX_APPWINDOW 必须同时清掉：两者都设时 WS_EX_TOOLWINDOW 会被忽略
        # （Win32 文档明确写了这条），不清就等于没摘。
        set_long(hwnd, GWL_EXSTYLE, (cur | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        styled.add(hwnd)
    except Exception as e:                      # 摘不掉不影响「窗口已藏住」这个结果
        log(f"[降级] 改 EXSTYLE 失败 {hwnd:#x}: {e}")
        styled.add(hwnd)


def _restore_windows(touched: dict[int, int], styled: set[int]) -> int:
    """把藏过的窗口**还原**成用户能看见、能操作、能关掉的状态。

    这是整个藏窗口功能里最要紧的一段。原实现只藏不还：游戏没起来时
    用户面对的是一个 ShowWindow(SW_HIDE) + WS_EX_TOOLWINDOW 的 Games.exe ——
    不在任务栏、不在 Alt-Tab、点不到关按钮，只能 taskkill。那比不藏糟得多。

    三件事都要做，缺一个窗口就是废的：
        1. 按位把 EXSTYLE 还原（去掉 WS_EX_TOOLWINDOW，把 WS_EX_APPWINDOW 加回去）
        2. SW_RESTORE（能从最小化/隐藏状态恢复并显示）
        3. SW_SHOW 兜底

    返回成功还原的窗口数。
    """
    if not touched:
        return 0
    win = _win32()
    user32, get_long, set_long = win[0], win[3], win[4]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]

    ok = 0
    for hwnd, style in list(touched.items()):
        # 窗口可能已经随进程退出销毁，这时没什么可还原的，也不能当成失败。
        if not user32.IsWindow(hwnd):
            log(f"还原时窗口 {hwnd:#x} 已不存在，跳过")
            touched.pop(hwnd, None)
            styled.discard(hwnd)
            continue
        try:
            set_long(hwnd, GWL_EXSTYLE, int(style))
        except Exception as e:
            # EXSTYLE 还原失败就必须继续往下走 ShowWindow：哪怕任务栏上多一个
            # 入口，也比留一个看不见的窗口强。
            log(f"[降级] 还原 EXSTYLE 失败 {hwnd:#x}: {e}")
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_SHOW)
        if not user32.IsWindowVisible(hwnd):
            log(f"[降级] 还原后窗口 {hwnd:#x} 仍不可见")
            continue
        ok += 1
        log(f"已还原窗口 hwnd={hwnd:#x} 标题={_window_title(hwnd)!r}")
        touched.pop(hwnd, None)
        styled.discard(hwnd)
    return ok


# ---------------------------------------------------------------------------
# 启动链路（scheme 解析 / 深链 / ShellExecuteW）—— 已实测，不要改
# ---------------------------------------------------------------------------

def deep_link_override() -> str:
    """环境变量覆盖值。没设 / 只有空白 -> ""（= 覆盖不生效）。"""
    return (os.environ.get(DEEP_LINK_OVERRIDE_ENV) or "").strip()


def find_deep_link_scheme(cfg_dir: Path | None = None) -> tuple[str | None, str]:
    """返回 (scheme, 来源说明)。找不到返回 (None, 原因)。"""
    # 0) 测试覆盖：环境变量说了算，**注册表一个字节都不读**
    override = deep_link_override()
    if override:
        log(f"[深链] scheme 被环境变量 {DEEP_LINK_OVERRIDE_ENV} 覆盖为 {override}")
        return override, f"环境变量 {DEEP_LINK_OVERRIDE_ENV}"

    # 1) 部署时写入的配置（省一次注册表遍历）
    if cfg_dir is not None:
        cfg = cfg_dir / CONFIG_NAME
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                s = str(data.get("scheme", "")).strip()
                if s.lower().startswith(DEEP_LINK_SCHEME_PREFIX):
                    return s, "shim_config.json"
            except (OSError, ValueError):
                pass

    # 2) 注册表：启动器安装时自己注册的协议
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if name.lower().startswith(DEEP_LINK_SCHEME_PREFIX):
                    return name, "注册表 HKCR"
    except Exception as e:
        return None, f"读注册表失败: {e}"
    return None, "注册表里没有 hypergryphlauncher-* 协议"


def find_launcher(cfg_dir: Path) -> Path | None:
    """定位 Launcher.exe。**仅用于诊断与兜底**，主路径是协议 URL。"""
    cfg = cfg_dir / CONFIG_NAME
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            p = Path(str(data.get("launcher", "")).strip('" '))
            if p.is_file():
                return p
        except (OSError, ValueError):
            pass
    # 2) 常见安装位置
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if not base:
            continue
        for nm in ("Hypergryph Launcher", "GRYPHLINK"):
            p = Path(base) / nm / "Launcher.exe"
            if p.is_file():
                return p
    for drive in "CDEFGH":
        for nm in ("Hypergryph Launcher", "GRYPHLINK"):
            p = Path(f"{drive}:\\") / nm / "Launcher.exe"
            if p.is_file():
                return p
    return None


def _shell_open(target: str, params: str | None) -> int:
    return ctypes.windll.shell32.ShellExecuteW(None, "open", target, params, None, 1)


def launch(cfg_dir: Path) -> int:
    """调起官方启动器并让它直接开始游戏。"""
    scheme, how = find_deep_link_scheme(cfg_dir)

    # --- 测试覆盖（ZMD_DEEP_LINK_SCHEME）：整条深链变成 no-op ---
    #
    # 假 scheme **必然**调不通（没有对应的协议处理器），调不通就会落到下面
    # 「直接跑 Launcher.exe」那一步 —— 那会把用户机器上**真的**官方启动器
    # 拉起来，窗口弹在用户桌面上，官方目录被写。
    # 所以覆盖生效时这里直接返回成功：一个 ShellExecuteW 都不发，
    # 也不去定位 Launcher.exe。
    if deep_link_override():
        log(f"[深链] 覆盖生效（{how}，scheme={scheme}）："
            "不执行任何 ShellExecuteW，也不定位 Launcher.exe，直接返回成功")
        return 0

    if scheme:
        url = scheme + DEEP_LINK_TAIL
        log(f"协议来源: {how}  scheme={scheme}")
        log(f"用了深链: {url}")
        rc = _shell_open(url, None)
        if rc > 32:
            log(f"ShellExecuteW 成功 (hinst={rc})")
            return 0
        log(f"深链失败 rc={rc}，回落到命令行方式")
    else:
        log(f"[注意] {how}")
        log(f"[注意] 回落到内置 scheme {FALLBACK_SCHEME}")
        url = FALLBACK_SCHEME + DEEP_LINK_TAIL
        log(f"用了兜底深链: {url}")
        rc = _shell_open(url, None)
        if rc > 32:
            log(f"兜底深链成功 (hinst={rc})")
            return 0

    # 最后手段：直接跑 Launcher.exe（只会打开界面，要人点「启动游戏」）
    launcher = find_launcher(cfg_dir)
    if launcher is None:
        log("[错误] 协议深链不可用，且找不到 Launcher.exe")
        return 2
    args = " ".join(FALLBACK_ARGS)
    log(f"兜底执行: \"{launcher}\" {args}")
    rc = _shell_open(str(launcher), args)
    if rc > 32:
        log(f"ShellExecuteW 成功 (hinst={rc}) —— 注意：这种只会打开启动器，"
            f"仍需手动点「启动游戏」")
        return 0
    log(f"ShellExecuteW 失败 rc={rc}")
    return 1


# ---------------------------------------------------------------------------
# 藏窗口
# ---------------------------------------------------------------------------

def wants_hide_mode(argv) -> bool:
    """参数解析要容错：Steam 可能带一堆自己的参数过来。

    只有精确的 --hide 才切模式；未知参数一律忽略，绝不当成开关 ——
    猜错一次就会在完全不该藏的时候去藏窗口。
    """
    for arg in argv:
        if str(arg).strip().lower() == HIDE_ARG:
            return True
    return False


def spawn_hider(cfg: dict) -> int | None:
    """派生一个分离的藏窗口进程，返回它的 pid（没派生返回 None）。

    必须分离。藏窗口要轮询到游戏起来（默认 60 秒），这段时间绝不能挂在
    Steam 拉起的那条链上 —— 那会让 Steam 一直把 shim 当成正在跑的进程。
    派生放在调完深链**之后**，保证外壳窗口这件事一秒都占不到启动。
    """
    if not cfg["hide_launcher"]:
        log("hide_launcher=false，不派生藏窗口进程")
        return None
    exe = _self_exe()
    if exe is None:
        # 生产路径恒为 PyInstaller 打包。源码直跑（开发/测试）时不派生：
        # 那会立刻开始藏**这台机器上真实启动器**的窗口。
        log("非打包运行（frozen=false），不派生藏窗口进程")
        return None
    flags = DETACHED_PROCESS | CREATE_NO_WINDOW
    try:
        # CREATE_NO_WINDOW 与 DETACHED_PROCESS 同时给：后者保证没有控制台、
        # 不继承 Steam 的句柄；前者在 DETACHED_PROCESS 被丢掉的构建里兜底
        # （两者同用时前者会被系统忽略，无副作用）。
        proc = subprocess.Popen(
            [exe, HIDE_ARG],
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log(f"[降级] 派生藏窗口进程失败: {e}")
        return None
    log(f"藏窗口进程已派生 pid={proc.pid} flags={flags:#x} exe={exe}")
    return proc.pid


def hide_launcher_windows(cfg: dict) -> int:
    """藏窗口的全部实现都包在这里：任何异常都只记录，不往上抛。

    降级不是 bug，是设计：外壳窗口藏不住只是难看，游戏起不起来是生死。
    """
    try:
        return _hide_loop(cfg)
    except Exception as e:
        log(f"[降级] 藏窗口放弃: {type(e).__name__}: {e}")
        return 1


def _hide_loop(cfg: dict) -> int:
    """观察 -> 藏 -> 游戏起来就收手；宽限期内游戏没起来就把窗口还原回去。

    三段式（2026-10-17 回归的修复）：

        [0, settle)   观察期。只扫进程、记日志，**一个窗口都不动**。
        [settle, 游戏出现)  藏窗口期。
        grace 到期仍无游戏进程 -> 判定启动失败 -> 还原窗口。

    观察期是必须的：实测 DoMainBtnClicked（真正拉起游戏的那一步）出现在启动后
    第 3~52 秒，而原实现在 0 秒就开始按窗口，两次实测都把启动器按死了。
    """
    hide_ms = int(cfg["hide_ms"])
    settle_ms = int(cfg["hide_settle_ms"])
    grace_ms = int(cfg["hide_grace_ms"])
    t0 = time.monotonic()
    deadline = t0 + grace_ms / 1000.0          # 宽限期：决定「启动失败」的时刻
    hide_until = t0 + hide_ms / 1000.0         # 藏窗口只持续这么久
    settle_at = t0 + settle_ms / 1000.0        # 观察期结束、允许开始藏的时刻
    pids, game = _scan()
    next_scan = time.monotonic() + HIDE_RESCAN_SECONDS

    log(f"观察期 {settle_ms}ms（不碰窗口）→ 藏窗口至多 {hide_ms}ms → "
        f"启动宽限期 {grace_ms}ms")

    # 官方启动器本来就开着是常态。默认只按本次启动**新弹出来**的那个窗口，
    # 用户之后自己点开的启动器界面不该被顺手关掉。
    # 必须在观察期**之前**拍基线：那才是「本来就开着」的真实状态。
    baseline: set[int] = set()
    if not cfg["hide_if_preexisting"]:
        baseline = set(_top_windows(pids))
        if baseline:
            log(f"启动器已开着的窗口，本次不动: {[hex(h) for h in sorted(baseline)]}")
    else:
        log("hide_if_preexisting=true：预先就可见的窗口也会一起藏")

    logged: set[int] = set()
    styled: set[int] = set()
    touched: dict[int, int] = {}          # hwnd -> 原始 EXSTYLE，还原时要用
    hide_count = 0
    saw_game = False
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        if now >= next_scan:
            found, game = _scan()
            next_scan = now + HIDE_RESCAN_SECONDS
            if found != pids:
                log(f"Games.exe 进程: {[hex(p) for p in sorted(found)] or '未找到'}")
            pids = found

        # 游戏进程一出现就收手：此后启动器的行为全部交回给它自己，
        # 用户手动打开的启动器窗口不会被这个还在跑的轮询影响。
        if not saw_game and game:
            saw_game = True
            log(f"游戏进程已出现，停止轮询 (pid={game})")
            break

        # 观察期内绝不碰窗口。
        if now < settle_at:
            time.sleep(HIDE_SLOW_INTERVAL)
            continue
        # 过了 hide_ms 就停止隐藏，但**继续**盯到宽限期结束：
        # 游戏仍然没起来的话照样要还原窗口。
        if now >= hide_until:
            time.sleep(HIDE_SLOW_INTERVAL)
            continue

        for hwnd in _top_windows(pids):
            if hwnd in baseline:
                continue
            _hide_window(hwnd, cfg["hide_taskbar"], styled, touched)
            hide_count += 1
            if hwnd not in logged:
                logged.add(hwnd)
                user32 = _win32()[0]
                still = "（ShowWindow 后仍可见）" if user32.IsWindowVisible(hwnd) else ""
                log(f"隐藏了 hwnd={hwnd:#x} 标题={_window_title(hwnd)!r}{still}")

        # 启动器在前 10 秒里会反复把窗口 show 回来，密一点；之后放慢。
        time.sleep(HIDE_FAST_INTERVAL if (now - t0) < HIDE_FAST_SECONDS
                   else HIDE_SLOW_INTERVAL)

    if saw_game:
        log(f"藏窗口结束，共按下去 {hide_count} 次（游戏已起来，窗口保持隐藏）")
        return 0

    # 走到这里 = 宽限期到了还没等到游戏进程 = 启动失败。
    # 必须还原：用户现在要能看见这个 Games.exe 并把它关掉。
    log(f"启动失败：{grace_ms}ms 宽限期内没等到游戏进程，还原启动器窗口")
    restored = _restore_windows(touched, styled)
    if restored:
        log(f"已还原 {restored} 个启动器窗口，用户可以正常看到并关闭它")
    else:
        log(f"[降级] 没有可还原的窗口（本次共按下去 {hide_count} 次）")
    log(f"藏窗口结束，共按下去 {hide_count} 次")
    return 0


def run_hider() -> int:
    """--hide 模式：**只**藏窗口，绝不碰启动链路（深链已经在外壳进程调过了）。"""
    here = base_dir()
    cfg = load_config(here)
    log(f"--- 藏窗口模式 pid={os.getpid()} hide_ms={cfg['hide_ms']} "
        f"settle_ms={cfg['hide_settle_ms']} grace_ms={cfg['hide_grace_ms']} "
        f"taskbar={cfg['hide_taskbar']} preexisting={cfg['hide_if_preexisting']} ---")
    if not cfg["hide_launcher"]:
        log("hide_launcher=false，藏窗口模式直接退出")
        return 0
    return hide_launcher_windows(cfg)


def wait_for_game(cfg: dict) -> None:
    """keep_shim_until_game：shim 撑着不退，让 Steam 在那 14 秒里显示「运行中」。

    有上限。游戏真起不来时不能让 Steam 的界面无限期卡在运行状态。
    """
    limit = int(cfg["hide_ms"]) / 1000.0
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            _, game = _scan()
        except Exception as e:
            log(f"[降级] 等游戏进程时枚举失败，提前放弃: {e}")
            return
        if game:
            log(f"游戏进程已出现 (pid={game})，shim 可以退出")
            return
        time.sleep(0.25)
    log(f"[注意] 等游戏进程超时（{limit:.0f}s），shim 退出")


# ---------------------------------------------------------------------------
# Games.exe 伪装
#
# 只在 shim_config.json 的 impersonate=true 时启用，并且**必须先有**
# 当年那个判定实验（脚本已删除，结论见 README）的 B 组结论。
# B 组没过就开着，SDK 必然回 10100。
#
# 硬边界（写代码时就钉死，不靠运行期自觉）：
#   * 写操作**只**发生在 shim 自己所在的目录（Steam 安装目录）里。
#     官服目录 C:\\Program Files\\Hypergryph Launcher\\ 只用来读 game_dir，
#     一个字节都不写 —— 见 _assert_writable()，那里有硬拦截。
#   * 官服游戏目录只被当作**只读定位点**：取它的 Endfield.exe 路径，
#     绝不写入、移动、改名。
# ---------------------------------------------------------------------------

def _assert_writable(target: Path) -> None:
    """写入前的硬闸门：目标是官服目录就抛，绝不静默放过。

    这不是「善意提醒」，是断言。伪装方案里唯一被复制的源文件是 shim 自己
    （在 Steam 目录内），目标也在 Steam 目录内；一旦因为路径拼装出错而
    指向官服目录，宁可整个功能失败，也不能碰官方文件。
    """
    try:
        norm = os.path.normcase(os.path.abspath(str(target)))
    except (OSError, ValueError):
        return
    parts = norm.replace("/", "\\").split("\\")
    # 逐级检查任何一段是否命中受保护目录名；不只查最后一段，
    # 否则 "...\Hypergryph Launcher\1.6.0\Games.exe" 这种嵌套会被漏掉。
    if any(p.lower() in PROTECTED_DIR_NAMES for p in parts):
        raise PermissionError(
            f"[安全拦截] 拒绝写入官服目录内的路径: {target}\n"
            f"           该目录只读。伪装出来的 Games.exe 必须放在 Steam 安装目录里。")


def _same_dir(a: Path, b: Path) -> bool:
    """两个路径是不是同一个目录（解析联接 + 忽略大小写）。

    解析联接是有必要的：官服目录曾被 mklink 联接进 Steam 目录，
    不解析的话 realpath 与字面路径会被当成两个不同的地方，
    防自噬的那道闸就漏了。
    """
    try:
        return (os.path.normcase(os.path.realpath(str(a)))
                == os.path.normcase(os.path.realpath(str(b))))
    except OSError:
        return False


def wants_launch_game_mode(argv) -> bool:
    """只有精确的 --launch-game 才切模式；未知参数一律忽略。

    理由同 wants_hide_mode：猜错一次就会在不该 fork 的时候去 fork。
    Steam 可能带一堆自己的启动参数过来，不能把其中任意一个当开关。
    """
    for arg in argv:
        if str(arg).strip().lower() == LAUNCH_GAME_ARG:
            return True
    return False


def impersonate_staged(install_dir: Path, src_exe: Path | None = None) -> Path:
    """把自身 exe 复制成同目录的 Games.exe，返回目标路径。

    复制而不是改名：改完名 shim 自己就没名字了，Steam 下次点开始游戏
    直接找不到文件。复制品可以长期留着当缓存。

    已经存在且不比自身旧就跳过重拷 —— 每次点开始游戏都重拷一个几百 MB 的
    exe 是纯浪费，而内容相同的重复拷贝没有意义。

    src_exe 只给测试用（生产路径恒为 sys.executable）。
    """
    src = str(src_exe) if src_exe is not None else _self_exe()
    if not src:
        raise RuntimeError(
            "非打包运行（sys.frozen 为假），没有可复制的自身 exe。\n"
            "伪装模式只能跑打包后的 Endfield.exe；源码直跑请保持 impersonate=false。")
    src_path = Path(src)
    dst = install_dir / IMPERSONATED_EXE_NAME
    # 闸门放在最前面：哪怕调用方传错了目录，也不该等到 shutil 才发现。
    _assert_writable(dst)
    if not src_path.is_file():
        raise FileNotFoundError(f"找不到自身 exe: {src_path}")
    try:
        same = (dst.is_file()
                and dst.stat().st_size == src_path.stat().st_size
                and dst.stat().st_mtime >= src_path.stat().st_mtime)
    except OSError:
        same = False
    if same:
        log(f"Games.exe 已是最新，跳过复制: {dst}")
        return dst
    try:
        # copy2 保留时间戳；先写临时名再替换，避免中途失败留下半个 exe
        # 被下一次启动当成「已部署」。
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(str(src_path), str(tmp))
        os.replace(str(tmp), str(dst))
    except OSError as e:
        raise RuntimeError(f"复制 Games.exe 失败: {dst} <- {src_path}: {e}") from e
    log(f"已把自身复制成 Games.exe: {src_path} -> {dst}")
    return dst


def game_command_line(exe: Path, args: tuple[str, ...] | list[str] | None = None) -> str:
    """拼出拉起游戏用的命令行。

    单独抽出来是为了能对字符串做断言：参数顺序和内容都可能影响 SDK，
    测试必须能直接看到最终形态，而不是只能靠 mock 验证调用次数。

    args=None 用 OFFICIAL_GAME_ARGS（真启动器实测值）；传 () 就是一个参数都不传。
    """
    return subprocess.list2cmdline(
        [str(exe), *(OFFICIAL_GAME_ARGS if args is None else args)])


def _create_process_wait(exe: Path, args: tuple[str, ...] | list[str],
                         cwd: Path, timeout_ms: int) -> int:
    """CreateProcessW + 等待它结束，取回退出码。

    必须同步等待，原因不是「Steam 要看到退出码」这么表面：
    **父进程一旦先退出，SDK 就查不到它的映像路径**，启动鉴权会直接空掉
    （实测 filePath 变成空串）。Games.exe 必须是活着的父进程。

    提权链不能断：整条链继承本 shim 的管理员令牌，所以这里不碰
    CREATE_BREAKAWAY_FROM_JOB 之类的旗标。
    """
    import ctypes
    from ctypes import wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateProcessW.restype = wt.BOOL
    k32.WaitForSingleObject.restype = wt.DWORD
    k32.GetExitCodeProcess.restype = wt.BOOL
    k32.TerminateProcess.restype = wt.BOOL
    k32.CloseHandle.restype = wt.BOOL

    # STARTUPINFOW / PROCESS_INFORMATION 用模块级那两份（挂起父进程那边
    # 也用它们），布局完全一致，不在这里重复定义。
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    cmdline = subprocess.list2cmdline([str(exe), *args])
    log(f"CreateProcessW: {cmdline}   (cwd={cwd})")
    if not k32.CreateProcessW(None, ctypes.create_unicode_buffer(cmdline),
                              None, None, False, 0, None, str(cwd),
                              ctypes.byref(si), ctypes.byref(pi)):
        err = ctypes.get_last_error()
        # 740 = ERROR_ELEVATION_REQUIRED：链上有人不提权。
        hint = ("（WinError 740 = 需要提权。本 shim 清单必须是 "
                "requireAdministrator，且构建时带 --uac-admin）") if err == 740 else ""
        raise OSError(err, f"CreateProcessW 失败: {cmdline}{hint}")
    log(f"已拉起 pid={pi.dwProcessId} hProcess={pi.hProcess}")
    try:
        WAIT_OBJECT_0 = 0
        if k32.WaitForSingleObject(pi.hProcess, timeout_ms) != WAIT_OBJECT_0:
            log(f"[注意] 等待 pid={pi.dwProcessId} 超时（{timeout_ms}ms），终止它")
            k32.TerminateProcess(pi.hProcess, 1)
            return 3
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code)):
            return 3
        log(f"子进程 pid={pi.dwProcessId} 退出码={code.value}")
        return int(code.value)
    finally:
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)


def run_dispatcher(cfg: dict, install_dir: Path) -> int:
    """伪装模式的调度器：复制出 Games.exe，让它 fork 游戏，自己退出。"""
    log(f"伪装调度器：目录={install_dir}")
    try:
        games = impersonate_staged(install_dir)
    except (PermissionError, OSError, RuntimeError) as e:
        # 伪装建不起来时**不回落深链**：用户要的是去掉启动器这一层，
        # 静默回落只会让他以为方案生效了，实际上弹出来的还是启动器界面。
        log(f"[错误] 伪装调度器准备失败: {e}")
        return 2
    log(f"拉起 {games} {LAUNCH_GAME_ARG}")
    try:
        rc = _create_process_wait(games, (LAUNCH_GAME_ARG,), install_dir,
                                  IMPERSONATE_WAIT_MS)
    except OSError as e:
        log(f"[错误] 拉起 Games.exe 失败: {e}")
        return 2
    log(f"--- 调度器退出 rc={rc}（转发自 Games.exe）")
    return rc


def run_launcher_child(cfg: dict, install_dir: Path) -> int:
    """伪装出来的 Games.exe：拉起官服目录里真正的 Endfield.exe，自己退出。"""
    game_dir = str(cfg.get("game_dir") or "").strip()
    if not game_dir:
        # 伪装模式下 game_dir 是硬依赖。缺了就明确报错，不要回落深链。
        log("[错误] impersonate=true 但 shim_config.json 里没有 game_dir。")
        log("       重新部署并写入 game_dir: "
            "python endfield_updater.py shim install --impersonate")
        return 2
    gd = Path(game_dir)
    exe = gd / GAME_EXE_NAME
    if not exe.is_file():
        log(f"[错误] game_dir 下找不到游戏本体: {exe}")
        return 2
    # 防自噬：game_dir 若被配成 shim 自己所在的目录，拉起来的 Endfield.exe
    # 就是**本 shim**，它会再派生一个 Games.exe，无限 fork 把机器打死。
    # 配置写错时这个检查是最后一道防线，必须有。
    if _same_dir(gd, install_dir):
        log(f"[错误] game_dir 与 shim 所在目录相同: {gd}")
        log("       那会把 shim 自己再拉起来，形成无限 fork。拒绝执行。")
        return 2
    # 官服目录全程只读：这里只把它当作 exe 路径与 cwd 的来源。
    log(f"拉起官服游戏（只读该目录，不写入）: {exe}")
    log(f"命令行: {game_command_line(exe)}   工作目录={gd}")
    try:
        rc = _create_process_wait(exe, OFFICIAL_GAME_ARGS, gd,
                                  IMPERSONATE_WAIT_MS)
    except OSError as e:
        log(f"[错误] 拉起游戏失败: {e}")
        return 2
    log(f"--- Games.exe 退出 rc={rc}（转发自游戏）")
    return rc


# ---------------------------------------------------------------------------
# 按父进程分流：沿**祖先链**匹配
# ---------------------------------------------------------------------------
#
# 用户的要求（原文）：
#
#     把自己复制到 steam 对应的目录 伪装自己是游戏本体
#     如果是 steam.exe 打开 则启动游戏
#     如果不是 steam.exe 打开 则打开工具主页配置
#
# 所以 shim 起来第一件事是「我是被谁拉起来的」：
#
#     NtQueryInformationProcess(ProcessBasicInformation)
#         -> InheritedFromUniqueProcessId  ← **父进程**的 pid
#            （同一个结构里的 UniqueProcessId 是**自己**，读错它会自欺欺人）
#     OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + QueryFullProcessImageNameW
#         -> 该祖先映像的**完整路径**，取文件名比较
#
# 但**只看直接父进程是不够的**（2026-10-20 实测与推演）：
#   * 真 Steam 可能经 steamwebhelper.exe 中转再拉起游戏进程
#   * onefile 打包时 PyInstaller 的引导进程会再拉起一个**同路径**的子进程
#     来跑 Python 代码 —— 直接父进程是那个引导进程（映像名也是 Endfield.exe），
#     永远不在白名单里
#   * 「Steam 拉起时直接父进程到底叫不叫 steam.exe」**从未在真机上验证过**
#
# 所以改成沿祖先链逐级向上，**链上任意一级命中白名单就算命中**。
#
# 硬约束：**游戏侧一行都不改**。分流只是在它前面加一道闸。

class AncestorNode:
    """祖先链上的**一级**。

    每个字段都要能进日志 —— shim.log 里那几行是用户唯一能自查的证据。
    """

    __slots__ = ("level", "pid", "image", "name", "birth", "terminated",
                 "reason", "same_exe")

    def __init__(self, level: int, pid: int, image: str | None,
                 birth: float | None, terminated: bool, reason: str,
                 same_exe: bool = False) -> None:
        self.level = level
        self.pid = pid
        self.image = image
        self.name = os.path.basename(image) if image else ""
        self.birth = birth
        self.terminated = terminated
        self.reason = reason
        self.same_exe = same_exe

    @property
    def matchable(self) -> bool:
        """这一级能不能参与白名单匹配。

        四种情况**不能**参与：
          * 映像读不到 —— 没有名字可比
          * 进程已经终止 —— 身份已不可验证（且 QueryFullProcessImageNameW
            对已终止的进程实测会以 ERROR_GEN_FAILURE(31) 失败）
          * 创建时间校验没过 —— PID 被复用，这个不是真正的祖先
          * 和自己是同一个 exe —— onefile 的引导进程实例，永远不是启动者
        """
        return bool(self.image) and not self.terminated and not self.reason \
            and not self.same_exe

    def text(self) -> str:
        """日志里那一节的写法：pid:1234(name.exe)。"""
        if self.terminated:
            return f"pid:{self.pid}(<已退出>)"
        if not self.image:
            return f"pid:{self.pid}(<读不到>)"
        return f"pid:{self.pid}({self.name})"


class ParentInfo:
    """父进程识别的结果。

    每个字段都要能进日志 —— shim.log 里那几行是用户唯一能自查的证据。
    """

    __slots__ = ("pid", "image", "name", "birth", "self_birth", "reason", "ok",
                 "chain", "stop_reason", "hit_level", "hit_name")

    def __init__(self) -> None:
        self.pid: int | None = None
        self.image: str | None = None
        self.name: str = ""
        self.birth: float | None = None
        self.self_birth: float | None = None
        self.reason: str = ""
        self.ok: bool = False
        # 整条祖先链（从直接父进程开始，由近到远）
        self.chain: list[AncestorNode] = []
        # 为什么停在某一层（空串 = 走满了深度上限）
        self.stop_reason: str = ""
        # 命中白名单的那一级（level 从 1 开始；0 = 没命中）
        self.hit_level: int = 0
        self.hit_name: str = ""

    def image_text(self) -> str:
        """映像全路径；拿不到就把**原因**写出来，绝不留空。"""
        if self.image:
            return self.image
        return f"<拿不到：{self.reason or '未知原因'}>"

    def chain_text(self) -> str:
        """整条链的日志写法。"""
        if not self.chain:
            return "(读不到任何祖先)"
        return " -> ".join(n.text() for n in self.chain)


def _filetime_to_unix(ft) -> float:
    """FILETIME（100ns since 1601）-> unix 秒。"""
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return ticks / 10_000_000.0 - 11644473600.0


def _process_birth_time(handle) -> float | None:
    """进程的创建时间（unix 秒）。读不到返回 None。"""
    k32, _ = _pwin32()
    c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                               ctypes.byref(k), ctypes.byref(u)):
        return None
    return _filetime_to_unix(c)


def _self_birth_time() -> float | None:
    """本进程的创建时间。用 GetCurrentProcess 伪句柄，不必额外开句柄。"""
    k32, _ = _pwin32()
    return _process_birth_time(k32.GetCurrentProcess())


def _read_proc_state(pid: int) -> tuple[str | None, float | None, bool, str]:
    """(映像全路径, 创建时间, 是否已终止, 失败原因)。

    **只开一次句柄**同时取路径和创建时间：分两次开会给 PID 复用留时间窗 ——
    两次之间那个 pid 可能已经被系统回收再分配出去。

    「进程已退出」在这里被识别出来，而不是笼统地说「读不到」：
    进程**对象**在所有句柄都关掉之前一直存在，所以 OpenProcess 往往还能成功，
    但进程本身已经终止 —— 这时 GetExitCodeProcess 会给出非 STILL_ACTIVE 的
    退出码，而 QueryFullProcessImageNameW 会以 ERROR_GEN_FAILURE(31) 失败。
    （2026-10-20 实测确认：已终止的进程 open_ok=True、terminated=True、
      image_err=31。）
    """
    k32, _ = _pwin32()
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None, None, False, (f"OpenProcess 失败（WinError "
                                    f"{ctypes.get_last_error()}）—— 进程可能已经"
                                    f"完全销毁，或权限不足")
    try:
        birth = _process_birth_time(h)
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)) and \
                code.value != STILL_ACTIVE:
            return None, birth, True, (f"进程已退出（进程已终止，退出码 "
                                       f"{code.value}）")
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(32768)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None, birth, False, (f"QueryFullProcessImageNameW 失败"
                                        f"（WinError {ctypes.get_last_error()}）")
        image = buf.value or None
        if not image:
            return None, birth, False, "映像路径读出来是空串"
        return image, birth, False, ""
    finally:
        k32.CloseHandle(h)


def _same_image(a: str | None, b: str | None) -> bool:
    """两个映像路径是不是同一个文件（忽略大小写，解析 .. 与 .）。"""
    if not a or not b:
        return False
    try:
        return (os.path.normcase(os.path.abspath(a))
                == os.path.normcase(os.path.abspath(b)))
    except (OSError, ValueError):
        return False


def collect_ancestor_chain(depth: int = DEFAULT_PARENT_MATCH_DEPTH,
                            self_birth: float | None = None
                            ) -> tuple[list[AncestorNode], str]:
    """沿父进程链**向上**遍历，返回 (各级, 停止原因)。

    从直接父进程开始，逐级向上取每个祖先的**映像全路径**，取**文件名**与
    白名单比较。链上**任意一级**命中就算命中 —— 真 Steam 可能经
    steamwebhelper.exe 中转再拉起游戏进程；只看直接父进程的话，认错了就
    永远走配置主页，游戏根本起不来。

    三道硬闸（缺一不可）：

      1. **PID 复用防护（每一级都做）**：该祖先的创建时间必须**早于**它的
         下一级子进程（时间链严格递减）。不满足就**停止向上遍历** ——
         不能把一个不相关的进程当成祖先。
      2. **深度上限**：parent_match_depth，默认 4（**含直接父进程**）。
         链条异常长时不乱扫。
      3. **有环保护**：已访问过的 pid 再次出现就停（自指/互指的链不能死循环）。

    已终止的祖先**不参与**白名单匹配（身份已不可验证），但**可以**继续往上
    找它的父进程：进程对象在句柄全关之前仍然存在，NtQueryInformationProcess
    照样读得到父 pid（2026-10-20 实测）；返回 0 才说明进程已完全销毁，
    那时停止遍历。

    「同一个 exe 的另一个实例」（onefile 引导进程）照旧跳过：它永远不是
    启动者，不参与匹配，但链继续往上走。
    """
    if self_birth is None:
        self_birth = _self_birth_time()
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        depth = DEFAULT_PARENT_MATCH_DEPTH
    depth = max(PARENT_MATCH_DEPTH_MIN, min(PARENT_MATCH_DEPTH_MAX, depth))

    nodes: list[AncestorNode] = []
    visited: set[int] = set()
    self_exe = _self_exe()

    pid = read_parent_pid(os.getpid())
    if not pid:
        return [], ("NtQueryInformationProcess 读不到父进程 pid"
                    "（父进程已完全销毁，或返回 0）")
    # 时间基准：第一级比的是**本进程**的创建时间
    child_birth = self_birth
    stop = ""
    for level in range(1, depth + 1):
        # --- 有环保护 ---
        if pid in visited:
            stop = f"祖先链出现环（pid={pid} 重复出现），停止遍历"
            break
        visited.add(pid)
        image, birth, terminated, why = _read_proc_state(pid)
        same_exe = bool(self_exe and image and _same_image(image, self_exe))
        node = AncestorNode(level, pid, image, birth, terminated, why, same_exe)
        nodes.append(node)
        # --- PID 复用防护：时间链必须严格递减 ---
        if birth is None:
            node.reason = node.reason or "读不到创建时间，无法校验 PID 是否被复用"
            stop = node.reason
            break
        if child_birth is None:
            stop = "读不到子进程的创建时间，无法校验 PID 是否被复用"
            break
        if birth > child_birth + PARENT_BIRTH_TOLERANCE_S:
            node.reason = (f"第{level}级祖先 pid={pid} 的创建时间 {birth:.3f} "
                           f"晚于它的子进程 {child_birth:.3f} —— PID 已被复用，"
                           f"它不是真正的祖先，停止向上遍历")
            stop = node.reason
            break
        # 这一级过了时间校验：基准换成它，继续往上
        child_birth = birth
        # --- 往上一级 ---
        nxt = read_parent_pid(pid)
        if not nxt:
            stop = (f"第{level}级祖先 pid={pid} 的父进程读不到"
                    f"（NtQueryInformationProcess 返回 0 = 进程已完全销毁）")
            break
        pid = nxt
    return nodes, stop


def find_whitelist_hit(chain, whitelist) -> AncestorNode | None:
    """祖先链上**第一个**能参与匹配、且文件名命中白名单的节点。

    链是**由近到远**的：直接父进程先看，然后一级一级往上。
    找不到返回 None（= 没命中，走配置主页）。
    """
    for node in chain:
        if not node.matchable:
            continue
        if parent_name_allowed(node.name, whitelist):
            return node
    return None


def match_depth(cfg: dict) -> int:
    """这次要沿祖先链走几级。坏值一律回落到默认（4）。"""
    raw = cfg.get("parent_match_depth")
    try:
        d = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PARENT_MATCH_DEPTH
    if d < PARENT_MATCH_DEPTH_MIN:
        return DEFAULT_PARENT_MATCH_DEPTH
    return min(PARENT_MATCH_DEPTH_MAX, d)


def identify_parent(depth: int = DEFAULT_PARENT_MATCH_DEPTH) -> ParentInfo:
    """认「我是被谁拉起来的」—— 沿**祖先链**认。

    认不出来时 ok=False —— 调用方**必须**走安全默认（配置主页）。
    宁可多开一次配置界面，也不能在认错父进程时凭空启动一次游戏。

    ok 的语义 = 「**直接父进程**这一级认得出来」（老语义不变）：
    它被复用 / 已退出 / 读不到 -> ok=False。链上**更上面**的祖先照样会被
    收集并参与白名单匹配。
    """
    info = ParentInfo()
    info.self_birth = _self_birth_time()
    chain, stop = collect_ancestor_chain(depth, info.self_birth)
    info.chain = chain
    info.stop_reason = stop
    skipped = [n for n in chain if n.same_exe]
    if skipped:
        log("[分流] 跳过同 exe 的引导进程实例: "
            + ", ".join(f"{n.pid} ({n.image})" for n in skipped))
    if not chain:
        info.reason = stop or "读不到父进程"
        return info
    first = chain[0]
    info.pid = first.pid
    info.image = first.image
    info.name = first.name
    info.birth = first.birth
    if first.reason:
        info.reason = first.reason
        return info
    info.ok = True
    return info


def whitelist_names(cfg: dict) -> list[str]:
    """白名单文件名列表。空/坏配置一律回落到默认（steam.exe）。"""
    raw = cfg.get("parent_whitelist")
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,;\s]+", raw) if x]
    out: list[str] = []
    if isinstance(raw, (list, tuple)):
        for n in raw:
            if isinstance(n, str) and n.strip():
                out.append(n.strip())
    return out or list(DEFAULT_PARENT_WHITELIST)


def parent_name_allowed(name: str, whitelist) -> bool:
    """父进程文件名是否在白名单里。

    **只比文件名**，且大小写不敏感。绝不用完整路径比较：Steam 可能装在
    任意盘符、任意目录，用路径比等于把用户挡在门外。
    """
    if not name:
        return False
    low = name.lower()
    return any(low == str(w).strip().lower() for w in whitelist)


def wants_force_config(argv) -> bool:
    """只有精确的 --force-config 才生效；未知参数一律忽略。

    理由同 wants_hide_mode：Steam 可能带一堆自己的参数过来，
    不能把其中任意一个当开关。
    """
    for arg in argv:
        if str(arg).strip().lower() == FORCE_CONFIG_ARG:
            return True
    return False


def wants_force_launch(argv) -> bool:
    """只有精确的 --force-launch 才生效。"""
    for arg in argv:
        if str(arg).strip().lower() == FORCE_LAUNCH_ARG:
            return True
    return False


def decide_route(cfg: dict, argv=None) -> tuple[str, ParentInfo, str]:
    """决定走哪一侧。返回 (side, 父进程信息, 理由)。

    side = "launch" 走现有的启动游戏逻辑（launch_mode 原样生效）
    side = "config" 打开配置主页

    优先级（从高到低）：
        1. 命令行 --force-config / --force-launch（给测试与排障用）
        2. 配置 launch_source（always_launch / always_config 旁路父进程判断）
        3. 父进程判断（launch_source = parent，默认）
    """
    argv = list(argv or [])
    # 无论如何都认一次祖先链：日志里必须写清楚「我是被谁拉起来的」，
    # 哪怕这次的决定压根没用到它（旁路/强制）。
    info = identify_parent(match_depth(cfg))
    wl = whitelist_names(cfg)
    # 链上**任意一级**命中白名单就算命中（真 Steam 可能经中转进程拉起）
    hit = find_whitelist_hit(info.chain, wl)
    info.hit_level = hit.level if hit is not None else 0
    info.hit_name = hit.name if hit is not None else ""

    if wants_force_config(argv):
        return "config", info, f"命令行 {FORCE_CONFIG_ARG}：强制走配置主页"
    if wants_force_launch(argv):
        return "launch", info, f"命令行 {FORCE_LAUNCH_ARG}：强制走启动游戏"

    src = str(cfg.get("launch_source") or LAUNCH_SOURCE_PARENT).strip().lower()
    if src == LAUNCH_SOURCE_ALWAYS_LAUNCH:
        return "launch", info, "launch_source=always_launch：旁路父进程判断"
    if src == LAUNCH_SOURCE_ALWAYS_CONFIG:
        return "config", info, "launch_source=always_config：旁路父进程判断"

    if hit is not None:
        return ("launch", info,
                f"祖先链第 {hit.level} 级 {hit.name} 命中白名单 {wl}")
    if not info.ok:
        return "config", info, f"认不出父进程（{info.reason}）→ 安全默认"
    if info.stop_reason:
        return ("config", info,
                f"祖先链上没有命中白名单的名字 {wl}（{info.stop_reason}）")
    return "config", info, f"祖先链上没有命中白名单的名字 {wl}"


def log_route(info: ParentInfo, side: str, reason: str,
              whitelist) -> None:
    """把分流决策写进 shim.log。

    这是用户**唯一**能自查的证据，一行都不能省：

        [分流] 祖先链 = pid:9004(steam.exe) -> pid:41022(steamwebhelper.exe) -> ...
        [分流] 父进程 pid=41022 映像=... 文件名=steamwebhelper.exe
               命中层级=2 命中文件名=steam.exe 白名单=['steam.exe']
               匹配=命中 → 走「启动游戏」侧（...）

    整条链、命中在哪一级、走了哪一侧，全都要有。
    """
    # --- 第一行：整条祖先链 ---
    log(f"[分流] 祖先链 = {info.chain_text()}")
    if info.stop_reason:
        log(f"[分流] 祖先链遍历到此为止：{info.stop_reason}")
    match = "命中" if info.hit_level else "未命中"
    side_cn = "启动游戏" if side == "launch" else "配置主页"
    hit_txt = ""
    if info.hit_level:
        # 命中层级=1 时「文件名=」已经写的是它自己，不必重复
        hit_txt = (f" 命中层级={info.hit_level}"
                   + ("" if info.hit_level == 1
                      else f" 命中文件名={info.hit_name}"))
    log(f"[分流] 父进程 pid={info.pid if info.pid is not None else '<读不到>'} "
        f"映像={info.image_text()} 文件名={info.name or '<无>'}"
        f"{hit_txt} 白名单={list(whitelist)} 匹配={match} "
        f"→ 走「{side_cn}」侧（{reason}）")


# ---------------------------------------------------------------------------
# 配置主页（纯 Win32 自绘，不依赖 tkinter）—— **兜底**
# ---------------------------------------------------------------------------
#
# 为什么还要留着这个界面：shim 自己就叫 Endfield.exe，装在 Steam 的安装目录里。
# 用户从资源管理器双击它时父进程不是 steam.exe —— 那种场合**不该**启动游戏，
# 而应该打开一个能改配置的界面。
#
# 但它**不再是默认**：默认先拉起工具 GUI（Steam终末地更新器.exe），只有工具
# GUI 拉不起来时才轮到它，而且窗口顶部会写明「找不到工具 GUI，这里是内置
# 简易配置页」。用户要的是工具的页面，不是这个另造的窗口。
#
# 为什么不用 tkinter：shim 会被 PyInstaller 打包成 Endfield.exe 塞进 Steam
# 的安装目录。引入 tkinter 要多带一整套 tcl/tk（十几 MB），而这个窗口只用到
# 几个输入框；自绘控件更轻、启动更快，也不怕「frozen 后找不到 tcl 目录」。
#
# 控件全部用 Win32 **内置类**（STATIC/EDIT/COMBOBOX/BUTTON），不注册自定义类 ——
# 内置类在任何机器上都在。

CONFIG_WINDOW_TITLE = "Steam 终末地 shim — 配置主页"

# 控件 id
_CID_GAME_DIR = 1001
_CID_LAUNCHER = 1002
_CID_SCHEME = 1003
_CID_LAUNCH_MODE = 1004
_CID_LAUNCH_SOURCE = 1005
_CID_WHITELIST = 1006
_CID_TOOL = 1007
_CID_MATCH_DEPTH = 1008
_CID_BTN_SAVE = 1101
_CID_BTN_TEST = 1102
_CID_BTN_TOOL = 1103
_CID_BTN_CLOSE = 1104
_CID_STATUS = 1201

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
BN_CLICKED = 0
CB_ADDSTRING = 0x0143
CB_GETCURSEL = 0x0147
CB_SETCURSEL = 0x014E
CB_GETLBTEXT = 0x0148
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_BORDER = 0x00800000
ES_AUTOHSCROLL = 0x0080
CBS_DROPDOWNLIST = 0x00000002
CBS_HASSTRINGS = 0x00000008
COLOR_BTNFACE = 15
CW_USEDEFAULT = 0x80000000

# 配置主页的窗口类必须**自建**：内置类（STATIC 之类）的窗口过程是系统给的，
# 收不到 WM_COMMAND，按钮点了没反应。自建类在本机实测可正常注册
# （当年的实测脚本已随测试基础设施一起删除，结论见 README）。
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


CONFIG_WINDOW_CLASS = "ZmdShimConfigWindow"

_CFG_UI32 = None


def _cfg_ui32():
    """配置主页用到的 user32 原型。单独一个绑定器，不动 _win32()。"""
    global _CFG_UI32
    if _CFG_UI32 is not None:
        return _CFG_UI32
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
        wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UpdateWindow.argtypes = [wintypes.HWND]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.SendMessageW.restype = ctypes.c_ssize_t
    # lparam 必须是 c_void_p：CB_ADDSTRING / CB_GETLBTEXT 要往里塞字符串指针，
    # 用 LPARAM(c_ssize_t) 会直接抛 ArgumentError。
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, ctypes.c_void_p]
    user32.GetDpiForSystem.restype = wintypes.UINT
    user32.GetDpiForSystem.argtypes = []
    user32.SetProcessDPIAware.argtypes = []
    user32.SetProcessDPIAware.restype = wintypes.BOOL
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.GetSysColorBrush.restype = wintypes.HBRUSH
    user32.GetSysColorBrush.argtypes = [ctypes.c_int]
    user32.GetDlgItem.restype = wintypes.HWND
    user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    _CFG_UI32 = user32
    return user32


def _self_command() -> list[str] | None:
    """重新拉起自己一次的命令行。打包后是自身 exe，源码直跑是 python + 源文件。"""
    exe = _self_exe()
    if exe:
        return [exe]
    try:
        src = Path(__file__).resolve()
    except (NameError, OSError):
        return None
    if not src.is_file():
        return None
    return [sys.executable, str(src)]


def _tool_candidates_in(dir_path: Path) -> list[Path]:
    """某个目录里名字像工具 exe 的文件（**大小写不敏感**）。

    只认死名字 Steam终末地更新器.exe —— 不像老版本那样「release\\ 里随便
    哪个 exe 都算」。那太宽了：release\\ 里放个别的 exe 就会被当成工具拉起来。
    """
    out: list[Path] = []
    try:
        for p in sorted(dir_path.iterdir()):
            try:
                if p.is_file() and p.name.lower() == TOOL_EXE_NAME_LOWER:
                    out.append(p)
            except OSError:
                continue
    except OSError:
        return out
    return out


def search_tool_exe(cfg_dir: Path,
                    max_depth: int = TOOL_SEARCH_MAX_DEPTH
                    ) -> tuple[Path | None, list[str]]:
    """从 shim 所在目录**向上**找工具 GUI 的 exe。

    规则（就这几条，**不扫全盘**）：
        <第0..N级祖先>\\release\\Steam终末地更新器.exe
        <第0..N级祖先>\\Steam终末地更新器.exe

    文件名比对**大小写不敏感**（steam终末地更新器.exe / Steam终末地更新器.exe
    都认）。默认 N=5：够盖住「Steam 库 -> common -> 游戏目录」这种真实布局，
    又不至于在链条异常长时乱扫。

    返回 (找到的路径 or None, 探测过的路径列表)。
    """
    probed: list[str] = []
    try:
        base = Path(cfg_dir).resolve()
    except (OSError, ValueError):
        base = Path(cfg_dir)
    try:
        depth = int(max_depth)
    except (TypeError, ValueError):
        depth = TOOL_SEARCH_MAX_DEPTH
    depth = max(0, min(TOOL_SEARCH_MAX_DEPTH, depth))

    cur = base
    for _level in range(depth + 1):
        # 先看 release\\ 子目录，再看本目录本身（工具 exe 可能就躺在旁边）
        for d in (cur / "release", cur):
            probed.append(str(d))
            try:
                if not d.is_dir():
                    continue
            except OSError:
                continue
            hits = _tool_candidates_in(d)
            if hits:
                return hits[0], probed
        parent = cur.parent
        if parent == cur:          # 到盘根了
            break
        cur = parent
    return None, probed


def resolve_tool_path(cfg_dir: Path, cfg: dict | None = None
                      ) -> tuple[Path | None, str]:
    """定位工具 GUI 的 exe。返回 (路径 or None, 说明)。

    顺序（**第一个能用的就是它**）：
        1. 配置项 tool_path —— shim install 时写入的绝对路径（最可靠）
        2. 老配置项 tool_exe —— 兼容上一版配置
        3. 从 shim 所在目录向上找 release\\Steam终末地更新器.exe（有限深度）

    找不到时说明里会列出**探测过的每一个路径**，绝不只说一句「没找到」。
    """
    cfg = cfg if cfg is not None else load_config(cfg_dir)
    probes: list[str] = []
    for key in ("tool_path", "tool_exe"):
        raw = str(cfg.get(key) or "").strip().strip('" ')
        if not raw:
            continue
        p = Path(raw)
        probes.append(f"{key}={p}")
        try:
            if p.is_file():
                return p, f"配置项 {key}"
        except OSError:
            pass
        log(f"[工具GUI] 配置项 {key} 指向的文件不存在: {p}")
    hit, searched = search_tool_exe(cfg_dir)
    probes.extend(searched)
    if hit:
        return hit, "向上搜索找到"
    return None, ("没找到工具 GUI。探测过：" + "; ".join(probes))


def save_config_value(cfg_dir: Path, key: str, value) -> bool:
    """把一个键写回 shim_config.json（**保留其它键**）。失败只记录，不抛。

    用于「找到工具 GUI 之后把路径记回配置」这件事：下次就不用再找了。
    """
    path = Path(cfg_dir) / CONFIG_NAME
    data: dict = {}
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except (OSError, ValueError):
        data = {}
    if data.get(key) == value:
        return True
    data[key] = value
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as e:
        log(f"[降级] 写回配置失败（{key}）: {e}")
        return False
    log(f"[配置] 已把 {key} 写回 {path}")
    return True


def tool_gui_running(exe: Path) -> tuple[bool, int]:
    """工具 GUI 是不是已经在跑。返回 (在跑?, pid)。

    按**映像全路径**匹配（忽略大小写），不按进程名：只比进程名的话，
    别的目录下同名的东西会被误认成它，然后「已经在跑」就永远成立，
    工具 GUI 永远拉不起来。
    """
    try:
        want = os.path.normcase(os.path.abspath(str(exe)))
    except (OSError, ValueError):
        return False, 0
    try:
        psapi = _win32()[2]
    except Exception:
        return False, 0
    buf = (wintypes.DWORD * 4096)()
    returned = wintypes.DWORD()
    try:
        if not psapi.EnumProcesses(buf, ctypes.sizeof(buf), ctypes.byref(returned)):
            return False, 0
    except Exception:
        return False, 0
    for i in range(min(returned.value, len(buf))):
        pid = int(buf[i])
        if pid <= 0:
            continue
        path = _process_path(pid)
        if not path:
            continue
        try:
            got = os.path.normcase(os.path.abspath(path))
        except (OSError, ValueError):
            continue
        if got == want:
            return True, pid
    return False, 0


def _shell_open_exe(exe: Path, cwd: Path | None = None) -> int:
    """用 ShellExecuteW 拉起一个 exe。返回 HINSTANCE（>32 表示成功）。

    刻意**不用**裸 CreateProcess：ShellExecuteW 会按需处理 UAC，
    也会把工作目录设对 —— 工具 GUI 靠相对路径找同目录的东西。
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    rc = shell32.ShellExecuteW(None, "open", str(exe), None,
                               str(cwd) if cwd else None, 1)
    return int(rc) if rc else 0


def launch_tool_gui(cfg_dir: Path, cfg: dict | None = None) -> tuple[bool, str]:
    """拉起工具 GUI。返回 (成功?, 说明)。

    「成功」= 工具 GUI 已经打开了（这次拉起的，或本来就在跑）。
    找不到 / 拉不起来时返回 False，调用方据此回落到内置配置页。
    """
    exe, how = resolve_tool_path(cfg_dir, cfg)
    if exe is None:
        log(f"[工具GUI] {how}")
        return False, how
    # 已经在跑就别再拉一堆 —— 双击十次不该开十个窗口
    running, pid = tool_gui_running(exe)
    if running:
        log(f"[工具GUI] 已经在运行（pid={pid}），不重复拉起: {exe}")
        return True, f"工具 GUI 已经在运行（pid={pid}），没有重复拉起"
    try:
        rc = _shell_open_exe(exe, exe.parent)
    except Exception as e:                      # noqa: BLE001
        log(f"[工具GUI] ShellExecuteW 异常: {e}")
        return False, f"ShellExecuteW 异常：{e}"
    if rc > 32:
        log(f"[工具GUI] 已拉起（hinst={rc}）: {exe}（来源：{how}）")
        # 记回配置（缓存）：下次不用再找一遍
        save_config_value(cfg_dir, "tool_path", str(exe))
        return True, f"已拉起工具 GUI：{exe}"
    log(f"[工具GUI] ShellExecuteW 失败 rc={rc}: {exe}")
    return False, f"ShellExecuteW 失败（rc={rc}）：{exe}"


def find_tool_exe(cfg_dir: Path, cfg: dict | None = None) -> Path | None:
    """找完整工具（GUI 版）的 exe。找不到返回 None。

    **老名字**，保留是为了不打断已有调用方；实现已经统一到 resolve_tool_path
    （配置项 tool_path 优先，其次从 shim 目录向上找 release\\ 里的那个 exe）。
    """
    exe, _how = resolve_tool_path(cfg_dir, cfg)
    return exe


class _ConfigUi:
    """配置主页窗口。"""

    def __init__(self, cfg_dir: Path, info: ParentInfo | None = None):
        self.cfg_dir = Path(cfg_dir)
        self.info = info
        self.cfg = load_config(self.cfg_dir)
        self.hwnd = 0
        self.hinst = None
        self.scale = 1.0
        self.status = ""
        self.warn = ""

    # -- 尺寸换算 ------------------------------------------------------
    def px(self, v: int) -> int:
        return int(round(v * self.scale))

    def _dpi_scale(self) -> float:
        user32 = _cfg_ui32()
        try:
            dpi = int(user32.GetDpiForSystem())
        except Exception:
            dpi = 0
        if not dpi or dpi <= 0:
            dpi = 96
        return dpi / 96.0

    # -- 建控件 --------------------------------------------------------
    def _static(self, parent, text, x, y, w, h, cid=None):
        user32 = _cfg_ui32()
        return user32.CreateWindowExW(
            0, "STATIC", text, WS_CHILD | WS_VISIBLE, x, y, w, h, parent,
            ctypes.c_void_p(cid) if cid else None, self.hinst, None)

    def _edit(self, parent, cid, text, x, y, w, h=None):
        user32 = _cfg_ui32()
        h = h if h is not None else self.px(22)
        return user32.CreateWindowExW(
            0, "EDIT", text,
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER | ES_AUTOHSCROLL,
            x, y, w, h, parent, ctypes.c_void_p(cid), self.hinst, None)

    def _combo(self, parent, cid, items, current, x, y, w):
        user32 = _cfg_ui32()
        h = user32.CreateWindowExW(
            0, "COMBOBOX", "",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST | CBS_HASSTRINGS,
            x, y, w, self.px(180), parent, ctypes.c_void_p(cid), self.hinst, None)
        for it in items:
            user32.SendMessageW(
                h, CB_ADDSTRING, 0,
                ctypes.cast(ctypes.create_unicode_buffer(str(it)),
                            ctypes.c_void_p))
        try:
            idx = list(items).index(str(current))
        except ValueError:
            idx = 0
        user32.SendMessageW(h, CB_SETCURSEL, idx, 0)
        return h

    def _button(self, parent, cid, text, x, y, w, h=None):
        user32 = _cfg_ui32()
        h = h if h is not None else self.px(28)
        return user32.CreateWindowExW(
            0, "BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
            x, y, w, h, parent, ctypes.c_void_p(cid), self.hinst, None)

    def _build(self) -> None:
        user32 = _cfg_ui32()
        hwnd = self.hwnd
        x_lbl = self.px(14)
        x_ed = self.px(168)
        w_ed = self.px(478)
        y = self.px(10)

        self._static(hwnd, self.summary(), x_lbl, y, self.px(636), self.px(122))
        y += self.px(130)

        rows = (
            ("游戏目录 game_dir", _CID_GAME_DIR, str(self.cfg.get("game_dir") or "")),
            ("启动器 launcher", _CID_LAUNCHER, str(self.cfg.get("launcher") or "")),
            ("深链 scheme", _CID_SCHEME, str(self.cfg.get("scheme") or "")),
        )
        for label, cid, val in rows:
            self._static(hwnd, label, x_lbl, y + self.px(3), self.px(150), self.px(18))
            self._edit(hwnd, cid, val, x_ed, y, w_ed)
            y += self.px(28)

        self._static(hwnd, "启动方式 launch_mode", x_lbl, y + self.px(3),
                     self.px(150), self.px(18))
        self._combo(hwnd, _CID_LAUNCH_MODE, list(LAUNCH_MODES),
                    self.cfg.get("launch_mode", LAUNCH_MODE_AUTO),
                    x_ed, y, w_ed)
        y += self.px(28)

        self._static(hwnd, "分流方式 launch_source", x_lbl, y + self.px(3),
                     self.px(150), self.px(18))
        self._combo(hwnd, _CID_LAUNCH_SOURCE, list(LAUNCH_SOURCES),
                    self.cfg.get("launch_source", LAUNCH_SOURCE_PARENT),
                    x_ed, y, w_ed)
        y += self.px(28)

        self._static(hwnd, "父进程白名单", x_lbl, y + self.px(3),
                     self.px(150), self.px(18))
        self._edit(hwnd, _CID_WHITELIST,
                   ", ".join(whitelist_names(self.cfg)), x_ed, y, w_ed)
        y += self.px(28)

        self._static(hwnd, "祖先链深度", x_lbl, y + self.px(3),
                     self.px(150), self.px(18))
        self._edit(hwnd, _CID_MATCH_DEPTH,
                   str(self.cfg.get("parent_match_depth",
                                    DEFAULT_PARENT_MATCH_DEPTH)),
                   x_ed, y, w_ed)
        y += self.px(28)

        self._static(hwnd, "完整工具路径", x_lbl, y + self.px(3),
                     self.px(150), self.px(18))
        self._edit(hwnd, _CID_TOOL,
                   str(self.cfg.get("tool_path")
                       or self.cfg.get("tool_exe") or ""),
                   x_ed, y, w_ed)
        y += self.px(36)

        bx = x_lbl
        by = y
        self._button(hwnd, _CID_BTN_SAVE, "保存", bx, by, self.px(96))
        bx += self.px(104)
        self._button(hwnd, _CID_BTN_TEST, "测试启动", bx, by, self.px(96))
        bx += self.px(104)
        self._button(hwnd, _CID_BTN_TOOL, "打开完整工具", bx, by, self.px(120))
        bx += self.px(128)
        self._button(hwnd, _CID_BTN_CLOSE, "关闭", bx, by, self.px(80))
        y += self.px(36)

        self.status = ""
        self._static(hwnd, "", x_lbl, y, self.px(636), self.px(34), _CID_STATUS)

    # -- 读 / 写 -------------------------------------------------------
    def _h(self, cid) -> int:
        """按控件 id 取 HWND。控件没建出来时返回 0。"""
        user32 = _cfg_ui32()
        return int(user32.GetDlgItem(self.hwnd, cid) or 0)

    def _text(self, cid) -> str:
        user32 = _cfg_ui32()
        h = self._h(cid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(4096)
        user32.GetWindowTextW(h, buf, 4096)
        return buf.value.strip()

    def _combo_text(self, cid) -> str:
        user32 = _cfg_ui32()
        h = self._h(cid)
        if not h:
            return ""
        idx = int(user32.SendMessageW(h, CB_GETCURSEL, 0, 0))
        if idx < 0:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        # 取第 idx 项的文本：消息号要**加上索引**（CB_GETLBTEXT + i）
        user32.SendMessageW(h, CB_GETLBTEXT + idx, 256,
                            ctypes.cast(buf, ctypes.c_void_p))
        return buf.value.strip()

    def set_status(self, text: str) -> None:
        user32 = _cfg_ui32()
        self.status = text
        h = self._h(_CID_STATUS)
        if h:
            user32.SetWindowTextW(h, text)
        log(f"[配置] {text}")

    def summary(self) -> str:
        lines = []
        if _FALLBACK_NOTE:
            # 顶部第一行就说清楚「为什么不是工具 GUI」，别让用户以为
            # 这就是工具的主页。
            lines.append(_FALLBACK_NOTE)
            lines.append("")
        if self.info is not None:
            lines.append(f"父进程 pid={self.info.pid}  映像={self.info.image_text()}")
            lines.append("父进程判定：" + ("认得出" if self.info.ok
                                        else f"认不出（{self.info.reason}）"))
            lines.append("祖先链：" + self.info.chain_text())
        else:
            lines.append("父进程：未探测")
        lines.append(f"分流方式 launch_source = {self.cfg.get('launch_source')}"
                     f"    启动方式 launch_mode = {self.cfg.get('launch_mode')}")
        lines.append(f"祖先链匹配深度 parent_match_depth = "
                     f"{self.cfg.get('parent_match_depth', DEFAULT_PARENT_MATCH_DEPTH)}"
                     f"（含直接父进程；链上任意一级命中白名单都算命中）")
        gd = str(self.cfg.get("game_dir") or "")
        if not gd:
            lines.append("游戏目录：(未配置)")
        else:
            mark = "存在" if Path(gd).is_dir() else "**不存在**"
            lines.append(f"游戏目录：{gd}  [{mark}]")
        lines.append(f"配置文件：{self.cfg_dir / CONFIG_NAME}")
        return "\r\n".join(lines)

    def read_fields(self) -> dict:
        """读出界面上的值，合成一份新配置（保留不认识的旧字段）。"""
        data = dict(self.cfg)
        data["game_dir"] = self._text(_CID_GAME_DIR)
        data["launcher"] = self._text(_CID_LAUNCHER)
        data["scheme"] = self._text(_CID_SCHEME)
        lm = self._combo_text(_CID_LAUNCH_MODE)
        if lm in LAUNCH_MODES:
            data["launch_mode"] = lm
        ls = self._combo_text(_CID_LAUNCH_SOURCE)
        if ls in LAUNCH_SOURCES:
            data["launch_source"] = ls
        data["parent_whitelist"] = [
            x for x in re.split(r"[,;\s]+", self._text(_CID_WHITELIST)) if x]
        # 祖先链深度：填的不是正整数就**沿用原值**，绝不猜。
        old_depth = int(self.cfg.get("parent_match_depth")
                        or DEFAULT_PARENT_MATCH_DEPTH)
        raw = self._text(_CID_MATCH_DEPTH).strip()
        try:
            d = int(raw)
        except ValueError:
            d = 0
        if d >= PARENT_MATCH_DEPTH_MIN:
            data["parent_match_depth"] = min(PARENT_MATCH_DEPTH_MAX, d)
            self.warn = ""
        else:
            data["parent_match_depth"] = old_depth
            self.warn = (f"祖先链深度填的不是正整数（{raw!r}），"
                         f"沿用原值 {old_depth}")
        data["tool_path"] = self._text(_CID_TOOL)
        # 老字段同步一份：上一版配置用的是 tool_exe，别让它在保存时消失。
        data["tool_exe"] = data["tool_path"]
        return data

    def do_save(self) -> bool:
        data = self.read_fields()
        path = self.cfg_dir / CONFIG_NAME
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as e:
            self.set_status(f"保存失败：{e}")
            return False
        self.cfg = data
        log(f"[配置] 已保存 {path}")
        if self.warn:
            self.set_status(f"已保存到 {path}（{self.warn}）")
        else:
            self.set_status(f"已保存到 {path}")
        return True

    def do_test_launch(self) -> None:
        """按当前配置真启动一次游戏。

        做法是**重新拉起自己**并带上 --force-launch：分流闸一放行，
        后面就是**原封不动**的启动逻辑（launch_mode 照旧生效）。
        """
        if not self.do_save():
            return
        cmd = _self_command()
        if not cmd:
            self.set_status("测试启动失败：拿不到自身的可执行文件路径")
            return
        try:
            subprocess.Popen(cmd + [FORCE_LAUNCH_ARG], close_fds=True,
                            creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self.set_status(f"测试启动失败：{e}")
            return
        self.set_status("已按当前配置发起一次启动（--force-launch），"
                        "详情看 shim.log")

    def do_open_tool(self) -> None:
        # 用界面上的路径优先（用户可能刚改过），没填才按规则去找。
        typed = self._text(_CID_TOOL).strip()
        probe = dict(self.cfg)
        if typed:
            probe["tool_path"] = typed
        exe, how = resolve_tool_path(self.cfg_dir, probe)
        if exe is None:
            self.set_status(f"找不到完整工具（{TOOL_EXE_NAME}）。{how}")
            return
        ok, why = launch_tool_gui(self.cfg_dir, probe)
        if ok:
            self.set_status(why)
        else:
            self.set_status(f"调起完整工具失败：{why}")

    # -- 事件 ----------------------------------------------------------
    def on_command(self, cid: int) -> None:
        if cid == _CID_BTN_SAVE:
            self.do_save()
        elif cid == _CID_BTN_TEST:
            self.do_test_launch()
        elif cid == _CID_BTN_TOOL:
            self.do_open_tool()
        elif cid == _CID_BTN_CLOSE:
            user32 = _cfg_ui32()
            user32.DestroyWindow(self.hwnd)

    # -- 建窗口 / 消息循环 ---------------------------------------------
    def create(self) -> None:
        user32 = _cfg_ui32()
        # 先声明 DPI 感知，否则在高 DPI 屏上字会糊。
        try:
            user32.SetProcessDpiAwarenessContext = getattr(
                user32, "SetProcessDpiAwarenessContext")
            user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        self.scale = self._dpi_scale()
        self.hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        # 自建窗口类：内置类的窗口过程是系统给的，收不到 WM_COMMAND。
        wc = _WNDCLASSW()
        wc.style = 0x0003                       # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = _CONFIG_WNDPROC
        wc.hInstance = self.hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32512))
        wc.hbrBackground = user32.GetSysColorBrush(COLOR_BTNFACE)
        wc.lpszClassName = CONFIG_WINDOW_CLASS
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() != 1410:
            # 1410 = ERROR_CLASS_ALREADY_EXISTS：同名类已注册过，照样能用。
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = int(user32.CreateWindowExW(
            0, CONFIG_WINDOW_CLASS, CONFIG_WINDOW_TITLE, WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT, CW_USEDEFAULT, self.px(664), self.px(560),
            None, None, self.hinst, None))
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        _UI_BY_HWND[self.hwnd] = self
        self._build()
        user32.ShowWindow(self.hwnd, SW_SHOW)
        user32.UpdateWindow(self.hwnd)

    def run(self) -> int:
        user32 = _cfg_ui32()
        self.create()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        _UI_BY_HWND.pop(self.hwnd, None)
        return 0


_UI_BY_HWND: dict[int, "_ConfigUi"] = {}


def _config_wndproc(hwnd, msg, wparam, lparam):
    """配置主页的窗口过程。"""
    ui = _UI_BY_HWND.get(int(hwnd))
    if msg == WM_COMMAND:
        if ui is not None:
            code = int(wparam) & 0xFFFF
            notify = (int(wparam) >> 16) & 0xFFFF
            if notify == BN_CLICKED:
                ui.on_command(code)
        return 0
    if msg == WM_CLOSE:
        _cfg_ui32().DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        _cfg_ui32().PostQuitMessage(0)
        return 0
    return _cfg_ui32().DefWindowProcW(hwnd, msg, wparam, lparam)


# 必须是**模块级**对象：ctypes 的回调如果被回收，之后再触发消息就是崩溃。
_CONFIG_WNDPROC = _WNDPROC(_config_wndproc)


def open_config_page(cfg_dir: Path, info: ParentInfo | None = None) -> int:
    """打开**内置** Win32 配置主页窗口。返回进程退出码。

    这一侧**绝不**碰启动链路：不会调深链、不会建任何进程。

    它现在是**兜底**：默认（config_page=tool）先拉起工具 GUI，
    工具 GUI 拉不起来时才轮到这里。理由见 try_open_tool_page 的注释。
    """
    try:
        ui = _ConfigUi(Path(cfg_dir), info)
        return ui.run()
    except Exception as e:
        log(f"[错误] 内置配置页打开失败: {type(e).__name__}: {e}")
        return 2


# ---------------------------------------------------------------------------
# 配置侧：优先拉起工具 GUI，拉不起来才回落内置页
# ---------------------------------------------------------------------------
#
# 用户双击 shim 时想要的是**工具自己的页面**（Steam终末地更新器.exe），
# 不是 shim 里另外造的那个纯 Win32 自绘窗口。那个窗口功能更少
# （工具 GUI 里没有 launch_source / parent_whitelist / 祖先链深度这些项），
# 拿它当默认界面等于「给用户一个次品」。
#
# 但它**不能删**：工具 GUI 找不到（文件不在 / 路径变了 / 启动失败）时，
# 总得给用户一个能改配置的地方。所以它是兜底，而且必须**明说**自己是简易页。

# 内置页顶部要显示的说明。有值时说明「为什么不是工具 GUI」。
_FALLBACK_NOTE = ""


def config_page_mode(cfg: dict) -> str:
    """配置侧要开哪个页面。认不出的取值一律当 tool。

    坏值不能落成 builtin —— 那会让用户明确要工具 GUI 的时候，
    悄悄拿到一个功能更少的窗口。
    """
    raw = cfg.get("config_page")
    if isinstance(raw, str):
        raw = raw.strip().lower()
    if raw in CONFIG_PAGES:
        return raw
    return DEFAULT_CONFIG_PAGE


def try_open_tool_page(cfg_dir: Path, cfg: dict) -> bool:
    """配置侧：优先拉起工具 GUI。

    返回 True = 工具 GUI 已经打开（这次拉起的，或本来就在跑的），
    调用方**不要**再开内置页；返回 False = 走内置兜底页。
    """
    global _FALLBACK_NOTE
    mode = config_page_mode(cfg)
    if mode == CONFIG_PAGE_BUILTIN:
        _FALLBACK_NOTE = ("config_page=builtin：按配置使用内置简易配置页，"
                          "不拉起工具 GUI。")
        log(f"[配置侧] {_FALLBACK_NOTE}")
        return False
    ok, why = launch_tool_gui(cfg_dir, cfg)
    if ok:
        _FALLBACK_NOTE = ""
        log(f"[配置侧] {why}")
        return True
    _FALLBACK_NOTE = (f"找不到工具 GUI，这里是内置简易配置页（{why}）。"
                      f"完整工具的路径可以在下面填，或重新跑一次 shim install。")
    log(f"[配置侧] {_FALLBACK_NOTE}")
    return False


# ---------------------------------------------------------------------------
# 挂起父进程启动（launch_mode）
#
# 参照 Xel Launcher 的 Helpers/ParentProcessLauncher.cs + EndfieldLauncher.cs。
# 做法与硬边界见文件头那一节：官服目录全程**只读**，一个字节都不写；
# 父进程永远不 ResumeThread；任何异常路径都必须 TerminateProcess 掉它。
# ---------------------------------------------------------------------------

class SuspendedParentError(RuntimeError):
    """挂起父进程这条路走不通。

    auto 模式靠捕获**这一个**异常类型回落深链，所以别的异常（尤其是
    非本模块的意外错误）不能继承它 —— 否则一个 bug 就会被当成
    「挂起父进程不适合这台机器」而悄悄回落到会弹窗口的深链。
    """


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    """字段顺序与布局照 winnt.h：STARTUPINFO 在前，lpAttributeList 在后。"""
    _fields_ = [("StartupInfo", STARTUPINFOW),
                ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    """Psapi.h 的 PROCESS_BASIC_INFORMATION。

    两个容易搞混的字段：
        UniqueProcessId            = **自己**的 pid
        InheritedFromUniqueProcessId = **父进程**的 pid
    上一次实验就是在这儿栽的，测「父进程是不是那个 Games.exe」必须读后者。
    """
    _fields_ = [("ExitStatus", ctypes.c_long),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_size_t),
                ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_size_t),
                ("InheritedFromUniqueProcessId", ctypes.c_size_t)]


_PWIN32 = None


def _pwin32():
    """挂起父进程要用到的那批 kernel32/ntdll 原型，返回 (kernel32, ntdll)。

    单独一个绑定器而不是并进 _win32()：那里的返回值是个按位置索取的元组，
    往里插东西会牵动藏窗口那一整片代码。这里的东西自成一组，互不影响。
    """
    global _PWIN32
    if _PWIN32 is not None:
        return _PWIN32
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")

    k32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_wchar),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(PROCESS_INFORMATION)]
    k32.CreateProcessW.restype = wintypes.BOOL

    k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t)]
    k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

    k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    k32.UpdateProcThreadAttribute.restype = wintypes.BOOL

    k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k32.DeleteProcThreadAttributeList.restype = None

    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE

    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME)]
    k32.GetProcessTimes.restype = wintypes.BOOL
    # 父进程识别要拿映像全路径。这组原型原先只在 _win32() 里声明过，
    # 这里补一份：父进程那一段要**只开一次句柄**同时取路径和创建时间，
    # 分两次开会给 PID 复用留出时间窗。
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.GetCurrentProcess.argtypes = []
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL

    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long

    _PWIN32 = (k32, ntdll)
    return _PWIN32


# ---------------------------------------------------------------------------
# 定位官方 Games.exe
# ---------------------------------------------------------------------------

def version_sort_key(name: str) -> tuple[int, ...]:
    """把版本目录名变成可比较的元组，逐段按数字比大小。

    必须逐段比而不是字符串比：'1.10.0' 字符串大于 '1.9.0'，数字却是小的。
    启动器从 1.6 升到 1.10 是完全正常的事，字符串比会选错版本。
    纯数字段转 int；非数字段（Cache/CRY之类）给 -1，永远排在最后。
    """
    return tuple(int(p) if p.isdigit() else -1 for p in str(name).split("."))


def _is_reparse_point(path: Path) -> bool:
    """目录是符号链接/联接就返回 True。

    联接是有理由要排除的：官方启动器更新时会把老版本目录整个换掉，
    经联接指过去的 Games.exe 可能下一秒就不存在了 —— 拿一个随时会变的
    路径去当父进程映像，SDK 那边的判定就成了抽奖。
    """
    try:
        st = os.lstat(str(path))
    except OSError:
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or os.path.islink(str(path))


def _games_exe_under(root: Path) -> list[Path]:
    """<root>\\<版本>\\Games.exe 全找出来。只读，不写不改。"""
    out: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if not d.is_dir() or _is_reparse_point(d):
                continue
            g = d / GAMES_EXE_NAME
            if g.is_file():
                out.append(g)
        except OSError:
            continue
    return out


def _raw_config(cfg_dir: Path | None) -> dict:
    """只读地拿原始配置字典（不做任何类型转换/容错）。"""
    if cfg_dir is None:
        return {}
    p = cfg_dir / CONFIG_NAME
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def find_official_games_exe(cfg_dir: Path | None = None) -> tuple[Path | None, str]:
    """定位「版本号最高的那个」官方 Games.exe，返回 (路径, 来源说明)。

    顺序（**全部由配置推导**，自动适配非默认安装位置）：
        1. shim_config.json 的 launcher_root（显式指定，优先级最高）
        2. launcher 字段的父目录 —— 那是 Hypergryph Launcher\\ 本身
        3. game_dir 的上上级（形如 <根>\\games\\<游戏名>）
        4. 以上都**没有配置**时，才去扫 %ProgramFiles% 等默认安装位置

    第 4 条的门槛是刻意的：配置齐了还去扫默认位置，只会在测试里意外
    碰到本机真实安装的 Games.exe，然后凭空拉起一个挂起的真进程。

    版本目录会随启动器更新变（当前 1.6.0），所以只能靠扫，不能写死。
    """
    cfg = _raw_config(cfg_dir)
    roots: list[tuple[Path, str]] = []
    # 配置里到底有没有给过线索。没有线索才可以去扫默认安装位置；
    # **给了线索却推不出根目录，那是配置坏了**，必须当错误处理。
    # 这一条是安全边界，不是洁癖：game_dir 写错一个字符就退回全盘扫描，
    # 会凭空选中本机真实的 Games.exe 并拉起一个挂起的真进程 ——
    # 那是「猜」，而本函数的第一条纪律就是不猜。
    _hint_given = False

    def _add(p, why: str) -> None:
        try:
            roots.append((Path(p), why))
        except (TypeError, ValueError):
            pass

    lr = cfg.get("launcher_root")
    if isinstance(lr, str) and lr.strip():
        _add(lr.strip(), "launcher_root")
        _hint_given = True
    ln = cfg.get("launcher")
    if isinstance(ln, str) and ln.strip().strip('" '):
        _hint_given = True
        p = Path(ln.strip().strip('" '))
        if p.name.lower() == "launcher.exe":
            _add(p.parent, "launcher 的父目录")
        elif p.is_dir():
            _add(p, "launcher 指向的目录")
    gd = cfg.get("game_dir")
    if isinstance(gd, str) and gd.strip():
        _hint_given = True
        g = Path(gd.strip())
        if g.parent.name.lower() == "games":
            _add(g.parent.parent, "game_dir 的上上级")

    if not _hint_given:
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                for nm in LAUNCHER_DIR_NAMES:
                    _add(Path(base) / nm, "%%" + env + "\\" + nm)

    cands: list[tuple[tuple[int, ...], int, Path, str]] = []
    for idx, (root, why) in enumerate(roots):
        for g in _games_exe_under(root):
            cands.append((version_sort_key(g.parent.name), -idx, g, why))

    if not cands:
        if _hint_given:
            probed = ", ".join(str(r) for r, _ in roots) or "(配置给的线索推不出根目录)"
            return None, (f"配置里有 launcher/game_dir，但推不出启动器根目录，"
                          f"且这些位置下没有 <版本>\\Games.exe：{probed}")
        probed = ", ".join(str(r) for r, _ in roots) or "(没有任何可探测的位置)"
        return None, f"这些位置下没有 <版本>\\Games.exe：{probed}"
    # 版本号高的赢；同版本时取配置里靠前的那个根（上面的 -idx 就是为此）
    best = max(cands, key=lambda t: (t[0], t[1]))
    seen = sorted({f"{g.parent.name}" for _k, _i, g, _w in cands})
    return best[2], (f"{best[3]}，版本 {best[2].parent.name}"
                     f"（候选版本 {seen}）")


def resolve_game_args(cfg: dict) -> tuple[str, ...]:
    """这次拉起游戏要传什么参数。

    默认 OFFICIAL_GAME_ARGS —— 那是本机 games.log 里抓到的真启动器命令行，
    已实测能过鉴权。shim_config.json 的 game_args 可以覆盖：
        "game_args": ""    -> 一个参数都不传（Xel 对官服渠道就是这么做的）
        "game_args": null  -> 回到默认
    """
    ga = cfg.get("game_args", None)
    if ga is None:
        return OFFICIAL_GAME_ARGS
    if isinstance(ga, str):
        return tuple(a for a in ga.split() if a) if ga.strip() else ()
    if isinstance(ga, (list, tuple)):
        return tuple(str(a) for a in ga)
    return OFFICIAL_GAME_ARGS


# ---------------------------------------------------------------------------
# Win32 原语
# ---------------------------------------------------------------------------

def _open_image_read_lock(games_exe: Path) -> int:
    """拿一个「只允许别人读」的共享读锁，挡住启动器升级把 Games.exe 换掉。

    共享模式只给 FILE_SHARE_READ：别人能读，**不能写也不能改名**。
    启动器升级做的正是「替换 Games.exe」，不锁住的话完全可能锁了个寂寞。
    作用域和 Xel 的 using var imageLock 一致：只覆盖「定位文件 -> 建好子进程」。
    """
    k32, _ = _pwin32()
    h = k32.CreateFileW(str(games_exe), GENERIC_READ, FILE_SHARE_READ, None,
                        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == INVALID_HANDLE_VALUE:
        raise SuspendedParentError(
            f"打开 Games.exe 的共享读锁失败（WinError {ctypes.get_last_error()}）: "
            f"{games_exe}")
    return int(h)


def _close_handle(h) -> None:
    try:
        if h:
            _pwin32()[0].CloseHandle(h)
    except Exception:                       # 关句柄失败不影响任何结论
        pass


def _create_suspended_parent(games_exe: Path) -> PROCESS_INFORMATION:
    """CreateProcessW(..., CREATE_SUSPENDED) 建一个**永远不恢复**的 Games.exe。

    命令行只给引号包起来的 exe 路径，不带任何参数（和 Xel 一致）。
    返回的 hThread 用不上 —— 永远不 ResumeThread —— 由调用方立刻关掉；
    留着它只会让人以为可以恢复它。
    """
    k32, _ = _pwin32()
    cmd = f'"{games_exe}"'
    if len(cmd) >= MAX_COMMAND_LINE:
        raise SuspendedParentError(f"Games.exe 路径过长（{len(cmd)} 字符）")
    if '"' in str(games_exe) or "\0" in str(games_exe):
        raise SuspendedParentError(f"Games.exe 路径含无效字符: {games_exe!r}")
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    log(f"CreateProcessW(Games.exe, CREATE_SUSPENDED) cwd={games_exe.parent}")
    if not k32.CreateProcessW(str(games_exe),
                              ctypes.cast(ctypes.create_unicode_buffer(cmd),
                                          ctypes.POINTER(ctypes.c_wchar)),
                              None, None, False, CREATE_SUSPENDED, None,
                              str(games_exe.parent),
                              ctypes.byref(si), ctypes.byref(pi)):
        err = ctypes.get_last_error()
        hint = ("（WinError 740 = 需要提权）" if err == 740 else "")
        raise SuspendedParentError(
            f"创建挂起的 Games.exe 失败（WinError {err}）{hint}: {games_exe}")
    return pi


def _create_child_with_parent(parent_handle: int, exe: Path,
                              args: tuple[str, ...], cwd: Path) -> PROCESS_INFORMATION:
    """把游戏拉起来，并指定 parent_handle 为它的**直接父进程**。

    三步缺一不可：
        InitializeProcThreadAttributeList -> UpdateProcThreadAttribute(PARENT_PROCESS)
        -> CreateProcessW(EXTENDED_STARTUPINFO_PRESENT)

    STARTUPINFOEX 的 cb 必须等于 sizeof(STARTUPINFOEX)（不是 sizeof(STARTUPINFO)），
    写小了 CreateProcessW 会直接失败 —— 这是 Xel 里 `Size = sizeof<StartupInfoEx>()`
    那行的用意，照抄。
    """
    k32, _ = _pwin32()
    cmd = game_command_line(exe, args)
    if len(cmd) >= MAX_COMMAND_LINE:
        raise SuspendedParentError(f"游戏命令行过长（{len(cmd)} 字符）")

    size = ctypes.c_size_t(0)
    # 第一遍：拿属性列表要多大。这一遍**必须**失败（传的是 NULL），
    # 它失败时把需要的字节数写进 size。
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise SuspendedParentError(
            "InitializeProcThreadAttributeList 查询大小失败"
            f"（WinError {ctypes.get_last_error()}）")
    buf = (ctypes.c_byte * size.value)()
    plist = ctypes.cast(buf, ctypes.c_void_p)
    if not k32.InitializeProcThreadAttributeList(plist, 1, 0, ctypes.byref(size)):
        raise SuspendedParentError(
            "InitializeProcThreadAttributeList 初始化失败"
            f"（WinError {ctypes.get_last_error()}）")
    try:
        handle_slot = ctypes.c_void_p(int(parent_handle))
        if not k32.UpdateProcThreadAttribute(
                plist, 0, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                ctypes.cast(ctypes.byref(handle_slot), ctypes.c_void_p),
                ctypes.sizeof(ctypes.c_void_p), None, None):
            raise SuspendedParentError(
                "UpdateProcThreadAttribute(PARENT_PROCESS) 失败"
                f"（WinError {ctypes.get_last_error()}）")
        sie = STARTUPINFOEXW()
        sie.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        sie.lpAttributeList = plist
        pi = PROCESS_INFORMATION()
        log(f"CreateProcessW(游戏, 父进程=挂起的 Games.exe) {cmd}   (cwd={cwd})")
        if not k32.CreateProcessW(str(exe),
                                  ctypes.cast(ctypes.create_unicode_buffer(cmd),
                                              ctypes.POINTER(ctypes.c_wchar)),
                                  None, None, False,
                                  EXTENDED_STARTUPINFO_PRESENT
                                  | CREATE_UNICODE_ENVIRONMENT, None, str(cwd),
                                  ctypes.byref(sie), ctypes.byref(pi)):
            err = ctypes.get_last_error()
            hint = ("（WinError 740 = 需要提权）" if err == 740 else "")
            raise SuspendedParentError(
                f"创建游戏进程失败（WinError {err}）{hint}: {cmd}")
        return pi
    finally:
        # 属性列表必须在 CreateProcessW 之后就失效 —— 里面的父进程句柄
        # 只是创建时用一次，游戏跑起来之后靠内核里的父子关系维持。
        k32.DeleteProcThreadAttributeList(plist)


def read_parent_pid(pid: int) -> int | None:
    """NtQueryInformationProcess 读**父进程** pid；读不到返回 None。

    返回的是 InheritedFromUniqueProcessId。同一个结构里还有个
    UniqueProcessId，那是**自己**的 pid —— 断言「父进程是不是那个挂起的
    Games.exe」时读错这一个，就会拿自己的 pid 去比，然后莫名其妙地通过。
    """
    k32, ntdll = _pwin32()
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        pbi = _PROCESS_BASIC_INFORMATION()
        retlen = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            h, PROCESS_BASIC_INFORMATION_CLASS, ctypes.byref(pbi),
            ctypes.sizeof(pbi), ctypes.byref(retlen))
        if status != 0:                 # NTSTATUS != STATUS_SUCCESS
            return None
        return int(pbi.InheritedFromUniqueProcessId)
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


def process_cpu_seconds(handle) -> float:
    """进程累计占用的 CPU 秒数（内核 + 用户），读不到返回 -1.0。

    这是「挂起的父进程**从未被恢复**」的可测证据：一个真跑起来的 Qt 程序
    在几秒内不可能还是 0.00。-1.0 表示读不到，不当成 0。
    """
    k32, _ = _pwin32()
    c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e),
                               ctypes.byref(k), ctypes.byref(u)):
        return -1.0
    ticks = (k.dwHighDateTime << 32 | k.dwLowDateTime) \
        + (u.dwHighDateTime << 32 | u.dwLowDateTime)
    return ticks / 10_000_000.0


def hide_own_console() -> bool:
    """把自己的控制台窗口按下去，返回是否真的藏了。

    只在 sys.frozen 时才动手：源码直跑（开发/测试）时 GetConsoleWindow()
    返回的是**调用者那个**终端，按下去等于把用户正在看的窗口弄没。
    这是和 _self_exe() 同一个理由：只有打包后的 exe 才是真正的部署产物。
    没有控制台（GetConsoleWindow 返回 NULL）也什么都不做。
    """
    if not getattr(sys, "frozen", False):
        return False
    if os.environ.get("ZMD_SHIM_KEEP_CONSOLE") == "1":
        log("ZMD_SHIM_KEEP_CONSOLE=1，不藏自己的控制台窗口")
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetConsoleWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        hwnd = user32.GetConsoleWindow()
        if not hwnd:
            log("没有自己的控制台窗口（GetConsoleWindow 返回 NULL），跳过")
            return False
        user32.ShowWindow(hwnd, SW_HIDE)
        log(f"已隐藏自己的控制台窗口 hwnd={hwnd}")
        return True
    except Exception as e:
        log(f"[降级] 隐藏控制台窗口失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 挂起的父进程：生命周期
# ---------------------------------------------------------------------------

_LIVE_PARENTS: "list[SuspendedParent]" = []


def _terminate_all_parents(reason: str) -> None:
    """兜底：把还活着的挂起父进程全部终结掉。

    run_suspended_parent 的 finally 已经会收尾；这里是第二道网，专防那些
    绕开 finally 的退路（os._exit、未捕获的 Ctrl+C）。留着挂死的 Games.exe
    在任务管理器里比什么都糟 —— 用户会以为官方启动器卡死了。
    """
    for p in list(_LIVE_PARENTS):
        try:
            p.terminate(reason)
        except Exception:
            pass


def _install_parent_guard() -> None:
    import atexit
    atexit.register(_terminate_all_parents, "进程退出兜底")

    def _on_sigint(_signum, _frame):
        _terminate_all_parents("收到 Ctrl+C")
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_sigint)
        except (ValueError, OSError, AttributeError):
            # 非主线程里 signal.signal 会抛；那种情况下靠 atexit 兜底即可
            pass


class SuspendedParent:
    """持有一个挂起的 Games.exe，游戏退出（或自己退出）时终结它。

    对应 Xel 的 SuspendedParentLifetime + ParentProcessLauncher 两个类。
    做成上下文管理器是为了让「哪条路径都能收干净」这件事由语法保证：
    异常、提前 return、正常结束，走的都是同一个 __exit__。
    """

    def __init__(self, games_exe: Path):
        self.games_exe = games_exe
        self.hProcess: int | None = None
        self.pid = 0
        self.terminated = False
        self.closed = False
        pi = _create_suspended_parent(games_exe)
        self.hProcess = int(pi.hProcess)
        self.pid = int(pi.dwProcessId)
        # 主线程句柄永远用不上：父进程**永不恢复**。留着它只会诱导
        # 有人去 ResumeThread，那等于把启动器界面放出来。
        _close_handle(pi.hThread)
        _LIVE_PARENTS.append(self)
        log(f"挂起的 Games.exe 已创建 pid={self.pid}（本进程**永不恢复**它）")

    # -- 观测（测试与诊断用，不改任何状态）--------------------------------
    def cpu_seconds(self) -> float:
        return -1.0 if self.closed else process_cpu_seconds(self.hProcess)

    def alive(self) -> bool:
        if self.closed or not self.hProcess:
            return False
        k32, _ = _pwin32()
        return k32.WaitForSingleObject(self.hProcess, 0) == WAIT_TIMEOUT

    # -- 收尾 ------------------------------------------------------------
    def terminate(self, reason: str = "") -> bool:
        """终结挂起的父进程。幂等：已经收过了就直接返回 False。

        TerminateProcess 而不是等它自己退出：它**永远**不会自己退出，
        而且 SDK 只在父进程活着的时候查得到它的映像路径，所以宁可
        游戏已经结束、父进程多活 0.1ms，也不要先杀父进程再管游戏。
        """
        if self.closed or not self.hProcess:
            return False
        k32, _ = _pwin32()
        ok = bool(k32.TerminateProcess(self.hProcess, 0))
        self.terminated = True
        _close_handle(self.hProcess)
        self.hProcess = None
        self.closed = True
        try:
            _LIVE_PARENTS.remove(self)
        except ValueError:
            pass
        log(f"挂起的 Games.exe pid={self.pid} 已终结 "
            f"({'成功' if ok else '失败，可能已退出'}）{('：' + reason) if reason else ''}")
        return ok

    def __enter__(self) -> "SuspendedParent":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # 无论正常、异常、还是还没拉起游戏就失败，都必须收掉它，
        # 否则会在任务管理器里留下一个永远挂着、谁也关不掉的 Games.exe。
        if not self.closed:
            self.terminate("离开作用域（异常路径）" if exc_type else "")
        return False


# ---------------------------------------------------------------------------
# 挂起父进程启动主流程
# ---------------------------------------------------------------------------

def run_suspended_parent(cfg: dict, cfg_dir: Path) -> int:
    """用挂起的官方 Games.exe 当父进程，把游戏直接拉起来。

    返回**游戏的退出码**（Steam 那边就靠它判断游戏结束了）。
    任何一步走不通都抛 SuspendedParentError，交给调用方决定是回落还是报错。
    """
    game_dir_s = str(cfg.get("game_dir") or "").strip()
    if not game_dir_s:
        raise SuspendedParentError(
            "shim_config.json 里没有 game_dir，挂起父进程方案必须知道游戏本体在哪。\n"
            "       重新部署并写入 game_dir: python endfield_updater.py shim install")
    gd = Path(game_dir_s)
    exe = gd / GAME_EXE_NAME
    if not exe.is_file():
        raise SuspendedParentError(f"game_dir 下找不到游戏本体: {exe}")
    # 防自噬：game_dir 若被配成 shim 自己所在目录，父进程就选到了 shim 自己的
    # 副本，SDK 那边的映像路径永远对不上。
    if _same_dir(gd, cfg_dir):
        raise SuspendedParentError(
            f"game_dir 与 shim 所在目录相同: {gd}\n"
            "       会把 shim 自己当成父进程，拒绝执行。")

    games, how = find_official_games_exe(cfg_dir)
    if games is None:
        raise SuspendedParentError(f"找不到官方 Games.exe —— {how}")
    log(f"官方 Games.exe: {games}（{how}）")

    args = resolve_game_args(cfg)
    settle_ms = int(cfg.get("parent_settle_ms", 5000))
    wait_ms = int(cfg.get("parent_wait_ms", 600000))

    lock = _open_image_read_lock(games)          # 挡住升级把 Games.exe 换掉
    log(f"已锁住 {games}（只读共享），防止启动器升级期间被替换")
    _install_parent_guard()
    parent: SuspendedParent | None = None
    child: PROCESS_INFORMATION | None = None
    try:
        parent = SuspendedParent(games)
        child = _create_child_with_parent(parent.hProcess, exe, args, gd)
        # 建好了就撤锁：再锁着会挡住启动器正常升级（和 Xel 的作用域一致）
        _close_handle(lock)
        lock = None

        game_pid = int(child.dwProcessId)
        real_parent = read_parent_pid(game_pid)
        # 自查：SDK 看的就是这个字段。它对不上就必须判失败 ——
        # 悄悄放行只会变成一次让用户对着 errorCode 10100 猜原因的启动。
        if real_parent != parent.pid:
            raise SuspendedParentError(
                f"游戏进程 pid={game_pid} 的直接父进程是 {real_parent}，"
                f"不是那个挂起的 Games.exe pid={parent.pid}。拒绝继续。")
        log(f"游戏进程 pid={game_pid} 的直接父进程 = {real_parent} "
            f"← 挂起的 Games.exe ✓（SDK 要的就是这一条）")

        hide_own_console()

        # 「游戏进程有没有真的起来」：CreateProcessW 成功只说明创建了，
        # 一个起不来、加载器就退掉的进程同样会返回成功。活过这段时间才算起来。
        k32, _ = _pwin32()
        if k32.WaitForSingleObject(child.hProcess, settle_ms) == WAIT_OBJECT_0:
            code = wintypes.DWORD()
            k32.GetExitCodeProcess(child.hProcess, ctypes.byref(code))
            raise SuspendedParentError(
                f"游戏进程在 {settle_ms}ms 内就退出了（退出码 {code.value}），"
                "判定启动失败")

        # 正常路径：等游戏退出，退出码透传给 Steam。
        _close_handle(child.hThread)
        return _wait_game_exit(game_pid, wait_ms)
    finally:
        # 异常收尾：子进程已经建起来却没走到「游戏退出」那一步，说明这次启动
        # 是半成品 —— auto 马上就要回落到深链，会**再**起一个游戏。
        # 不收掉的话用户会同时看到一个坏掉的和一个好掉的。
        # 正常路径上游戏早已退出，TerminateProcess 对已退出的进程返回 FALSE，
        # 是安全的空操作。
        if child is not None and sys.exc_info()[0] is not None:
            try:
                _pwin32()[0].TerminateProcess(child.hProcess, 1)
                log("[回滚] 启动失败，已终止半途建出来的游戏进程，"
                    "避免和回落路径起的那个重复")
            except Exception as e:                   # 收不掉也要继续往下走
                log(f"[降级] 终止半途建出来的游戏进程失败: {e}")
        if child is not None:
            _close_handle(child.hThread)
            _close_handle(child.hProcess)
        if lock is not None:
            _close_handle(lock)
        if parent is not None:
            parent.terminate("启动失败回滚" if sys.exc_info()[0] is not None
                             else "游戏已退出")


def _wait_game_exit(pid: int, wait_ms: int) -> int:
    """等游戏进程退出并取回退出码。已经退出/查不到时返回一个明确的非 0 值。

    这里不再持有 CreateProcessW 的 hProcess（那属于上面的 finally），
    直接用 pid 重新 OpenProcess —— 少一个跨函数传句柄的出错点。
    """
    k32, _ = _pwin32()
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                        False, pid)
    if not h:
        log(f"[注意] 打不开游戏进程 pid={pid}（可能已经退出），按退出码 3 处理")
        return 3
    try:
        r = k32.WaitForSingleObject(h, wait_ms)
        if r != WAIT_OBJECT_0:
            log(f"[注意] 等游戏退出超时（{wait_ms}ms），终止它")
            k32.TerminateProcess(h, 1)
            return 3
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return 3
        log(f"游戏进程 pid={pid} 退出，退出码={code.value}")
        return int(code.value)
    finally:
        k32.CloseHandle(h)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> int:
    here = base_dir()

    # 分身模式：只藏窗口，绝不调深链（调过了，重复调会把启动器再弹一次）
    if wants_hide_mode(sys.argv[1:]):
        return run_hider()

    log(f"--- shim 启动 pid={os.getpid()} (frozen={getattr(sys, 'frozen', False)}) "
        f"目录={here} ---")

    cfg = load_config(here)

    # --- %TEMP%\_MEI* 残留清理 ---------------------------------------
    # 必须在**分流判断之前**：onefile 每次启动都往 %TEMP% 解包 ~19 MB，
    # 用户硬杀游戏时 PyInstaller 来不及清理（历史上漏过 2.16 GB）。
    # 清理放在后台线程里，绝不拖慢 Steam 拉起这条链。
    # 删不掉/删错的后果都比漏删严重，所以判定很保守（见 purge_stale_mei）。
    cleanup_stale_mei(cfg)

    # 伪装出来的 Games.exe：只 fork 游戏，绝不碰启动器。
    # 放在藏窗口判断**之后**、深链**之前** —— 两个分支都要求「不调深链」，
    # 而 --launch-game 是精确匹配，不会和 --hide 混。
    if wants_launch_game_mode(sys.argv[1:]):
        return run_launcher_child(cfg, here)

    # --- 分流闸：先看「我是被谁拉起来的」 ---------------------------
    # 父进程是 steam.exe（白名单）-> 走下面**原封不动**的启动逻辑；
    # 其它任何情况（含双击、含父进程已退出）-> 打开配置主页，不启动游戏。
    # 这道闸在启动逻辑**之前**，它后面的 launch_mode 逻辑一行都没改。
    side, pinfo, reason = decide_route(cfg, sys.argv[1:])
    log_route(pinfo, side, reason, whitelist_names(cfg))
    if side == "config":
        log("走配置主页这一侧：不启动游戏、不调深链、不建任何进程")
        # 默认先拉起**工具自己的页面**（Steam终末地更新器.exe）。
        # 拉不起来才回落到内置 Win32 简易配置页。
        if try_open_tool_page(here, cfg):
            log("[配置侧] 工具 GUI 已打开，shim 随即退出")
            return 0
        return open_config_page(here, pinfo)

    # 默认路径：调深链把启动交回官方启动器（这是已实测可用的行为，
    # impersonate 没开时一个字节都不变）。
    if cfg.get("impersonate"):
        log("impersonate=true：走 Games.exe 伪装，不调深链")
        return run_dispatcher(cfg, here)
    if cfg.get("game_dir"):
        log(f"（仅记录，未启用伪装）game_dir={cfg['game_dir']}")

    mode = cfg.get("launch_mode", LAUNCH_MODE_AUTO)
    log(f"launch_mode={mode}")

    # --- 挂起父进程：启动器界面根本不存在的那条路 ---
    # 放在深链**之前**判断：auto 就是「先试这条，失败再走深链」。
    if mode in (LAUNCH_MODE_AUTO, LAUNCH_MODE_SUSPENDED_PARENT):
        try:
            rc = run_suspended_parent(cfg, here)
            # 成功就是终局：没有启动器界面，也就没什么可藏的。
            # 藏窗口那套是为深链准备的，这里派生 hider 只会白扫一堆进程。
            log(f"挂起父进程方案启动完成，游戏退出码 rc={rc}（透传给 Steam）")
            log(f"--- shim 退出 rc={rc} ---")
            return rc
        except SuspendedParentError as e:
            log(f"[挂起父进程失败] {e}")
            if mode == LAUNCH_MODE_SUSPENDED_PARENT:
                log("launch_mode=suspended_parent：**不**回落深链（回落会让用户"
                    "以为方案生效，实际弹出来的还是启动器界面）")
                try:
                    ctypes.windll.user32.MessageBoxW(
                        None,
                        "挂起父进程方案启动失败。\n\n"
                        f"{e}\n\n"
                        f"shim 所在目录：\n{here}\n\n"
                        "把 shim_config.json 里的 launch_mode 改成 \"deep_link\" "
                        "可以退回官方启动器深链（会弹出启动器界面）。\n\n"
                        f"详见 {LOG_NAME}",
                        "Steam 终末地 shim", 0x10)
                except Exception:
                    pass
                log(f"--- shim 退出 rc=2 ---")
                return 2
            log("launch_mode=auto：回落到协议深链")
        except Exception as e:
            # 非 SuspendedParentError 的意外错误**不**回落：静默回落会掩盖 bug，
            # 而且用户会以为方案生效了，实际看到的却是启动器界面。
            log(f"[错误] 挂起父进程方案内部异常，不回落: {type(e).__name__}: {e}")
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"挂起父进程方案内部异常：\n{type(e).__name__}: {e}\n\n"
                    f"详见 {LOG_NAME}",
                    "Steam 终末地 shim", 0x10)
            except Exception:
                pass
            log("--- shim 退出 rc=2 ---")
            return 2

    # --- 深链（auto 的回落目标 / deep_link 的唯一路径）---
    # 刻意**不做**「已经在运行就别启动」的判断。
    # 官方启动器常驻后台是常态，那种 guard 会让每一次点开始游戏都变成空操作
    # —— 而且 shim 自己就叫 Endfield.exe，按进程名查必然把自己也查进去。
    # 重复点击由官方启动器自己去重（它本来就是深链协议处理器）。

    launcher = find_launcher(here)
    log(f"启动器（仅诊断用）: {launcher or '未找到'}")

    # 先把启动链路打通，再考虑外壳好不好看。顺序不能反。
    rc = launch(here)

    if rc == 0:
        cfg = load_config(here)        # 读配置放在启动之后，一秒都不占启动
        spawn_hider(cfg)
        if cfg["keep_shim_until_game"]:
            wait_for_game(cfg)
    else:
        log("启动失败，不派生藏窗口进程")

    if rc != 0:
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                "拉起官方启动器失败。\n\n"
                "请确认你装了鹰角官服客户端。\n\n"
                f"shim 所在目录：\n{here}\n\n"
                f"详见 {LOG_NAME}",
                "Steam 终末地 shim", 0x10)
        except Exception:
            pass
    log(f"--- shim 退出 rc={rc} ---")
    return rc


if __name__ == "__main__":
    sys.exit(main())
