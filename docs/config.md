<a id="config"></a>
# 配置参考 shim_config.json

> 本文是 [主 README](../README.md) 拆出的深入章节 —— 全部配置项、坏值回落规则与生效方式。返回 [目录](../README.md)。

配置写在**部署目录**里：

```text
C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\shim_config.json
```

本机当前这份的完整内容：

```json
{
  "launcher": "C:\\Program Files\\Hypergryph Launcher\\Launcher.exe",
  "scheme": "hypergryphlauncher-abYeZZ16BPluCFyT",
  "deep_link": "hypergryphlauncher-abYeZZ16BPluCFyT://start?ta=endfield",
  "version": "1.0.0",
  "app_id": 4732690,
  "written": 1792646833,
  "impersonate": false,
  "launch_mode": "auto",
  "launch_source": "parent",
  "parent_whitelist": ["steam.exe"],
  "parent_match_depth": 4,
  "config_page": "tool",
  "tool_path": "D:\\Tools\\Steam终末地更新器.exe",
  "game_dir": "C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game",
  "cleanup_stale_mei": true,
  "cleanup_mei_min_age_hours": 1.0,
  "hide_launcher": false,
  "hide_ms": 60000,
  "hide_settle_ms": 15000,
  "hide_grace_ms": 90000,
  "hide_taskbar": true,
  "hide_if_preexisting": false,
  "keep_shim_until_game": false
}
```

### 全部配置项

「部署写」= `endfield_updater.py` 的 `shim_install()` 会写这个键；
「shim 读」= `endfield_shim.py` 会不会真的用到它。

| 键 | 部署写 | shim 读 | 默认 | 说明 |
|---|---|---|---|---|
| `launcher` | 是 | 是（`find_official_games_exe()` 经 `_raw_config()`） | `""` | 官方启动器 `Launcher.exe` 绝对路径。`find_official_games_exe()` 用它的**父目录**当启动器根 |
| `scheme` | 是 | 是（`find_deep_link_scheme()` 直接读 JSON） | `""` | 官方深链协议名，注册表探测得到 |
| `deep_link` | 是 | 否 | 由 `scheme` 拼 | 仅供人看 |
| `version` | 是 | 否 | `"1.0.0"` | 配置版本号 |
| `app_id` | 是 | 否 | `4732690` | |
| `written` | 是 | 否 | unix 秒 | 配置写入时间 |
| `impersonate` | 是（仅 `--impersonate`） | 是 | `false` | **别开**，见[已废弃的方案](tech.md#deprecated) |
| `launch_mode` | 是（沿用已有） | 是 | `"auto"` | `auto` / `suspended_parent` / `deep_link` |
| `launch_source` | 是（沿用已有） | 是 | `"parent"` | `parent` / `always_launch` / `always_config` |
| `parent_whitelist` | 是（沿用已有） | 是 | `["steam.exe"]` | 只比文件名，大小写不敏感 |
| `parent_match_depth` | 是（沿用已有） | 是 | `4` | 含直接父进程，范围 1~16 |
| `config_page` | 是（沿用已有） | 是 | `"tool"` | `tool` / `builtin` / `auto` |
| `tool_path` | 是（沿用已有） | 是 | `""` | 工具 GUI 的**绝对路径** |
| `game_dir` | 是（探测） | 是 | `""` | 官服游戏目录（含 `Endfield.exe` 那层） |
| `cleanup_stale_mei` | 是 | 是 | `true` | 启动时顺手清 `%TEMP%` 下的 `_MEI*` 残留 |
| `cleanup_mei_min_age_hours` | 是 | 是 | `1.0` | 「陈旧」判定门槛（小时），下限 0.1 |
| `hide_launcher` | 是 | 是 | `false` | 藏官方启动器窗口，**实验性，别开** |
| `hide_ms` | 是 | 是 | `60000` | 藏窗口总时长（1000~600000） |
| `hide_settle_ms` | 是 | 是 | `15000` | 观察期，这段时间内只看不藏（0~300000） |
| `hide_grace_ms` | 是 | 是 | `90000` | 启动宽限期（1000~900000），且强制 ≥ `hide_settle_ms + 1000` |
| `hide_taskbar` | 是 | 是 | `true` | 藏窗口时连任务栏 / Alt-Tab 一起摘掉 |
| `hide_if_preexisting` | 是 | 是 | `false` | 连「本来就开着」的启动器窗口也一起藏 |
| `keep_shim_until_game` | 是 | 是 | `false` | 游戏出现前 shim 不退出（深链路径） |
| `launcher_root` | **否** | 是 | `""` | 显式指定启动器根目录，优先级最高 |
| `game_args` | **否** | 是 | `null` | `null` = 用官方实测参数；`""` = 一个都不传 |
| `parent_settle_ms` | **否** | 是 | `5000` | 游戏进程必须活过这么久（0~60000） |
| `parent_wait_ms` | **否** | 是 | `600000` | 等游戏退出最多等多久（1000~86400000） |
| `tool_exe` | **否** | 是 | `""` | **老字段**，只作兼容 |

> 最后五个键 `shim install` **不写**（它们只存在于 `endfield_shim.py` 的
> `DEFAULT_CONFIG` 里），要改得手改 `shim_config.json`。
> `launcher_root` / `game_args` 的读取走 `find_official_games_exe()` / `resolve_game_args()`；
> `tool_exe` 走 `resolve_tool_path()`。

### 坏值怎么回落

`endfield_shim.py` 的 `load_config()` 逐项处理，**认不出就回落默认值，不会静默变成另一种行为**：

| 配置项 | 坏值时的行为 |
|---|---|
| `launch_mode` | 不是 `auto`/`suspended_parent`/`deep_link` → `auto` |
| `launch_source` | 不是 `parent`/`always_launch`/`always_config` → `parent`（**不会**变成 `always_launch`） |
| `config_page` | 不是 `tool`/`builtin`/`auto` → `tool`（**不会**变成 `builtin`） |
| `parent_match_depth` | 不是正整数 → 默认 `4`；超过 16 → 截到 16 |
| `parent_whitelist` | 类型不对或为空 → 默认 `["steam.exe"]` |
| `hide_ms` / `hide_settle_ms` / `hide_grace_ms` | 夹到各自区间 |
| `parent_settle_ms` / `parent_wait_ms` | 夹到各自区间 |
| `cleanup_mei_min_age_hours` | 解析不了 → 默认 `1.0`；小于 0.1 → 按 0.1 |
| 布尔类字段 | `_as_bool()` 认 `true/1/yes/on/y` 与 `false/0/no/off/n/""`；认不出才用默认值 |
| `game_dir` / `launcher_root` / `tool_path` / `tool_exe` | 不是字符串 → 当没配（`game_dir` 置空串） |
| `game_args` | `null` → 默认参数；字符串 → 按空白切；全字符串的列表 → 原样；其它类型 → 默认参数 |
| 整个文件读不动 / 不是 JSON 对象 | **全部用默认值**，并在 `shim.log` 记「[降级] …」 |

### 改完怎么生效

- 改 `shim_config.json` → **下次启动 shim 时生效**。shim 每次启动都重新读一遍这个文件
  （`main()` 里的 `load_config(here)`）。
- 想立刻验证：**完全退出 Steam 再重启**，然后点一次开始游戏。
- 读当前生效值：

```powershell
> python -X utf8 endfield_updater.py shim status --json
```

> shim 也会**回写**这个文件：`launch_tool_gui()` 找到工具 GUI 后用
> `save_config_value()` 把 `tool_path` 记回去，下次不用再找。
