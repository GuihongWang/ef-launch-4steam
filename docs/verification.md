<a id="status"></a>
# 当前状态

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 2026-10-22 端到端验证的三份日志原文与 pid 闭环记录。返回 [目录](../README.md)。

2026-10-22 在本机完成端到端验证并复查。三份互不相干的日志对上了同一组 pid：
工具自己的 shim 日志、游戏 SDK 的鉴权日志、Steam 自己的进程日志。

**shim 侧** —— `C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\shim.log`
（`endfield_shim.py` 的 `log()` 写出，落在 `base_dir()` 即 exe 所在目录）：

```text
[2026-10-22 13:35:22] --- shim 启动 pid=27044 (frozen=True) 目录=C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield ---
[2026-10-22 13:35:22] [分流] 跳过同 exe 的引导进程实例: 8368 (C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\Endfield.exe)
[2026-10-22 13:35:22] [分流] 祖先链 = pid:8368(Endfield.exe) -> pid:34512(steam.exe) -> pid:38340(explorer.exe) -> pid:1260(winlogon.exe)
[2026-10-22 13:35:22] [分流] 父进程 pid=8368 映像=...\Arknights Endfield\Endfield.exe 文件名=Endfield.exe 命中层级=2 命中文件名=steam.exe 白名单=['steam.exe'] 匹配=命中 → 走「启动游戏」侧（祖先链第 2 级 steam.exe 命中白名单 ['steam.exe']）
[2026-10-22 13:35:22] launch_mode=auto
[2026-10-22 13:35:22] 官方 Games.exe: C:\Program Files\Hypergryph Launcher\1.6.0\Games.exe（launcher 的父目录，版本 1.6.0（候选版本 ['1.6.0']））
[2026-10-22 13:35:22] 已锁住 C:\Program Files\Hypergryph Launcher\1.6.0\Games.exe（只读共享），防止启动器升级期间被替换
[2026-10-22 13:35:22] CreateProcessW(Games.exe, CREATE_SUSPENDED) cwd=C:\Program Files\Hypergryph Launcher\1.6.0
[2026-10-22 13:35:22] 挂起的 Games.exe 已创建 pid=37604（本进程**永不恢复**它）
[2026-10-22 13:35:22] CreateProcessW(游戏, 父进程=挂起的 Games.exe) "C:\Program Files\Hypergryph Launcher\games\Endfield Game\Endfield.exe" -launcher_lang=zh-cn -launcher_sub_channel=1   (cwd=C:\Program Files\Hypergryph Launcher\games\Endfield Game)
[2026-10-22 13:35:22] 游戏进程 pid=34852 的直接父进程 = 37604 ← 挂起的 Games.exe ✓（SDK 要的就是这一条）
```

**SDK 鉴权侧** —— `C:\Users\<你>\AppData\LocalLow\Hypergryph\Endfield\sdklogs\OneChannelLC.log`：

```text
[10-22 13:35:37.941] [info] [] [] [line:] [thread 0] Add launch check:Launcher, 1, 2
[10-22 13:35:37.941] [info] [] [] [line:] [thread 0] Total launch check size:1
[10-22 13:35:38.014] [info] [] [] [line:] [thread 0] Parent process check success:37604
[10-22 13:35:38.014] [info] [] [] [line:] [thread 0] Record check result:{"code":2003,"msg":{"code":0,"msg":{"filePath":"c:\\program files\\hypergryph launcher\\1.6.0\\games.exe"}}},
```

**Steam 侧** —— `C:\Program Files (x86)\Steam\logs\gameprocess_log.txt`：

```text
[2026-10-22 13:35:22] AppID 4732690 adding PID 8368 as a tracked process ""C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\Endfield.exe""
[2026-10-22 13:35:22] AppID 4732690 adding PID 27044 as a tracked process ""C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\Endfield.exe""
[2026-10-22 13:35:22] AppID 4732690 adding PID 34852 as a tracked process ""C:\Program Files\Hypergryph Launcher\games\Endfield Game\Endfield.exe" -launcher_lang=zh-cn -launcher_sub_channel=1"
[2026-10-22 13:35:24] AppID 4732690 adding PID 4588 as a tracked process ""C:\Program Files\Hypergryph Launcher\games\Endfield Game\AntiCheatExpert\ACE-Setup64.exe" -install -g6010071"
[2026-10-22 13:35:28] AppID 4732690 adding PID 16100 as a tracked process ""C:\Program Files\Hypergryph Launcher\games\Endfield Game\UnityCrashHandler64.exe" --attach 34852 143527936"
```

pid 对应关系（整件事的闭环）：

| 角色 | 本次 pid | 出处 |
|---|---|---|
| Steam 拉起的 shim 引导进程 | 8368 | shim.log / Steam `gameprocess_log.txt` |
| shim 真正干活的进程 | 27044 | shim.log / Steam `gameprocess_log.txt` |
| 挂起的官方 `Games.exe`（父进程） | 37604 | shim.log |
| 真正的游戏进程 | 34852 | shim.log / Steam `gameprocess_log.txt` |
| SDK 判定通过的父进程 | 37604 | `OneChannelLC.log` |
| ACE 安装进程 | 4588 | Steam `gameprocess_log.txt` |
| Unity 崩溃处理器 | 16100 | Steam `gameprocess_log.txt` |

`37604` 同时出现在 shim 的「挂起的 Games.exe」和 SDK 的「Parent process check success」里
—— 父进程检查通过了；Steam 把真游戏进程（pid 34852，路径在官服目录）登记成自己的
子进程，ACE 也正常拉起来了；官方启动器界面从头到尾没出现。

> **一次没跑完的地方（如实记录）**：这次运行 shim 最终返回的是 **rc=3**，不是 0。
> 原因是 `endfield_shim.py` 的 `run_suspended_parent()` 传给 `_wait_game_exit()` 的
> `parent_wait_ms` 默认 **600000 ms（10 分钟）**，13:45:27 超时后 shim 记
> 「等游戏退出超时（600000ms），终止它」并返回 3。而 Steam 侧直到 **14:32:54**
> 才记录游戏退出 —— 也就是说那行「终止它」**并没有真的杀掉游戏**（原因见
> [已知限制与未验证项](../README.md#limitations)）。游戏确实跑起来了，但 shim 的退出码不是 0。
