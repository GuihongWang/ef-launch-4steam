#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 终末地更新器 — 自动改写 appmanifest 的 InstalledDepots/buildid，欺骗 Steam 更新检查。

用途：
  Steam 外区客户端会把 Hypergryph 启动器（官服/国际服）下载的文件当成“需要更新”，
  通过写入与 Steam appinfo / SteamDB 一致的 manifest / buildid，让 Steam 认为当前
  已是最新版本，从而不再用 Steam 版内容覆盖你已有的文件。

与「鸣潮版」的关键差异：
  - appid            4732690 (Arknights: Endfield / 明日方舟：终末地)
  - depot            4732691（终末地只有一个 depot，鸣潮工具硬编码了 3513351）
  - Steam installdir "Arknights Endfield"
  - 主程序           Endfield.exe
  - DX 切换          -force-d3d11（鸣潮是 -dx11 / -dx12）
  - 官服安装器       Hypergryph 启动器
                      （默认 C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game）

数据来源（优先级从高到低）：
  1. 命令行手填 --manifest / --buildid
  2. 粘贴 SteamDB 复制的 "-app 4732690 -depot 4732691 -manifest 123" 字符串
  3. 本机 Steam appcache\\appinfo.vdf（新版 Steam 为 appcache\\appinfo\\appinfo_XX.vdf 分片；
      Steam 启动联网后会缓存最新 depot manifest）
  4. 内置快照常量（仅在以上都拿不到时兜底，会明确警告其可能已过期）

用法：
  python endfield_updater.py status
  python endfield_updater.py apply
  python endfield_updater.py apply --manifest 3024011413268238677 --buildid 25728898
  python endfield_updater.py apply --from-steamdb "-app 4732690 -depot 4732691 -manifest 3024011413268238677"
  python endfield_updater.py restore
  python endfield_updater.py shim status
  python endfield_updater.py shim install
  python endfield_updater.py convert --game-dir "C:\\Program Files\\Hypergryph Launcher\\games\\Endfield Game"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 游戏常量
# ---------------------------------------------------------------------------

APP_ID = 4732690
DEPOT_ID = "4732691"
GAME_NAME = "Arknights Endfield"          # Steam installdir（注意是空格分隔，不是冒号）
GAME_DISPLAY = "终末地"
GAME_EXE_NAME = "Endfield.exe"
DEFAULT_LANGUAGE = "schinese"

# 启动选项：Steam 侧 config.launch 里定义了 -force-d3d11（DirectX 11），默认为 DX12
LAUNCH_DX11 = "-force-d3d11"
LAUNCH_DX12 = ""

# 官服（Hypergryph 启动器）常见安装位置。
# 实测本机： C:\Program Files\Hypergryph Launcher\games\Endfield Game
# 注意是 "Endfield Game"（f 小写），且启动器目录名是 "Hypergryph Launcher"，
# 不是网上常见的 "GRYPHLINK"（那是国际服/旧称）。
OFFICIAL_DIR_HINTS = (
    r"C:\Program Files\Hypergryph Launcher\games\Endfield Game",
    r"C:\Program Files (x86)\Hypergryph Launcher\games\Endfield Game",
    r"D:\Hypergryph Launcher\games\Endfield Game",
    r"E:\Hypergryph Launcher\games\Endfield Game",
    r"F:\Hypergryph Launcher\games\Endfield Game",
    # 兼容其它渠道/旧版本的命名
    r"C:\Program Files\GRYPHLINK\games\Endfield Game",
    r"C:\Program Files\GRYPHLINK\games\EndField Game",
    r"C:\Program Files\Arknights Endfield",
    r"D:\Arknights Endfield",
    r"E:\Arknights Endfield",
)

# 搜索 exe 时要跳过的目录名（小写比较）。
# 关键：官服目录下有 "终末地资源备份包\终末地鹰角官服\Endfield.exe" 这种备份副本，
# 不跳过的话会误命中备份目录，然后把联接指到备份上去。
SKIP_DIR_NAMES = frozenset({
    "windows", "system32", "syswow64",
    "$recycle.bin", "system volume information",
    "ace", "anticheat", "antichatexpert",
    "backup", "backups", "bak", "old", "temp", "tmp", "cache",
    "崩溃", "日志", "logs", "crash",
})

# 内置兜底快照（抓取时间 2026-10-15）。appinfo 与 SteamDB 都拿不到时才会用到，
# 且每次使用都会打印过期警告——终末地版本迭代很快，这个值大概率很快就不准了。
SNAPSHOT_DATE = "2026-10-15"
FALLBACK_BUILDID = "25728898"
FALLBACK_MANIFEST = "3024011413268238677"
FALLBACK_DEPOT_SIZE = "68276329686"

DEFAULT_STEAM_ROOT = Path(r"C:\Program Files (x86)\Steam")
BACKUP_DIR = Path.home() / ".steam_endfield_updater"
BACKUP_NAME = f"appmanifest_{APP_ID}.acf.bak"

# 锁定的 Steam 库（多库机器上必须能指定写到哪个库，否则 Steam 根本看不到游戏）
PINNED_LIBRARY: str | None = None


def _fix_console_encoding() -> None:
    """中文 Windows 控制台默认 GBK，直接 print 中文会乱码或抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (stream.encoding or "").lower()
            if enc and enc not in ("utf-8", "utf8"):
                stream.reconfigure(encoding=enc, errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_fix_console_encoding()


def clean_path(value) -> Path:
    """
    清洗用户传入的路径：去掉首尾空白和成对引号。

    从别处粘贴的路径常常带着引号（"D:\\Games\\EndField"），某些调用环境还会
    把引号原样传进 argv。不处理的话整条路径都带引号，直接找不到文件。
    """
    s = str(value).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return Path(s)


def clean_text(value: str) -> str:
    """去掉首尾空白和成对引号，用于启动参数等非路径文本。"""
    s = str(value).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s


# 运行时解析（resolve_game_paths 会更新）
STEAM_ROOT = DEFAULT_STEAM_ROOT
STEAMAPPS = STEAM_ROOT / "steamapps"
ACF_PATH = STEAMAPPS / f"appmanifest_{APP_ID}.acf"
APPINFO_PATH = STEAM_ROOT / "appcache" / "appinfo.vdf"

# INSTALL_DIR 恒为「Steam 期望的安装路径」，也就是联接的**落点**（link）。
# 它绝不能被 exe 反推覆盖 —— 否则 Steam 侧目录还没建时，INSTALL_DIR 会被指到
# 官服目录上，create_junction 就会去改名官服本体。这是两个不同的概念，必须分开。
INSTALL_DIR = STEAMAPPS / "common" / GAME_NAME

# 官服（Hypergryph 启动器）实际安装目录，联接的**来源**（target）。找不到时为 None。
OFFICIAL_GAME_DIR: Path | None = None


# ---------------------------------------------------------------------------
# Steam 库 / 安装位置自动发现（对应 Hypergryph 启动器的「重新定位游戏」）
# ---------------------------------------------------------------------------

def _parse_libraryfolders(steam_root: Path) -> list[Path]:
    """读 libraryfolders.vdf，返回全部库根目录（含失效库，便于选择目标）。"""
    roots: list[Path] = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return roots
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return roots
    # 新旧格式都覆盖：
    #   旧  "1"  { "path" "D:\\SteamLibrary" ... }
    #   新  "libraryfolders" { "0" { "path" "D:\\SteamLibrary" } }
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = m.group(1).replace("\\\\", "\\")
        p = Path(raw)
        if p not in roots:
            roots.append(p)
    return roots


def find_steam_roots() -> list[Path]:
    """可能的 Steam 安装根目录（硬编码候选 + 注册表）。"""
    candidates = [
        DEFAULT_STEAM_ROOT,
        Path(r"C:\Program Files\Steam"),
        Path(r"D:\Program Files (x86)\Steam"),
        Path(r"D:\Steam"),
        Path(r"E:\Steam"),
        Path(r"C:\Steam"),
        Path(r"F:\Steam"),
    ]
    try:
        import winreg

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath", "BaseInstallFolder"):
                        try:
                            val, _ = winreg.QueryValueEx(k, name)
                            if val:
                                candidates.append(Path(str(val).replace("/", "\\")))
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass

    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            if not c.exists():
                continue
            r = str(c.resolve()).lower()
            if r in seen:
                continue
            if (c / "steam.exe").exists() or (c / "steamapps").exists():
                seen.add(r)
                out.append(c)
        except OSError:
            continue
    return out


def _scan_drive_steamlibs() -> list[Path]:
    """扫描各盘下的 Steam / SteamLibrary 等含 steamapps 的目录。"""
    out: list[Path] = []
    for drive in "CDEFGHIJ":
        root = Path(f"{drive}:\\")
        if not root.exists():
            continue
        for name in (
            "Steam",
            "SteamLibrary",
            "steam",
            "Program Files (x86)\\Steam",
            "Program Files\\Steam",
        ):
            p = root / name
            try:
                if (p / "steamapps").exists() or (p / "steam.exe").exists():
                    out.append(p)
            except OSError:
                pass
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                if "steam" in child.name.lower():
                    try:
                        if (child / "steamapps").exists():
                            out.append(child)
                    except OSError:
                        pass
        except OSError:
            pass
    return out


def discover_steam_libraries(steam_root: Path | None = None) -> list[dict]:
    """返回每个 Steam 库的可用目标信息（含暂不可用的库，便于选择联接目标）。"""
    if steam_root is None:
        roots = find_steam_roots()
        steam_root = roots[0] if roots else DEFAULT_STEAM_ROOT
    libs = list(_parse_libraryfolders(steam_root))
    for extra in find_steam_roots():
        for p in _parse_libraryfolders(extra):
            if p not in libs:
                libs.append(p)
    for p in _scan_drive_steamlibs():
        if p not in libs:
            libs.append(p)

    result: list[dict] = []
    seen: set[str] = set()
    for lib in libs:
        steamapps = lib / "steamapps"
        try:
            key = str((steamapps if steamapps.exists() else lib).resolve()).lower()
        except OSError:
            key = str(lib).lower()
        if key in seen:
            continue
        seen.add(key)
        acf = steamapps / f"appmanifest_{APP_ID}.acf"
        inst = steamapps / "common" / GAME_NAME
        item = {
            "steam_root": lib,
            "steamapps": steamapps,
            "library_path": lib,
            "appmanifest": acf if acf.exists() else None,
            "install_dir": inst if inst.exists() else None,
            "appmanifest_path": acf,
            "install_dir_path": inst,
            "exists": steamapps.exists() or lib.exists(),
            "has_game": acf.exists() or inst.exists(),
        }
        result.append(item)
    return result


def find_endfield_exe(start: Path | None = None, max_depth: int = 4) -> list[Path]:
    """
    Hypergryph 启动器逻辑：定位 Endfield.exe 所在文件夹。
    - 指定 start：在该目录下递归查找（类似「重新定位游戏」选文件夹）
    - 不指定：扫描 Steam 各库 + 常见官服安装路径（快速，不全盘扫）

    返回按「离 start 越浅越靠前」排序 —— 官服目录里存在
    「终末地资源备份包\\终末地鹰角官服\\Endfield.exe」这种深层备份副本，
    真正的游戏 exe 一定比它浅，排序后 find_game_folder() 自然选对。
    """
    hits: list[tuple[int, Path]] = []
    seen: set[str] = set()

    def add(p: Path, depth: int) -> None:
        try:
            rp = str(p.resolve()).lower()
        except OSError:
            rp = str(p).lower()
        if rp not in seen:
            seen.add(rp)
            hits.append((depth, p))

    def walk(p: Path, depth: int, limit: int) -> None:
        if depth > limit or len(hits) >= 20:
            return
        try:
            if p.is_file() and p.name.lower() == GAME_EXE_NAME.lower():
                add(p, depth)
                return
            if not p.is_dir():
                return
            direct = p / GAME_EXE_NAME
            if direct.is_file():
                add(direct, depth)
            if depth >= limit:
                return
            for child in p.iterdir():
                if not child.is_dir():
                    continue
                name = child.name.lower()
                if name in SKIP_DIR_NAMES:
                    continue
                # 备份包 / 旧版本暂存目录一律不进（名字里带关键词就跳）
                if any(k in name for k in ("备份", "backup", "bak", "旧", "old")):
                    continue
                walk(child, depth + 1, limit)
        except OSError:
            return

    if start is not None:
        walk(Path(start), 0, max_depth)
        hits.sort(key=lambda t: t[0])
        return [p for _d, p in hits]

    # 1) 直接候选（Steam 库 + 已知官服路径）
    for item in discover_steam_libraries():
        for folder in (item["install_dir"], item["steamapps"] / "common" / GAME_NAME):
            if folder and (Path(folder) / GAME_EXE_NAME).is_file():
                add(Path(folder) / GAME_EXE_NAME, 0)

    for raw in OFFICIAL_DIR_HINTS:
        exe = Path(raw) / GAME_EXE_NAME
        if exe.is_file():
            add(exe, 0)

    if hits:
        return [p for _d, p in hits]

    # 2) 各 Steam 库 common 下浅搜
    for item in discover_steam_libraries():
        walk(item["steamapps"] / "common", 0, 2)

    # 3) 盘符顶层常见游戏目录（浅）
    if not hits:
        for drive in "CDEFGH":
            root = Path(f"{drive}:\\")
            if not root.exists():
                continue
            try:
                for child in root.iterdir():
                    if not child.is_dir():
                        continue
                    n = child.name.lower()
                    if any(k in n for k in ("endfield", "arknights", "gryphlink",
                                            "gryph", "hypergryph")) or \
                            n in {"games", "program files", "program files (x86)"}:
                        walk(child, 0, 3)
            except OSError:
                continue

    hits.sort(key=lambda t: t[0])
    return [p for _d, p in hits]


def find_game_folder(start: Path | None = None) -> Path | None:
    """返回包含 Endfield.exe 的文件夹。"""
    exes = find_endfield_exe(start)
    if not exes:
        return None
    return exes[0].parent


def pin_library(path) -> None:
    """锁定目标 Steam 库。后续所有命令都用这个库，不再自动挑。"""
    global PINNED_LIBRARY
    PINNED_LIBRARY = str(clean_path(path))
    print(f"[库] 已锁定 Steam 库: {PINNED_LIBRARY}")


def unpin_library() -> None:
    """解除锁定，恢复自动选择。"""
    global PINNED_LIBRARY
    PINNED_LIBRARY = None
    print("[库] 已解除锁定，恢复自动选择")


def _apply_library(lib: Path, force_install_dir: bool = False) -> None:
    """
    把模块级路径指向指定库。

    force_install_dir=True（pin_library 场景）时无条件改写 INSTALL_DIR ——
    否则锁定到一个「还没装游戏」的库时，INSTALL_DIR 会残留上一个库的路径，
    导致 convert/junction 在错误的库里建联接。
    """
    global STEAM_ROOT, STEAMAPPS, ACF_PATH, APPINFO_PATH, INSTALL_DIR
    lib = Path(lib)
    steamapps = lib / "steamapps"
    if not steamapps.exists() and lib.name == "steamapps":
        lib = lib.parent
    STEAM_ROOT = lib
    STEAMAPPS = lib / "steamapps"
    ACF_PATH = STEAMAPPS / f"appmanifest_{APP_ID}.acf"
    APPINFO_PATH = lib / "appcache" / "appinfo.vdf"
    inst = STEAMAPPS / "common" / GAME_NAME
    if force_install_dir or inst.exists():
        INSTALL_DIR = inst


def _paths_payload() -> dict:
    return {
        "steam_root": str(STEAM_ROOT),
        "steamapps": str(STEAMAPPS),
        "appmanifest": str(ACF_PATH),
        "install_dir": str(INSTALL_DIR),
        "official_dir": str(OFFICIAL_GAME_DIR) if OFFICIAL_GAME_DIR else None,
        "appinfo": str(APPINFO_PATH),
        "pinned_library": PINNED_LIBRARY,
    }


def _libraries_payload(libs: list[dict]) -> list[dict]:
    return [
        {
            "library_path": str(x["library_path"]),
            "appmanifest": str(x["appmanifest"]) if x["appmanifest"] else None,
            "install_dir": str(x["install_dir"]) if x["install_dir"] else None,
            "has_game": x["has_game"],
        }
        for x in libs
    ]


def _is_inside_steam(p: Path) -> bool:
    """判断路径是否位于某个 Steam 库的 steamapps 下面。"""
    try:
        rp = os.path.normcase(os.path.realpath(str(p)))
    except OSError:
        return False
    return "\\steamapps\\" in rp


def detect_official_dir() -> Path | None:
    """
    探测官服（Hypergryph 启动器）实际安装目录。找不到返回 None。

    必须把联接解掉：Steam 侧建好联接后，find_game_folder() 会先命中
    steamapps\\common\\Arknights Endfield，而这个路径其实是指回官服目录的联接 ——
    直接返回它会把「官服目录」误报成「Steam 安装目录」，进而给出
    「无需联接」这种误导性警告。
    """
    global OFFICIAL_GAME_DIR
    OFFICIAL_GAME_DIR = None

    cands = find_endfield_exe()
    if not cands:
        return None

    # 每个候选：解析联接后的真实目录
    resolved_dirs: list[Path] = []
    for exe in cands:
        parent = exe.parent
        try:
            r = Path(os.path.realpath(str(parent)))
        except OSError:
            r = parent
        # realpath 可能指向一个不含 exe 的路径（异常情况），那就用原路径
        if not (r / GAME_EXE_NAME).is_file():
            r = parent
        if r not in resolved_dirs:
            resolved_dirs.append(r)

    # 优先挑「解析后不在 Steam 库里」的那个（即真正的官服目录）
    for r in resolved_dirs:
        if not _is_inside_steam(r):
            OFFICIAL_GAME_DIR = r
            return OFFICIAL_GAME_DIR

    # 全都在 Steam 库里（还没建联接，或 Steam 版就是本体）
    OFFICIAL_GAME_DIR = resolved_dirs[0]
    return OFFICIAL_GAME_DIR


def resolve_game_paths(detect_official: bool = True) -> dict:
    """
    解析当前应使用的 ACF / Steam 安装目录 / Steam 根，并更新模块全局路径。
    已 pin_library() 时直接用锁定库；否则优先「已有 appmanifest 的库」→
    「已有 common 目录的库」→ 默认库。

    注意：这里**不会**用 exe 反推去改写 INSTALL_DIR。
    INSTALL_DIR 恒为 Steam 期望的安装路径（联接落点），
    官服目录单独放在 OFFICIAL_GAME_DIR（联接来源）。
    """
    global STEAM_ROOT, STEAMAPPS, ACF_PATH, APPINFO_PATH, INSTALL_DIR

    if PINNED_LIBRARY:
        # 锁定模式下用锁定库；用户明确选了库，联接必须建在这个库里。
        _apply_library(Path(PINNED_LIBRARY), force_install_dir=True)
        out = _paths_payload()
        out["libraries"] = _libraries_payload(discover_steam_libraries())
        if detect_official:
            detect_official_dir()
            out = _paths_payload()
            out["libraries"] = _libraries_payload(discover_steam_libraries())
        return out

    libs = discover_steam_libraries()
    acf = None
    inst = None
    for item in libs:
        if item["appmanifest"]:
            acf = item["appmanifest"]
            if item["install_dir"]:
                inst = item["install_dir"]
            break
    if acf is None:
        for item in libs:
            if item["install_dir"]:
                inst = item["install_dir"]
                acf = item["steamapps"] / f"appmanifest_{APP_ID}.acf"
                break
    if acf is None and libs:
        item = libs[0]
        acf = item["steamapps"] / f"appmanifest_{APP_ID}.acf"
        inst = item["install_dir"] or (item["steamapps"] / "common" / GAME_NAME)

    if acf is not None:
        ACF_PATH = acf
        STEAMAPPS = acf.parent
        STEAM_ROOT = STEAMAPPS.parent if STEAMAPPS.name == "steamapps" else STEAM_ROOT
        APPINFO_PATH = STEAM_ROOT / "appcache" / "appinfo.vdf"
    if inst is not None:
        INSTALL_DIR = inst

    if detect_official:
        detect_official_dir()

    out = _paths_payload()
    out["libraries"] = _libraries_payload(libs)
    return out


def steam_link_targets() -> list[dict]:
    """创建 mklink 时可用的 Steam 目标位置（ACF + common 安装目录）。"""
    resolve_game_paths()
    out: list[dict] = []
    for item in discover_steam_libraries():
        acf = item.get("appmanifest_path") or item.get("appmanifest") or (
            item["steamapps"] / f"appmanifest_{APP_ID}.acf"
        )
        inst = item.get("install_dir_path") or item.get("install_dir") or (
            item["steamapps"] / "common" / GAME_NAME
        )
        acf_p = Path(acf)
        inst_p = Path(inst)
        out.append({
            "label": str(item["library_path"]),
            "steam_root": str(item["steam_root"]),
            "library_exists": bool(item.get("exists", True)),
            "appmanifest": str(acf_p),
            "appmanifest_exists": acf_p.exists(),
            "install_dir": str(inst_p),
            "install_dir_exists": inst_p.exists(),
            "has_game": item["has_game"],
        })
    return out


# ---------------------------------------------------------------------------
# ACF (Valve KeyValues text) — 保留缩进/制表符风格
# ---------------------------------------------------------------------------

def read_acf(path: Path) -> dict:
    """ACF 读取：支持 Steam 的 "key"\\n{ 块，以及 "key"\\t\\t"value"。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    root: dict = {}
    stack: list[dict] = [root]
    pending_key: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line == "}":
            if pending_key is not None:
                # "key" 后直接 } —— 空块
                stack[-1][pending_key] = {}
                pending_key = None
            if len(stack) > 1:
                stack.pop()
            continue
        if line == "{":
            if pending_key is None:
                continue
            node: dict = {}
            stack[-1][pending_key] = node
            stack.append(node)
            pending_key = None
            continue
        m = re.match(r'^"([^"]+)"\s*\{$', line)
        if m:
            node = {}
            stack[-1][m.group(1)] = node
            stack.append(node)
            pending_key = None
            continue
        m = re.match(r'^"([^"]+)"\s+"([^"]*)"$', line)
        if m:
            if pending_key is not None:
                stack[-1][pending_key] = {}
                pending_key = None
            stack[-1][m.group(1)] = m.group(2)
            continue
        m = re.match(r'^"([^"]+)"$', line)
        if m:
            if pending_key is not None:
                stack[-1][pending_key] = {}
            pending_key = m.group(1)
            continue
    return root


def write_acf(path: Path, data: dict) -> None:
    """按 Steam 常见风格写出 ACF（tab 缩进，值用两个 tab）。"""

    def emit(obj: dict, indent: int, out: list[str]) -> None:
        pad = "\t" * indent
        for k, v in obj.items():
            if isinstance(v, dict):
                out.append(f'{pad}"{k}"')
                out.append(f"{pad}{{")
                emit(v, indent + 1, out)
                out.append(f"{pad}}}")
            else:
                out.append(f'{pad}"{k}"\t\t"{v}"')

    lines = ["\"AppState\"", "{"]
    emit(data, 1, lines)
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Steam appinfo.vdf 二进制解析（字符串表 + key 索引）
# ---------------------------------------------------------------------------

_APPINFO_CACHE: dict[str, dict] = {}


def _read_bytes_retry(path: Path, attempts: int = 3) -> bytes:
    """Steam 运行中可能短暂持有 appinfo.vdf，读失败时重试几次。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return path.read_bytes()
        except (PermissionError, OSError) as e:  # noqa: PERF203
            last = e
            time.sleep(0.2 * (i + 1))
    raise last if last else OSError("read failed")


def _vdf_value_parser(strings: list[str]):
    """
    构造二进制 VDF 值解码器（闭包持有「字符串表下标 → 文本」映射）。
    老单文件与新分片的记录体编码完全相同，两者共用此解码器。
    """

    def parse_value(buf: bytes, q: int, t: int):
        """返回 (值, 新位置)。未知类型时抛 _Unknown，由调用方决定是否跳过。"""
        if t == 0x08:
            return None, q
        if t == 0x00:
            obj: dict = {}
            while q < len(buf):
                st = buf[q]
                q += 1
                if st == 0x08:
                    return obj, q
                if q + 4 > len(buf):
                    return obj, q
                kidx = struct.unpack_from("<I", buf, q)[0]
                q += 4
                key = strings[kidx] if kidx < len(strings) else f"idx{kidx}"
                val, q = parse_value(buf, q, st)
                obj[key] = val
            return obj, q
        if t == 0x01:
            end = buf.find(b"\x00", q)
            if end < 0:
                return "", len(buf)
            return buf[q:end].decode("utf-8", "replace"), end + 1
        if t == 0x02:
            return struct.unpack_from("<i", buf, q)[0], q + 4
        if t == 0x03:
            return struct.unpack_from("<f", buf, q)[0], q + 4
        if t == 0x04:
            return struct.unpack_from("<I", buf, q)[0], q + 4
        if t == 0x07:
            return struct.unpack_from("<Q", buf, q)[0], q + 8
        if t == 0x0A:
            return struct.unpack_from("<q", buf, q)[0], q + 8
        if t == 0x06:
            return None, q
        if t == 0x05:
            # utf-16le cstring
            end = q
            while end + 1 < len(buf):
                if buf[end] == 0 and buf[end + 1] == 0:
                    break
                end += 2
            return buf[q:end].decode("utf-16le", "replace"), end + 2
        raise ValueError(f"unknown type {t:#x}")

    return parse_value


def _vdf_decode_children(buf: bytes, start: int, strings: list[str], parse_value) -> dict:
    """
    从 buf[start]（第一个 type 字节）起解出连续的 (type, key 索引, value) 条目，
    遇 0x08（map 结束）或缓冲结束停止；单个字段解不了就停在该处，保留已解出的部分。
    老单文件记录从 65 起、新分片记录从 25 起，除起点外格式一致。
    """
    obj: dict = {}
    pp = start
    while pp < len(buf):
        st = buf[pp]
        pp += 1
        if st == 0x08:
            break
        if pp + 4 > len(buf):
            break
        kidx = struct.unpack_from("<I", buf, pp)[0]
        pp += 4
        key = strings[kidx] if kidx < len(strings) else f"idx{kidx}"
        try:
            val, pp = parse_value(buf, pp, st)
        except (ValueError, struct.error, IndexError):
            break
        obj[key] = val
    return obj


def _cache_put(key: str, value: dict) -> None:
    """写解析缓存，最多保留 2 条（单文件与分片可能短时并存）。"""
    global _APPINFO_CACHE
    _APPINFO_CACHE[key] = value
    while len(_APPINFO_CACHE) > 2:
        _APPINFO_CACHE.pop(next(iter(_APPINFO_CACHE)))


def _parse_appinfo_single(app_id: int, p: Path) -> dict:
    """
    解析老版单文件 appcache\\appinfo.vdf，返回指定 appid 的键值树。
    结果按 (路径, mtime, size) 缓存，避免 GUI 每次刷新都重新解几十 MB。
    """
    try:
        st = p.stat()
        cache_key = f"{p}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        cache_key = f"{p}|miss"

    cached = _APPINFO_CACHE.get(cache_key)
    if cached is not None:
        return cached.get(app_id, {}) if isinstance(cached, dict) else {}

    try:
        data = _read_bytes_retry(p)
    except OSError:
        return {}

    if len(data) < 16:
        return {}
    str_off = struct.unpack_from("<Q", data, 8)[0]
    if str_off >= len(data) or str_off < 16:
        return {}
    count = struct.unpack_from("<I", data, str_off)[0]
    pos = str_off + 4
    strings: list[str] = []
    for _ in range(count):
        if pos >= len(data):
            break
        end = data.find(b"\x00", pos)
        if end < 0:
            break
        strings.append(data[pos:end].decode("utf-8", "replace"))
        pos = end + 1
    parse_value = _vdf_value_parser(strings)

    apps: dict[int, dict] = {}
    p2 = 16
    while p2 + 8 <= str_off:
        aid, size = struct.unpack_from("<II", data, p2)
        blob = data[p2 + 8: p2 + 8 + size]
        p2 += 8 + size
        if size < 64:
            continue
        try:
            # offset 60: root map type(0x00) + key index，之后才是 children
            if blob[60] != 0x00:
                continue
            apps[aid] = _vdf_decode_children(blob, 65, strings, parse_value)
        except (struct.error, IndexError):
            continue

    _cache_put(cache_key, apps)
    return apps.get(app_id, {})


# 新版 Steam appinfo 分片（appcache\appinfo\appinfo_XX.vdf，共 256 个）头部布局：
#   [0]  u32 magic 0x0756442A
#   [4]  u32 版本
#   [8]  u32 分片号    [12] u32 分片总数
#   [24] u32 字符串表条数   [28] u32 字符串表字节数   [32] u32 数据区字节数
# 头之后到字符串表之前是索引区：若干 (appid, offset, size) 三元组，
# offset 相对数据区起点（数据区 = 文件尾 data_size 字节）。
# 记录体 = [20 字节 SHA1][0x00][root key u32][children...]，children 从 25 起，
# 编码与老单文件一致，所以共用 _vdf_value_parser / _vdf_decode_children。
APPINFO_SHARD_MAGIC = 0x0756442A


def _parse_appinfo_shard_data(data: bytes, app_id: int) -> dict:
    """在单个分片的字节流里找 app_id 的记录；未命中或格式不符返回 {}。"""
    if len(data) < 36 or struct.unpack_from("<I", data, 0)[0] != APPINFO_SHARD_MAGIC:
        return {}
    str_count, str_size, data_size = struct.unpack_from("<III", data, 24)
    data_start = len(data) - data_size
    string_start = data_start - str_size
    if not (36 <= string_start < data_start <= len(data)):
        return {}

    strings: list[str] = []
    pos = string_start
    for _ in range(str_count):
        if pos >= data_start:
            break
        end = data.find(b"\x00", pos)
        if end < 0 or end >= data_start:
            break
        strings.append(data[pos:end].decode("utf-8", "replace"))
        pos = end + 1
    parse_value = _vdf_value_parser(strings)

    i = 36
    while i + 12 <= string_start:
        aid, off, size = struct.unpack_from("<III", data, i)
        if aid == app_id and size >= 64 and 0 <= off and off + size <= data_size:
            rec = data[data_start + off: data_start + off + size]
            if len(rec) >= 25 and rec[20] == 0x00:
                obj = _vdf_decode_children(rec, 25, strings, parse_value)
                got = obj.get("appid")
                if obj and got in (None, app_id, str(app_id)):
                    return obj
        i += 4
    return {}


def _parse_appinfo_shards(app_id: int) -> dict:
    """
    新版 Steam（约 2026-11 起）不再写单文件 appinfo.vdf，而是把内容拆到
    appcache\\appinfo\\appinfo_XX.vdf 共 256 个分片。逐片扫索引区找 appid，
    命中即返回；结果按「分片数 + 最新 mtime + 总字节数」缓存。
    """
    base = APPINFO_PATH.parent / "appinfo"
    try:
        files = sorted(base.glob("appinfo_*.vdf"))
    except OSError:
        return {}
    if not files:
        return {}
    newest, total = 0, 0
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        newest = max(newest, st.st_mtime_ns)
        total += st.st_size
    cache_key = f"{base}|shards|{app_id}|{len(files)}|{newest}|{total}"
    cached = _APPINFO_CACHE.get(cache_key)
    if cached is not None:
        return cached.get(app_id, {}) if isinstance(cached, dict) else {}

    hit: dict = {}
    read_error = False
    for f in files:
        try:
            data = _read_bytes_retry(f)
        except OSError:
            read_error = True
            continue
        hit = _parse_appinfo_shard_data(data, app_id)
        if hit:
            break
    if read_error and not hit:
        # Steam 正持有某个分片，这次读不全 —— 不写缓存，下次再试
        return {}
    _cache_put(cache_key, {app_id: hit} if hit else {})
    return hit


def parse_appinfo(app_id: int = APP_ID, path: Path | None = None) -> dict:
    """
    解析本机 Steam appinfo，返回指定 appid 的键值树。
    优先老版单文件 appcache\\appinfo.vdf；该文件不存在（新版 Steam 已拆分片）
    或不含目标 app 时，回退到 appcache\\appinfo\\appinfo_XX.vdf 分片。
    显式传 path 时只按老单文件格式解析该路径。
    """
    if path is not None:
        p = Path(path)
        return _parse_appinfo_single(app_id, p) if p.exists() else {}

    if APPINFO_PATH.exists():
        app = _parse_appinfo_single(app_id, APPINFO_PATH)
        if app:
            return app
        # 单文件残留但没有目标 app（例如 Steam 升级后留下的旧文件）→ 再试分片
    return _parse_appinfo_shards(app_id)


def extract_manifest_info(app: dict) -> dict:
    """从 appinfo 抽出 public 分支 buildid 与 depot manifest。"""
    if not app:
        return {"buildid": None, "manifest": None, "depot_size": None, "timeupdated": None}

    depots = app.get("depots") or {}
    branches = depots.get("branches") or {}
    public = branches.get("public") or {}
    buildid = public.get("buildid")
    timeupdated = public.get("timeupdated")
    depot = depots.get(DEPOT_ID) or {}
    manifests = depot.get("manifests") or {}
    public_m = manifests.get("public") or {}
    # gid 即 manifest id（SteamDB 上也叫 ManifestID）
    manifest = public_m.get("gid") or public_m.get("manifest")
    size = public_m.get("size")
    return {
        "buildid": str(buildid) if buildid is not None else None,
        "manifest": str(manifest) if manifest is not None else None,
        "depot_size": str(size) if size is not None else None,
        "timeupdated": timeupdated,
    }


def extract_launch_options(app: dict) -> list[str]:
    """列出 appinfo 里声明的启动参数（终末地的 DX 切换就藏在这里）。"""
    out: list[str] = []
    cfg = (app or {}).get("config") or {}
    for entry in (cfg.get("launch") or {}).values():
        if isinstance(entry, dict):
            a = (entry.get("arguments") or "").strip()
            if a:
                desc = entry.get("description") or ""
                out.append(f"{a}  ({desc})" if desc else a)
    return out


# ---------------------------------------------------------------------------
# 目录体积
# ---------------------------------------------------------------------------

def folder_size(path: Path, progress=None) -> int:
    """
    递归统计目录字节数。用 os.scandir（stat 结果被缓存）比 rglob+is_file 快很多，
    终末地 60+ GB 的目录差别很明显。
    对目录联接/符号链接按 realpath 去重，避免重复统计或死循环。
    """
    p = Path(path)
    if not p.exists():
        return 0
    total = 0
    files = 0
    stack: list[Path] = [p]
    seen: set[str] = set()
    while stack:
        d = stack.pop()
        try:
            key = os.path.realpath(d).lower()
        except OSError:
            key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                            if progress is not None and files % 20000 == 0:
                                progress(total, files)
                    except OSError:
                        continue
        except OSError:
            continue
    if progress is not None:
        progress(total, files)
    return total


# ---------------------------------------------------------------------------
# SteamDB 字符串解析
# ---------------------------------------------------------------------------

def parse_steamdb_copy(text: str) -> dict:
    """支持 ' -app 4732690 -depot 4732691 -manifest 123 ' 或纯数字。"""
    out: dict = {}
    m = re.search(r"-manifest\s+(\d+)", text)
    if m:
        out["manifest"] = m.group(1)
    m = re.search(r"-buildid\s+(\d+)", text)
    if m:
        out["buildid"] = m.group(1)
    m = re.search(r"-depot\s+(\d+)", text)
    if m:
        out["depot"] = m.group(1)
    if not out and re.fullmatch(r"\d+", text.strip()):
        out["manifest"] = text.strip()
    return out


# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def check_environment() -> dict:
    """写 ACF / 建联接之前的前置条件体检。"""
    paths = resolve_game_paths()
    problems: list[str] = []
    warnings: list[str] = []

    # Steam
    if not STEAM_ROOT.exists():
        problems.append(f"未找到 Steam 根目录: {STEAM_ROOT}")
    if not STEAMAPPS.exists():
        problems.append(f"未找到 steamapps: {STEAMAPPS}")

    # appinfo（老单文件或新版分片，任一存在都算有数据源）
    shard_dir = APPINFO_PATH.parent / "appinfo"
    has_appinfo = APPINFO_PATH.exists() or (
        shard_dir.is_dir() and any(shard_dir.glob("appinfo_*.vdf"))
    )
    if not has_appinfo:
        warnings.append(
            f"未找到 {APPINFO_PATH} 及分片目录 {shard_dir}；"
            "请先启动一次 Steam（联网）让它缓存 appinfo，否则只能靠手动填 manifest/buildid"
        )
    else:
        info = extract_manifest_info(parse_appinfo())
        if not info.get("manifest"):
            warnings.append("appinfo 里没有 4732690 的 depot 信息，可能 Steam 还没拉到")

    # ACF 可写性
    if ACF_PATH.exists():
        if not os.access(ACF_PATH, os.W_OK):
            problems.append(f"appmanifest 只读（需管理员）: {ACF_PATH}")
    else:
        parent = ACF_PATH.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            problems.append(f"无法在 steamapps 目录创建文件（需管理员）: {parent}")
        elif not parent.exists():
            problems.append(f"steamapps 目录不存在: {parent}")

    # 官服目录
    official = OFFICIAL_GAME_DIR
    if official and Path(official).exists():
        if _is_inside_steam(Path(official)):
            warnings.append(
                f"只在 Steam 库里找到 Endfield.exe，没有独立的官服目录: {official}"
            )
    else:
        warnings.append(
            "没找到官服游戏目录（含 Endfield.exe 的那层）。"
            "请在 GUI 里手动填写，或用「查找 Endfield.exe…」定位"
        )

    # Steam 侧安装目录（联接的落点）
    jinfo = junction_info()
    if jinfo["is_junction"]:
        warnings.append(f"Steam 安装目录已是目录联接 -> {jinfo.get('target')}")
    elif INSTALL_DIR.exists():
        # 是真实文件夹：建联接前必须先改名让位，Program Files 下需要管理员
        if not is_admin():
            problems.append(
                f"Steam 安装目录是真实文件夹，建联接前需要先改名让位；"
                f"请以管理员身份运行（当前非管理员）: {INSTALL_DIR}"
            )
    else:
        # 不存在 —— 什么都不用做，mklink 会直接创建
        pass

    return {
        "admin": is_admin(),
        "steam_root": str(STEAM_ROOT),
        "appinfo_exists": has_appinfo,
        "acf_path": str(ACF_PATH),
        "acf_exists": ACF_PATH.exists(),
        "install_dir": str(INSTALL_DIR),
        "install_dir_exists": INSTALL_DIR.exists(),
        "official_dir": str(official) if official else None,
        "official_dir_exists": bool(official and Path(official).exists()),
        "junction": jinfo,
        "problems": problems,
        "warnings": warnings,
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    resolve_game_paths()
    payload = {
        "app_id": APP_ID,
        "depot_id": DEPOT_ID,
        "acf": str(ACF_PATH),
        "install_dir": str(INSTALL_DIR),
        "acf_exists": ACF_PATH.exists(),
        "state": None,
        "appinfo": None,
        "junction": junction_info(),
        "size": 0,
        "link_targets": [],
    }

    state: dict = {}
    if ACF_PATH.exists():
        acf = read_acf(ACF_PATH)
        state = acf.get("AppState", acf)
    else:
        print(f"[!] 未找到 appmanifest: {ACF_PATH}")
        print("    用 apply 会按模板新建一份。")
    payload["state"] = state

    print(f"游戏      : {GAME_DISPLAY} ({GAME_NAME})")
    print(f"App ID    : {APP_ID}")
    print(f"Depot ID  : {DEPOT_ID}")
    print(f"主程序    : {GAME_EXE_NAME}")
    print(f"Steam 库  : {STEAM_ROOT}" + (f"   [已锁定]" if PINNED_LIBRARY else ""))
    print(f"ACF 路径  : {ACF_PATH}")
    if state:
        print(f"  name              : {state.get('name')}")
        print(f"  StateFlags        : {state.get('StateFlags')}  (4 = 已完整安装)")
        print(f"  buildid           : {state.get('buildid')}")
        print(f"  TargetBuildID     : {state.get('TargetBuildID')}")
        print(f"  SizeOnDisk        : {state.get('SizeOnDisk')}")
        print(f"  LastUpdated       : {state.get('LastUpdated')}")
        print(f"  AutoUpdateBehavior: {state.get('AutoUpdateBehavior')}")
        print(f"  ScheduledAutoUpdate: {state.get('ScheduledAutoUpdate')}")
        depots = state.get("InstalledDepots") or {}
        for did, dval in depots.items():
            if isinstance(dval, dict):
                print(f"  InstalledDepots[{did}].manifest = {dval.get('manifest')}")
                print(f"  InstalledDepots[{did}].size     = {dval.get('size')}")

    print(f"\nSteam 安装目录: {INSTALL_DIR}")
    jinfo = junction_info()
    if jinfo["is_junction"]:
        print(f"  类型: 目录联接 -> {jinfo.get('target')}")
    else:
        print("  类型: 普通文件夹")
    if INSTALL_DIR.exists():
        sz = folder_size(INSTALL_DIR)
        payload["size"] = sz
        print(f"  实际体积: {sz}  ({sz / 1024**3:.2f} GiB)")
    else:
        print("  [!] 目录不存在（联接后会自动创建）")

    official = OFFICIAL_GAME_DIR
    print(f"\n官服目录: {official if official else '未找到'}")
    if official and Path(official).exists():
        if str(official).lower() == str(INSTALL_DIR).lower():
            print("  与 Steam 安装目录相同")
        else:
            print(f"  实际体积: ", end="")
            osz = folder_size(Path(official))
            print(f"{osz}  ({osz / 1024**3:.2f} GiB)")
        payload["official_dir"] = str(official)

    app = parse_appinfo()
    info = extract_manifest_info(app)
    payload["appinfo"] = info
    print("\nSteam appinfo 自动获取:")
    if app:
        print(f"  public buildid  : {info.get('buildid')}")
        print(f"  depot manifest  : {info.get('manifest')}")
        print(f"  depot size      : {info.get('depot_size')}")
        opts = extract_launch_options(app)
        if opts:
            print("  启动参数        : " + " | ".join(opts))
    else:
        print(f"  [!] 读取失败或无记录: {APPINFO_PATH}")

    cur_manifest = ((state.get("InstalledDepots") or {}).get(DEPOT_ID) or {}).get("manifest") \
        if isinstance(state.get("InstalledDepots"), dict) else None
    if state and info.get("buildid"):
        if state.get("buildid") != info.get("buildid") or cur_manifest != info.get("manifest"):
            print("  => ACF 与 appinfo 不一致，Steam 仍会触发更新，建议执行 apply")
        else:
            print("  => manifest / buildid 已与最新一致")
    elif not app:
        print(f"  => 无 appinfo 可比对，只能靠 SteamDB 手动值")

    print("\nSteam 库 / 可用联接目标:")
    rows = steam_link_targets()
    payload["link_targets"] = rows
    for i, t in enumerate(rows):
        mark = " [有游戏]" if t["has_game"] else ""
        print(f"  {i}. {t['label']}{mark}")
        print(f"     ACF: {t['appmanifest']} {'✓' if t['appmanifest_exists'] else '×'}")
        print(f"     DIR: {t['install_dir']} {'✓' if t['install_dir_exists'] else '×'}")

    if getattr(args, "json", False):
        print("\n--- JSON ---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _resolve_manifest_buildid(args: argparse.Namespace) -> tuple:
    """
    统一解析 manifest / buildid / depot_size，返回 (manifest, buildid, size, last_updated, 来源说明)。
    优先级：命令行 > SteamDB 粘贴串 > appinfo > 内置快照。
    """
    auto = extract_manifest_info(parse_appinfo())
    manifest = args.manifest or auto.get("manifest")
    buildid = args.buildid or auto.get("buildid")
    size = args.depot_size or auto.get("depot_size")
    source = "命令行" if (args.manifest or args.buildid) else ("appinfo" if auto.get("manifest") else "")

    if args.from_steamdb:
        parsed = parse_steamdb_copy(args.from_steamdb)
        if parsed.get("manifest"):
            manifest = parsed["manifest"]
            source = "SteamDB 粘贴串"
        if parsed.get("buildid"):
            buildid = parsed["buildid"]

    # 兜底快照
    if not manifest:
        manifest = FALLBACK_MANIFEST
        print(
            f"[!] appinfo 与命令行都没给出 manifest，回退到内置快照（{SNAPSHOT_DATE}）"
            f": {manifest}"
        )
        print("    这个值大概率已经过期，建议从 SteamDB 重新复制：")
        print(f"    https://steamdb.info/depot/{DEPOT_ID}/manifests/")
        source = f"内置快照({SNAPSHOT_DATE})，可能已过期"
    if not buildid:
        buildid = FALLBACK_BUILDID
        print(f"[!] buildid 回退到内置快照（{SNAPSHOT_DATE}）: {buildid}")

    last_updated = args.last_updated or auto.get("timeupdated") or int(time.time())
    return manifest, buildid, size, last_updated, source


def _size_source_dir() -> Path | None:
    """
    统计体积时该量哪个目录。

    Steam 侧目录可能还没建（首次转换），这时要量官服目录 ——
    反正联接之后两者指向同一份数据，SizeOnDisk 填谁都无所谓，
    但不能是 0（Steam 会认为安装不完整）。
    """
    if INSTALL_DIR.exists():
        return INSTALL_DIR
    if OFFICIAL_GAME_DIR and Path(OFFICIAL_GAME_DIR).exists():
        return Path(OFFICIAL_GAME_DIR)
    return None


def cmd_apply(args: argparse.Namespace) -> int:
    resolve_game_paths()
    manifest, buildid, depot_size, last_updated, source = _resolve_manifest_buildid(args)
    print(f"[来源] manifest/buildid 取自: {source}")

    # ACF 不存在则先按模板建一份
    if not ACF_PATH.exists():
        print(f"[ACF] 文件不存在，将按模板创建: {ACF_PATH}")
        src = _size_source_dir()
        sz0 = folder_size(src) if src else 0
        if src and src != INSTALL_DIR:
            print(f"[体积] 取自官服目录 {src}")
        create_acf(manifest, buildid, sz0 or int(depot_size or 0), last_updated)

    # 备份
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / BACKUP_NAME
    if not bak.exists() or args.force_backup:
        shutil.copy2(ACF_PATH, bak)
        print(f"[备份] {ACF_PATH} -> {bak}")
    else:
        print(f"[备份] 已存在，跳过: {bak}（--force-backup 可覆盖）")

    acf = read_acf(ACF_PATH)
    state = acf.get("AppState", acf)

    # 体积：优先实测（教程要求填文件夹大小）
    src = _size_source_dir()
    print(f"[体积] 统计 {src} …" if src else "[体积] 找不到可统计的目录")
    size_on_disk = folder_size(src) if src else 0
    if size_on_disk <= 0:
        try:
            size_on_disk = int(state.get("SizeOnDisk") or 0)
        except (TypeError, ValueError):
            size_on_disk = 0
    print(f"[体积] {size_on_disk} ({size_on_disk / 1024**3:.2f} GiB)")

    # buildid / TargetBuildID 对齐，避免「有待更新」
    target_build = args.buildid or buildid or state.get("TargetBuildID") or state.get("buildid")
    if args.use_target_as_build:
        target_build = state.get("TargetBuildID") or target_build

    depots = state.setdefault("InstalledDepots", {})
    d = depots.setdefault(DEPOT_ID, {})
    d["manifest"] = str(manifest)
    if size_on_disk:
        d["size"] = str(size_on_disk)
    elif depot_size:
        d["size"] = str(depot_size)

    state["buildid"] = str(target_build) if target_build else state.get("buildid")
    state["TargetBuildID"] = str(target_build) if target_build else state.get("TargetBuildID")
    state["LastUpdated"] = str(last_updated)
    if size_on_disk:
        state["SizeOnDisk"] = str(size_on_disk)
    # StateFlags 4 = fully installed
    state["StateFlags"] = "4"
    # AutoUpdateBehavior：1 = 仅启动时更新
    state["AutoUpdateBehavior"] = str(
        args.auto_update_behavior if args.auto_update_behavior is not None else 1
    )
    # 取消排队中的自动更新
    state["ScheduledAutoUpdate"] = "0"
    for k in ("BytesToDownload", "BytesDownloaded", "BytesToStage", "BytesStaged"):
        state[k] = "0"
    state["UpdateResult"] = "0"
    # **把「已暂存的更新」记录一起清掉。**
    # Steam 一旦给这个 app 排过一次更新，就会在 ACF 里写一个 StagedDepots
    # （记着要下多少字节、暂存到哪个 manifest）。光把字节数清零**不够** ——
    # Steam 读到 StagedDepots 还是会显示「更新中」，然后去续那个几十 GB 的
    # 下载（实测 2026-10-20：删光 shim 之后 Steam 排了 62 GB 的更新，
    # 就算把 shim 装回去，它也还在「更新中」）。
    # apply 的职责就是「阻止 Steam 更新覆盖」，所以这一段必须一起清。
    if state.pop("StagedDepots", None) is not None:
        print("  已清除 Steam 的 StagedDepots（暂存更新）记录")

    write_acf(ACF_PATH, state)

    print("\n[写入完成]")
    print(f"  InstalledDepots[{DEPOT_ID}].manifest = {manifest}")
    print(f"  buildid / TargetBuildID = {state['buildid']}")
    print(f"  SizeOnDisk = {state['SizeOnDisk']}")
    print(f"  LastUpdated = {state['LastUpdated']}")
    print("\n请完全退出并重启 Steam 后再启动游戏。")
    if LAUNCH_DX11:
        print(f"如需 DX11：Steam 里右键 {GAME_DISPLAY} → 属性 → 启动选项 填入 {LAUNCH_DX11}")
    print("若 Steam 仍显示更新，可再次执行 apply，或用 --from-steamdb 粘贴最新 manifest。")
    return 0


def cmd_restore(_: argparse.Namespace) -> int:
    resolve_game_paths()
    bak = BACKUP_DIR / BACKUP_NAME
    if not bak.exists():
        print(f"[!] 没有备份: {bak}")
        return 1
    shutil.copy2(bak, ACF_PATH)
    print(f"[恢复] {bak} -> {ACF_PATH}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    app = parse_appinfo()
    info = extract_manifest_info(app)
    if not app:
        info["source"] = "unavailable"
    elif APPINFO_PATH.exists():
        info["source"] = "appinfo.vdf"
    else:
        info["source"] = "appinfo 分片"
    info["appinfo_path"] = str(APPINFO_PATH)
    info["launch_options"] = extract_launch_options(app)
    info["snapshot_date"] = SNAPSHOT_DATE
    if getattr(args, "json", False):
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0 if app else 1


def cmd_check(_: argparse.Namespace) -> int:
    rep = check_environment()
    print(f"管理员权限   : {'是' if rep['admin'] else '否'}")
    print(f"Steam 根     : {rep['steam_root']}")
    print(f"appinfo      : {'存在' if rep['appinfo_exists'] else '缺失'}"
          f"（单文件或 appcache\\appinfo 分片）")
    print(f"ACF          : {rep['acf_path']}  ({'存在' if rep['acf_exists'] else '不存在'})")
    print(f"Steam 安装目录: {rep['install_dir']}  ({'存在' if rep['install_dir_exists'] else '不存在'})"
          "   <- 联接落点")
    print(f"官服目录     : {rep['official_dir']}  "
          f"({'存在' if rep['official_dir_exists'] else '未找到'})"
          "   <- 联接来源")
    j = rep["junction"]
    if j["is_junction"]:
        print(f"当前联接     : -> {j.get('target')}")
    else:
        print("当前联接     : 无")
    for w in rep["warnings"]:
        print(f"[警告] {w}")
    for p in rep["problems"]:
        print(f"[阻塞] {p}")
    if not rep["problems"]:
        print("\n环境检查通过。")
        return 0
    print("\n存在阻塞项，请先处理上面的 [阻塞]。")
    return 2


# ---------------------------------------------------------------------------
# 目录联接（mklink /j）
# ---------------------------------------------------------------------------

def junction_info(path: Path | None = None) -> dict:
    """返回 {exists, is_junction, target, real_dir}。

    注意 path 必须为 None 时才在运行时读全局 INSTALL_DIR —— 写死默认值会
    捕获导入那一刻的值，resolve_game_paths() 之后就不对了。
    """
    p = Path(path) if path is not None else INSTALL_DIR
    out = {
        "exists": p.exists() or p.is_symlink(),
        "is_junction": False,
        "target": None,
        "real_dir": str(p),
    }
    try:
        if not p.exists() and _is_reparse_point(p):
            # 指向已删除目标的联接：exists() 为 False，但仍是联接
            out["exists"] = True
        if p.is_symlink() or _is_reparse_point(p):
            out["is_junction"] = True
            t = _read_link_target(p)
            out["target"] = t
            if t:
                out["real_dir"] = t
    except OSError as e:
        out["error"] = str(e)
    return out


def _is_reparse_point(path: Path) -> bool:
    try:
        import ctypes

        GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
        GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        GetFileAttributesW.restype = ctypes.c_uint32
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = GetFileAttributesW(str(path))
        return attrs != 0xFFFFFFFF and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _read_link_target(path: Path) -> str | None:
    # Python 3.8+ 的 resolve() 在 Windows 上能穿透目录联接
    try:
        t = Path(path).resolve()
        return str(t)
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["cmd", "/c", "dir", "/al", str(path.parent)],
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in out.splitlines():
            if path.name in line and "[" in line:
                m = re.search(r"\[(.+)\]", line)
                if m:
                    return m.group(1).strip()
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# 目录联接（mklink /j）—— **已退役**
# ---------------------------------------------------------------------------
#
# 退役原因（两条，都是实测/踩坑得来的）：
#   1. 鉴权过不去：联接下 Steam 直接拉起官服目录里的 Endfield.exe，游戏的
#      **直接父进程**是 steam.exe，不是 Games.exe，U8 启动鉴权固定返回
#      errorCode 10100「请通过官方启动器打开游戏」（16 组实测无一例外）。
#   2. 会毁掉用户的游戏安装：建联接前要把已存在的真实目录**整目录改名**让位。
#      那段代码已经**删掉**，不再存在于本文件里 —— 不是「不用」，是「没有」。
#
# 现在走的是 shim 方案：把一个同名 Endfield.exe 放进 Steam 安装目录，
# 由它按**父进程**分流（父进程是 steam.exe 就启动游戏，其它情况打开配置主页）。
#
# 退役不是「静默删掉入口」：老脚本可能还在调 `junction create`，
# 所以入口保留，但**只打印说明并返回非零**，一个文件都不碰。

JUNCTION_RETIRED_EXIT_CODE = 3
JUNCTION_RETIRED_NOTICE = """\
[已退役] 目录联接（mklink /j）方案已退役，本命令**不再做任何文件操作**。

  退役原因 1（实测）：联接下 Steam 直接拉起官服目录里的 Endfield.exe，
    游戏的**直接父进程**是 steam.exe，不是 Games.exe，U8 启动鉴权固定返回
    errorCode 10100「请通过官方启动器打开游戏」（16 组实测无一例外）。

  退役原因 2（踩坑）：建联接前要把已存在的真实目录**整目录改名**让位 ——
    那会把你整个游戏安装搬走。那段代码已经删掉，不会再执行。

替代方案：
    python endfield_updater.py shim install
  在 Steam 安装目录放一个同名 Endfield.exe shim，由它按父进程分流：
    父进程是 steam.exe -> 走现有的启动游戏逻辑（launch_mode 照旧生效）
    其它情况（含双击）-> 打开配置主页，不启动游戏

  清理历史遗留的联接（只删链接本身，不动目标目录）：
    python endfield_updater.py shim unlink"""


def create_junction(link: Path, target: Path, force: bool = False) -> int:
    """**已退役**。建目录联接这条路整体停用。

    保留函数名是为了不打断老脚本：调它会拿到一段清楚的说明和一个非零退出码，
    而不是静默失败、也不是把用户的游戏目录改名。

    这里**一个字节都不写**：不建联接、不改名、不删任何东西。
    """
    print(JUNCTION_RETIRED_NOTICE)
    print()
    print(f"[退役] 本次请求：link={link}  target={target}  force={force}")
    print("[退役] 未做任何文件操作。")
    return JUNCTION_RETIRED_EXIT_CODE


def remove_junction(link: Path | None = None, delete_files: bool = False) -> int:
    """删除目录联接。rmdir 只删联接本身，不会动目标文件。

    **这个函数还留着**：联接方案虽然退役了，但历史机器上可能还留着一条联接，
    清理它是必要的（`shim install` 也会先拆它）。删的仍然只是链接本身。
    """
    target = clean_path(link) if link is not None else INSTALL_DIR
    info = junction_info(target)
    if not info["is_junction"]:
        print(f"[!] 不是目录联接: {target}")
        return 1
    cmd = f'rmdir "{target}"'
    print(f"[联接-退役] 删除（只删链接本身）: {cmd}")
    r = os.system(cmd)  # noqa: S605
    if r != 0:
        try:
            Path(target).rmdir()
        except OSError as e:
            print(f"[!] 删除失败: {e}")
            return 2
    print(f"[联接-退役] 已删除: {target}（目标目录 {info.get('target')} 未受影响）")
    print("[提示] 联接方案本身已退役。要用现行方案请跑: "
          "python endfield_updater.py shim install")
    return 0


# ---------------------------------------------------------------------------
# Steam 侧 shim（现行方案，取代目录联接）
# ---------------------------------------------------------------------------
#
# 为什么不能只靠联接：启动鉴权比对的是 Endfield.exe 的**直接父进程**映像路径。
# 联接下 Steam 直接拉起官服目录里的 Endfield.exe，父进程是 steam.exe，
# 不是 Games.exe，SDK 固定返回 errorCode 10100「请通过官方启动器打开游戏」。
# 目录联接这条路线已经被实测证伪（16 组数据，无一例外）。
#
# 现在改成：Steam 拉起安装目录里一个同名 shim Endfield.exe，
# shim 只做一件事 —— 调起官方启动器并把启动交回去：
#
#     steam.exe -> shim Endfield.exe -> hypergryphlauncher-<code>://start?ta=endfield
#                                             -> Launcher.exe
#                                                 -> Games.exe
#                                                     -> 官服目录里的 Endfield.exe（code 0）
#
# shim 不碰游戏本体、不改任何官方文件；账号 token 仍由 Games.exe 经共享内存
# 注入给游戏，所以必须走真启动器，没有别的办法。
#
# shim 自身是 requireAdministrator（Endfield.exe 也是）：提权一次后整条链继承，
# 只弹一次 UAC。若 shim 不提权，链路到 Endfield.exe 那步会 WinError 740。

SHIM_VERSION = "1.0.0"
SHIM_EXE_NAME = GAME_EXE_NAME            # 必须同名：Steam 按 appinfo 里的名字拉起
SHIM_CONFIG_NAME = "shim_config.json"
SHIM_LOG_NAME = "shim.log"
SHIM_INTERNAL_DIR = "_internal"          # PyInstaller onedir 的依赖目录
LAUNCHER_EXE_NAME = "Launcher.exe"
# 启动器 deep link。走协议 URL 才能自动开始游戏；--game=endfield 只会打开界面。
SHIM_SCHEME_PREFIX = "hypergryphlauncher-"
SHIM_DEEP_LINK_TAIL = "://start?ta=endfield"
# 藏官方启动器窗口（shim --hide 那个分身用）。shim 内部也有一份同值的兜底，
# 两边都缺字段时按 shim 的默认值走。
#   hide_launcher        总开关。**默认 false** —— 见下面 2026-10-17 的实测。
#   hide_ms              实际隐藏窗口的最长时长
#   hide_settle_ms       观察期，这段时间内一个窗口都不碰
#   hide_grace_ms        启动宽限期，到期还没等到游戏进程就还原窗口
#   hide_taskbar         顺带从任务栏 / Alt-Tab 里摘掉
#   hide_if_preexisting  连「启动器本来就没关」的窗口也一起藏（默认不动它）
#   keep_shim_until_game shim 等游戏进程出现才退出，Steam 那十几秒显示「运行中」
#
# 2026-10-17 19:41 / 19:49 两次实测：hide_launcher=true 时，hider 从 0 秒开始
# 按窗口，两次 Games.exe 都没走到 DoMainBtnClicked / cgsu create game process，
# 游戏进程压根没被创建。而同一天不藏窗口的会话一切正常。
# 所以藏窗口默认关掉 —— 外观是锦上添花，启动器起不来是灾难。
SHIM_HIDE_DEFAULTS = {
    "hide_launcher": False,
    "hide_ms": 60000,
    "hide_settle_ms": 15000,
    "hide_grace_ms": 90000,
    "hide_taskbar": True,
    "hide_if_preexisting": False,
    "keep_shim_until_game": False,
}
# ---------------------------------------------------------------------------
# Games.exe 伪装（实验性，**默认关闭**）
# ---------------------------------------------------------------------------
#
# 现行方案必然经过官方启动器（shim 调深链 -> Launcher -> Games.exe -> 游戏），
# 代价是启动器那一层界面挡在前面。伪装方案想去掉这一层：
#
#     steam.exe
#       └─ <Steam安装目录>\Endfield.exe     调度器（就是本 shim）
#            └─ <Steam安装目录>\Games.exe   同一二进制改名后的副本
#                 └─ 官服游戏目录\Endfield.exe
#
# 完全不碰官方启动器，界面根本不存在。
#
# 但它依赖一个**尚未实测**的前提：SDK 比对父进程时比文件名还是完整路径。
# 「启动鉴权判定实验」的 B 组就是测这个的（B 组 PASS = 只比文件名 = 可行）。
# 那套实验脚本已在 2026-10-21 随死方案一起删除，结论保留在 README 里
# （实测：A 组阳性对照本身无效，**没有得出任何结论**）。
#
# 所以 impersonate 默认 false，而且**只在命令行显式给了 --impersonate 时**
# 才写 true。实验没过就开着，必然 errorCode 10100 —— 那是在拿一个已经能用的
# 方案去赌一个没验证的假设。
SHIM_LAUNCH_GAME_ARG = "--launch-game"
SHIM_IMPERSONATED_EXE_NAME = "Games.exe"
# 与 shim 源码里的 OFFICIAL_GAME_ARGS 保持一致，也与 games.log 实测到的
# 真启动器命令行一致。顺序/内容都可能影响 SDK，不要改。
SHIM_OFFICIAL_GAME_ARGS = ["-launcher_lang=zh-cn", "-launcher_sub_channel=1"]
# shim 的三种启动方式。与 shim/endfield_shim.py 里的 LAUNCH_MODES 必须一致。
#   auto            先试「挂起的官方 Games.exe 当父进程」，任何一步失败就回落深链
#   suspended_parent 只走挂起父进程，失败就报错弹框，不回落
#   deep_link       只走深链（会弹出官方启动器界面），保留作兜底
SHIM_LAUNCH_MODES = ("auto", "suspended_parent", "deep_link")
SHIM_LAUNCH_MODE_DEFAULT = "auto"
# 按父进程分流（与 shim/endfield_shim.py 里的 LAUNCH_SOURCES 必须一致）：
#   parent        = 按父进程判断（默认）：父进程是 steam.exe 就启动游戏，
#                   其它情况（含双击、含父进程已退出）打开配置主页
#   always_launch = 永远走启动游戏那一侧
#   always_config = 永远走配置主页那一侧
SHIM_LAUNCH_SOURCES = ("parent", "always_launch", "always_config")
SHIM_LAUNCH_SOURCE_DEFAULT = "parent"
# 允许走「启动游戏」侧的父进程文件名。只比文件名，且大小写不敏感。
SHIM_PARENT_WHITELIST_DEFAULT = ["steam.exe"]
# 祖先链往上走几级（**含直接父进程**）。1 = 只查直接父进程。
# 与 shim/endfield_shim.py 里的 DEFAULT_PARENT_MATCH_DEPTH 必须一致。
SHIM_PARENT_MATCH_DEPTH_DEFAULT = 4
SHIM_PARENT_MATCH_DEPTH_MIN = 1
SHIM_PARENT_MATCH_DEPTH_MAX = 16
# shim 构建产物目录（相对路径）。打包时用 build_exe.py --add-data 把
# shim_build\dist\Endfield 整个目录塞进工具 exe 里，运行时解包到 sys._MEIPASS。
SHIM_SRC_REL = Path("shim_build") / "dist" / "Endfield"


def _default_shim_src_dir() -> Path:
    """shim 构建产物目录的**首选**位置。

    打包成 onefile 之后，PyInstaller 把冻结模块的 __file__ 设成
    ``<sys._MEIPASS>\\endfield_updater.pyc``，而 _MEIPASS 在 %TEMP% 下 ——
    那里**永远没有** shim_build\\dist\\Endfield。所以打包后必须指向
    sys._MEIPASS 下由 --add-data 打进去的那份数据。

    源码模式（python endfield_updater.py / python endfield_gui.py）行为不变：
    仍指向源码树里的 shim_build\\dist\\Endfield。
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / SHIM_SRC_REL
        try:
            return Path(sys.executable).resolve().parent / SHIM_SRC_REL
        except OSError:
            return Path(sys.executable).parent / SHIM_SRC_REL
    return Path(__file__).resolve().parent / SHIM_SRC_REL


SHIM_SRC_DIR = _default_shim_src_dir()


def shim_src_candidates() -> list[Path]:
    """shim 构建产物目录的候选路径，按优先级排列。

    1) ``SHIM_SRC_DIR`` —— 首选。测试会 monkeypatch 它，所以必须排第一。
    2) 打包后（frozen）：``sys._MEIPASS`` 下由 --add-data 打进去的那份。
       PyInstaller 对「目录型 --add-data」的落点在不同版本里不一样
       （有的把**内容**放进目标目录，有的再套一层源目录名），两种布局都列上，
       靠实际探测挑。
    3) 打包后：exe 同级的 ``shim_build\\dist\\Endfield``（用户手动摆的情况）。
    4) 源码模式：源码树里的 ``shim_build\\dist\\Endfield``。
    """
    out: list[Path] = []

    def add(p) -> None:
        try:
            p = Path(p)
        except TypeError:
            return
        if p not in out:
            out.append(p)

    add(SHIM_SRC_DIR)
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            add(Path(meipass) / SHIM_SRC_REL)
            add(Path(meipass) / SHIM_SRC_REL / "Endfield")
            add(Path(meipass) / "Endfield")
        try:
            add(Path(sys.executable).resolve().parent / SHIM_SRC_REL)
        except OSError:
            pass
    else:
        add(Path(__file__).resolve().parent / SHIM_SRC_REL)
    return out


def resolve_shim_src_dir() -> tuple[Path | None, list[Path]]:
    """找出真正可用的 shim 构建产物目录。

    返回 ``(命中目录, 试过的全部候选)``。一个都没命中时第一项是 None，
    第二项原样返回 —— 报错时要把**每一个**试过的路径都列出来。
    只报一个路径等于让用户去猜是哪一步没对上。

    2026-10-21 实测（frozen 模式，onefile 负载）：命中的是
    ``<sys._MEIPASS>\\shim_build\\dist\\Endfield`` —— 它是个**目录**，
    里面就一个 ``Endfield.exe``。build_exe.py 的 --add-data 传的是**目录**，
    PyInstaller 把目录的**内容**放进目标路径，所以目标路径本身仍是目录。
    于是 ``src_exe = src / SHIM_EXE_NAME`` 取法正确，
    ``src_internal = src / SHIM_INTERNAL_DIR`` 不存在 -> onefile 分支。

    换句话说：**只要负载里的文件叫 Endfield.exe**，下面候选表里的任何一种
    落点都能命中（目录级命中即可）。唯独「负载文件不叫 Endfield.exe」
    （比如 dest 直接写成 shim_build/dist/Endfield）不在支持范围内 ——
    因为 shim_install 规定死了「负载目录里必须有个 Endfield.exe」。
    """
    tried = shim_src_candidates()
    for c in tried:
        try:
            if (c / SHIM_EXE_NAME).is_file():
                return c, tried
        except OSError:
            continue
    return None, tried


# ---------------------------------------------------------------------------
# 工具 GUI（Steam 终末地更新器）
# ---------------------------------------------------------------------------
#
# 用户双击 shim 时想要的是**工具自己的页面**，不是 shim 里另外造的那个
# 纯 Win32 自绘窗口。所以部署时必须把工具 exe 的**绝对路径**写进
# shim_config.json 的 tool_path —— shim 装在 Steam 库里，那个目录和
# 工具所在的目录八竿子打不着，光靠「向上找」是找不到的。
#
# shim install 是**从源码树跑的**，它知道工具 exe 在哪儿：
#   * 源码直跑 -> <仓库>\\release\\Steam终末地更新器.exe
#   * 打包成 exe -> 打包产物**自己**所在的目录（就是 release\\）
TOOL_EXE_NAME = "Steam终末地更新器.exe"
# 配置侧开哪个页面（与 shim/endfield_shim.py 里的 CONFIG_PAGES 必须一致）：
#   tool（默认）优先拉起工具 GUI，拉不起来回落内置 Win32 简易页
#   builtin     强制用内置 Win32 简易页
#   auto        语义同 tool（工具优先，失败回落）
SHIM_CONFIG_PAGES = ("tool", "builtin", "auto")
SHIM_CONFIG_PAGE_DEFAULT = "tool"
# %TEMP%\_MEI* 残留清理（onefile 的代价）。与 shim/endfield_shim.py 里的
# CFG_CLEANUP_MEI / CFG_CLEANUP_MEI_AGE 必须一致。
SHIM_CLEANUP_MEI_KEY = "cleanup_stale_mei"
SHIM_CLEANUP_MEI_AGE_KEY = "cleanup_mei_min_age_hours"
SHIM_CLEANUP_MEI_DEFAULT = True
SHIM_CLEANUP_MEI_AGE_DEFAULT = 1.0


def _tool_release_dir() -> Path:
    """工具 exe 所在的目录。

    打包后 sys.executable 的父目录就是它自己所在的地方（release\\），
    源码直跑则是 <仓库>\\release\\。两种形态都指对了。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent / "release"


def find_tool_exe() -> Path | None:
    """找工具 GUI 的 exe（Steam终末地更新器.exe）。找不到返回 None。

    文件名比对**大小写不敏感**：用户改名成 steam终末地更新器.exe 也认。
    """
    d = _tool_release_dir()
    try:
        if not d.is_dir():
            return None
        want = TOOL_EXE_NAME.lower()
        for p in sorted(d.iterdir()):
            try:
                if p.is_file() and p.name.lower() == want:
                    return p.resolve()
            except OSError:
                continue
    except OSError:
        return None
    return None


def _launcher_root_from_game_dir(game_dir: Path) -> Path | None:
    """官服游戏目录形如 <根>\\games\\<游戏名>，根目录就是 games 的上一级。"""
    try:
        games = game_dir.resolve().parent
        if games.name.lower() != "games":
            return None
        root = games.parent
        if (root / LAUNCHER_EXE_NAME).is_file():
            return root
    except OSError:
        pass
    return None


def find_official_launcher(official_dir: Path | None = None) -> Path | None:
    """定位 Hypergryph 启动器的 Launcher.exe；找不到返回 None。

    先用目录结构反推（最准），再扫常见安装位置。反推这一步在实测机器上
    直接命中 C:\\Program Files\\Hypergryph Launcher\\Launcher.exe。
    """
    cands: list[Path] = []
    gd = Path(official_dir) if official_dir else OFFICIAL_GAME_DIR
    if gd is not None:
        r = _launcher_root_from_game_dir(gd)
        if r is not None:
            cands.append(r)
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            for nm in ("Hypergryph Launcher", "GRYPHLINK"):
                cands.append(Path(base) / nm)
    for drive in "CDEFGH":
        for nm in ("Hypergryph Launcher", "GRYPHLINK"):
            cands.append(Path(f"{drive}:\\") / nm)
    for c in cands:
        exe = c / LAUNCHER_EXE_NAME
        if exe.is_file():
            return exe
    return None


def shim_installed_marker() -> bool:
    """极简判断：Steam 安装目录里是不是躺着我们部署的 shim。

    比 shim_status() 便宜（不读配置、不探测启动器），GUI 刷新状态时高频调用。
    """
    try:
        return (INSTALL_DIR / SHIM_CONFIG_NAME).is_file() and (
            INSTALL_DIR / SHIM_EXE_NAME).is_file()
    except OSError:
        return False


def find_deep_link_scheme() -> str | None:
    """找启动器注册的协议 scheme（如 hypergryphlauncher-abYeZZ16BPluCFyT）。

    走协议 URL 才能自动开始游戏 —— 实测 `Launcher.exe --game=endfield`
    只会打开启动器界面，要人再点一次「启动游戏」；而
    `<scheme>://start?ta=endfield` 会在约 14 秒后直接把游戏拉起来。

    app_code 从注册表读，不硬编码 —— 启动器换版本/换渠道也不会失效。
    """
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if name.lower().startswith(SHIM_SCHEME_PREFIX):
                    return name
    except OSError:
        pass
    return None


def lab_impersonate_verdict() -> bool | None:
    """读「启动鉴权判定实验」的结论 —— **恒为 None（没有结论）**。

    B 组 PASS = SDK 只比文件名 -> 伪装方案可行。
    B 组 FAIL = 比完整路径 -> 伪装必然 10100，此时 status 会明确警告。

    2026-10-21 彻底清理：判定实验的脚本与那份报告（``authpath.*.txt``）都
    作为**已废弃方案**被删掉了，所以这里恒为 None ——「没有结论」，
    而不是「结论是 FAIL」。这个区别在 status 的提示文案里说清楚了。

    2026-10-22 清理测试基础设施时，报告所在的 ``_lab\\`` 目录**整个消失** ——
    从此再也不可能出现新的报告。函数保留（``shim status --json`` 的
    ``impersonate_verified`` 字段还指着它），但它不再读任何文件。
    """
    return None


def _match_depth_of(value) -> int:
    """把配置里的 parent_match_depth 规整成一个合法的深度。

    坏值（不是数字 / <= 0）一律回落到默认 4 —— 与 shim 侧的行为一致。
    """
    try:
        d = int(value)
    except (TypeError, ValueError):
        return SHIM_PARENT_MATCH_DEPTH_DEFAULT
    if d < SHIM_PARENT_MATCH_DEPTH_MIN:
        return SHIM_PARENT_MATCH_DEPTH_DEFAULT
    return min(SHIM_PARENT_MATCH_DEPTH_MAX, d)


def shim_status() -> dict:
    info = junction_info()
    exe = INSTALL_DIR / SHIM_EXE_NAME
    cfg = INSTALL_DIR / SHIM_CONFIG_NAME
    launcher = find_official_launcher()
    scheme = find_deep_link_scheme()
    cfg_launcher = None
    cfg_scheme = None
    cfg_impersonate = None
    cfg_game_dir = None
    cfg_launch_mode = None
    cfg_launch_source = None
    cfg_parent_whitelist = None
    cfg_parent_match_depth = None
    cfg_config_page = None
    cfg_tool_path = None
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            cfg_launcher = data.get("launcher")
            cfg_scheme = data.get("scheme")
            cfg_impersonate = data.get("impersonate")
            cfg_game_dir = data.get("game_dir")
            cfg_launch_mode = data.get("launch_mode")
            cfg_launch_source = data.get("launch_source")
            cfg_parent_whitelist = data.get("parent_whitelist")
            cfg_parent_match_depth = data.get("parent_match_depth")
            cfg_config_page = data.get("config_page")
            cfg_tool_path = data.get("tool_path")
        except (OSError, ValueError):
            pass
    games_exe = INSTALL_DIR / SHIM_IMPERSONATED_EXE_NAME
    src_hit, src_tried = resolve_shim_src_dir()
    # --- 部署形态：单文件（onefile）还是目录版（onedir）？ ---
    # 2026-10-20 起部署的是**单文件版**：Steam 库里只有一个 Endfield.exe。
    # 目录还在且有文件 = 上一版（onedir）留下的残留，shim install 会清掉。
    internal = INSTALL_DIR / SHIM_INTERNAL_DIR
    internal_files = 0
    try:
        if internal.is_dir():
            internal_files = sum(1 for _ in internal.rglob("*") if _.is_file())
    except OSError:
        internal_files = 0
    if internal_files:
        shim_mode = "onedir-residue"
    elif exe.is_file():
        shim_mode = "onefile"
    else:
        shim_mode = "none"
    # 完整性：落地的那份 exe 与构建源是否逐字节一致
    payload_ok = None
    payload_note = "没有构建源可比对"
    if exe.is_file() and src_hit is not None:
        src_exe = src_hit / SHIM_EXE_NAME
        if src_exe.is_file():
            same, why = _exe_matches(src_exe, exe)
            payload_ok = same
            payload_note = why
    return {
        "install_dir": str(INSTALL_DIR),
        "install_dir_exists": INSTALL_DIR.is_dir(),
        "is_junction": info["is_junction"],
        "junction_target": info.get("target"),
        "shim_installed": exe.is_file() and not info["is_junction"],
        "shim_exe": str(exe),
        "shim_bytes": exe.stat().st_size if exe.is_file() else 0,
        # 单文件版 / 目录版 —— 供 GUI 与命令行显示当前形态
        "shim_mode": shim_mode,
        "shim_mode_text": {
            "onefile": "单文件版（onefile，自包含：只有 Endfield.exe，"
                       "单独拷走也能用）",
            "onedir-residue": f"上一版目录版残留（_internal/ 里还有 "
                              f"{internal_files} 个文件，shim install 会清掉）",
            "none": "未部署",
        }.get(shim_mode, shim_mode),
        "shim_internal_files": internal_files,
        "shim_payload_ok": payload_ok,
        "shim_payload_note": payload_note,
        "shim_config": str(cfg) if cfg.is_file() else None,
        "shim_config_launcher": cfg_launcher,
        "shim_config_scheme": cfg_scheme,
        "deep_link_scheme": scheme,
        "deep_link": (scheme + SHIM_DEEP_LINK_TAIL) if scheme else None,
        "official_game_dir": str(OFFICIAL_GAME_DIR) if OFFICIAL_GAME_DIR else None,
        "official_launcher": str(launcher) if launcher else None,
        "impersonate": bool(cfg_impersonate),
        "impersonate_verified": lab_impersonate_verdict(),
        "game_dir": cfg_game_dir or "",
        "launch_mode": cfg_launch_mode or SHIM_LAUNCH_MODE_DEFAULT,
        "launch_source": cfg_launch_source or SHIM_LAUNCH_SOURCE_DEFAULT,
        "parent_whitelist": (cfg_parent_whitelist
                             if isinstance(cfg_parent_whitelist, list)
                             else list(SHIM_PARENT_WHITELIST_DEFAULT)),
        "parent_match_depth": _match_depth_of(cfg_parent_match_depth),
        "config_page": (cfg_config_page.strip().lower()
                        if isinstance(cfg_config_page, str)
                        and cfg_config_page.strip().lower() in SHIM_CONFIG_PAGES
                        else SHIM_CONFIG_PAGE_DEFAULT),
        "tool_path": cfg_tool_path if isinstance(cfg_tool_path, str) else "",
        "impersonated_exe": str(games_exe),
        "impersonated_exe_exists": games_exe.is_file(),
        "acf": str(ACF_PATH),
        "acf_exists": ACF_PATH.is_file(),
        "src_dir": str(src_hit) if src_hit is not None else str(SHIM_SRC_DIR),
        "src_available": src_hit is not None,
        # 试过的每一个候选路径 —— 一个都没命中时报错要全列出来，
        # 只报一个等于让用户去猜。
        "src_candidates": [str(x) for x in src_tried],
    }


def _shim_installed_files() -> list[Path]:
    """shim 部署会写入的文件。移除时只碰这些，绝不误伤目录里别的东西。

    含 Games.exe：它是伪装模式**运行时**在 Steam 安装目录里生成的
    （本 shim 的副本）。必须列进来，否则两种后果：
      1. shim remove 删不干净，留一个孤儿 Games.exe；
      2. 下次 shim install 看到它会当成「目录里别人的东西」而拒绝部署。
    """
    return [INSTALL_DIR / SHIM_EXE_NAME,
            INSTALL_DIR / SHIM_CONFIG_NAME,
            INSTALL_DIR / SHIM_LOG_NAME,
            INSTALL_DIR / SHIM_IMPERSONATED_EXE_NAME,   # 伪装模式生成的
            INSTALL_DIR / SHIM_INTERNAL_DIR]


def _exe_requires_admin(exe: Path) -> bool | None:
    """粗查 PE 里有没有 requireAdministrator；None = 读不出来。"""
    try:
        blob = exe.read_bytes()
    except OSError:
        return None
    if b'requestedExecutionLevel level="requireAdministrator"' in blob:
        return True
    if b'requestedExecutionLevel level="asInvoker"' in blob:
        return False
    return None


def _is_empty_dir(p: Path) -> bool:
    """目录里一个文件都没有（只有空子目录也算空）。

    用来把「无害残留」和「疑似真实安装」分开：Steam 半途失败的安装、
    或者被别的工具留下的骨架目录，特征就是**只有目录、没有文件**。
    """
    try:
        if p.is_symlink():
            return False
        if not p.is_dir():
            return False
        for _root, _dirs, files in os.walk(p):
            if files:
                return False
        return True
    except OSError:
        return False


def _partition_install_dir() -> tuple[list[Path], list[Path]]:
    """把安装目录里的东西分成 (无害残留=空目录, 可疑=有实际内容的项)。

    分家的理由（2026-10-19 实测事故）：
      上一轮 `shim install --force` 把整个目录改名成 `.bak_<时间戳>`，
      留下的 21 MB 备份就躺在 `steamapps\\common` 下面，Steam 会把它
      当成一个异常目录。而那台机器之所以「改名没出事」，只是因为它的
      Steam 目录里**本来就没有游戏本体** —— 纯属特例。
      在真正装了游戏的库上，这条 `--force` 会把整个游戏目录改名。

    所以：空目录一律跳过（本次就是被 10 个空目录骨架挡住的），
    有实际内容的才算「疑似真实安装」，而那条路**必须走不通**，见 shim_install。
    """
    owned = set(_shim_installed_files())
    benign: list[Path] = []
    suspicious: list[Path] = []
    for p in sorted(INSTALL_DIR.iterdir()):
        if p in owned:
            continue
        if _is_empty_dir(p):
            benign.append(p)
        else:
            suspicious.append(p)
    return benign, suspicious


def _is_drive_root(p: Path) -> bool:
    """这个路径是不是某个盘符的根（如 ``E:\\``）。

    盘符根目录**永远不可能**是 Steam 的安装目录：那里只有一个 exe，
    没有 ``_internal``，Steam 点开始游戏必然报
    ``Failed to load Python DLL 'E:\\\\_internal\\\\python314.dll'``。

    出现这种路径只可能是安装目录算错了（比如把某一层父目录当成了安装目录），
    所以直接拒绝 —— 不往那儿写，也不从那儿删。
    """
    try:
        return Path(p).parent == Path(p)
    except (TypeError, ValueError, OSError):
        return False


def _missing_payload_files(src_dir: Path, dst_dir: Path) -> list[str]:
    """列出 ``src_dir`` 里有、``dst_dir`` 里缺失或大小对不上的文件（相对路径）。

    只做「源 -> 目标」这一个方向：目标里多出来的旧文件不算问题
    （重新部署时目标目录里可能残留上一版构建产物，那是允许的）。
    """
    missing: list[str] = []
    try:
        for root, _dirs, files in os.walk(src_dir):
            for fn in files:
                s = Path(root) / fn
                try:
                    rel = s.relative_to(src_dir)
                except ValueError:
                    continue
                d = dst_dir / rel
                try:
                    if not d.is_file() or d.stat().st_size != s.stat().st_size:
                        missing.append(rel.as_posix())
                except OSError:
                    missing.append(rel.as_posix())
    except OSError:
        return missing
    return missing


# ---------------------------------------------------------------------------
# 单文件（onefile）形态：识别「我们的产物」
# ---------------------------------------------------------------------------
#
# 2026-10-20 起部署用的 shim 改成 **onefile（自包含）**：Steam 库里只有
# 一个 Endfield.exe，没有 _internal\。
#
# 但**上一版**（onedir）部署出去的那 19 MB 依赖目录还躺在 Steam 库里 ——
# 它必须被清掉（否则 19 MB 垃圾永远留着），可是**不能**连着别人的东西
# 一起删。
#
# 「我们的产物」怎么认？三条依据，任一命中就算：
#   1) 这个 exe 内嵌的 CArchive 目录里列出来的依赖文件名（最准）；
#   2) CPython 内置扩展模块名（sys.builtin_module_names + ".pyd"）；
#   3) 一份固定形态的运行时文件名清单（python3xx.dll / api-ms-win-* /
#      base_library.zip / ucrtbase / vcruntime140 / libcrypto / libssl / libffi）。
#
# 认不出来的东西（比如测试里造的 extra.dat）一律**不删**，并把名字报出来。
_PYI_RUNTIME_EXACT = {
    "base_library.zip", "ucrtbase.dll", "vcruntime140.dll",
    "vcruntime140_1.dll", "msvcp140.dll",
}
try:
    _BUILTIN_EXT_PYD = {n.lower() + ".pyd" for n in sys.builtin_module_names}
except Exception:                                    # pragma: no cover
    _BUILTIN_EXT_PYD = set()


def _is_pyi_runtime_file(name: str, extra_ok: set[str] | None = None) -> bool:
    """这个文件名是不是 PyInstaller onedir 的运行时依赖（= 我们的产物）。"""
    n = name.lower()
    if extra_ok and n in extra_ok:
        return True
    if n in _PYI_RUNTIME_EXACT:
        return True
    if n.startswith("api-ms-win-") and n.endswith(".dll"):
        return True
    if n.startswith("python3") and n.endswith(".dll"):
        return True
    for pre in ("libcrypto-", "libssl-", "libffi-"):
        if n.startswith(pre) and n.endswith(".dll"):
            return True
    if n.endswith(".pyd") and n in _BUILTIN_EXT_PYD:
        return True
    return False


def pyi_archive_payload_names(exe: Path) -> set[str]:
    """读 onefile exe 内嵌的 CArchive 目录，返回它带的依赖文件名集合。

    只读，不改任何东西。读不出来就返回空集（调用方据此放宽到静态清单）。
    """
    import struct

    cookie_magic = b"MEI\x0c\x0b\x0a\x0b\x0e"
    try:
        blob = exe.read_bytes()
    except OSError:
        return set()
    pos = blob.rfind(cookie_magic)
    if pos < 0:
        return set()
    try:
        (_m, length, toc_offset, toc_len, _py) = struct.unpack(
            "!8sIIII", blob[pos:pos + 24])
        pkg_start = pos + 88 - length
        p = pkg_start + toc_offset
        end = p + toc_len
        names: set[str] = set()
        while p < end:
            (entrylen, _off, _clen, _ulen, _flag, typ) = struct.unpack(
                "!IIIIBc", blob[p:p + 18])
            nm = blob[p + 18:p + entrylen].split(b"\0")[0].decode("utf-8", "replace")
            if typ in (b"b", b"d", b"x"):        # BINARY / DATA / EXTENSION
                names.add(nm.replace("\\", "/").lower())
            p += entrylen
        return names
    except Exception:
        return set()


def _internal_foreign_entries(internal: Path,
                              extra_ok: set[str] | None = None) -> list[str]:
    """列出 ``_internal\\`` 里**不是我们产物**的条目（相对路径）。"""
    bad: list[str] = []
    try:
        for root, dirs, files in os.walk(internal):
            for fn in files:
                rel = (Path(root) / fn).relative_to(internal).as_posix()
                if not _is_pyi_runtime_file(fn, extra_ok):
                    bad.append(rel)
            # 空目录不算「别人的东西」，但**非空**的子目录要逐个看
            for dn in dirs:
                sub = Path(root) / dn
                try:
                    has = any(1 for _ in sub.rglob("*"))
                except OSError:
                    has = True
                if not has:
                    continue
                for f in sub.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(internal).as_posix()
                        if not _is_pyi_runtime_file(f.name, extra_ok):
                            bad.append(rel)
    except OSError:
        return bad
    return bad


def clear_internal_dir(internal: Path, extra_ok: set[str] | None = None
                        ) -> tuple[bool, str, int]:
    """清空 ``_internal\\`` 里的东西，**但保留 _internal 目录本身**。

    保留目录本身是有原因的：Steam 那边可能有它自己的目录骨架，
    把整个目录删掉会让 Steam 认为安装不完整。

    返回 ``(成功?, 说明, 删掉的文件数)``。
    只要发现一个**不是我们产物**的东西，就**一个字节都不删**，
    把它的名字报出来让人自己判断。
    """
    if not internal.is_dir():
        return True, "没有 _internal 目录", 0
    bad = _internal_foreign_entries(internal, extra_ok)
    if bad:
        return False, ("_internal\\ 里有不是本工具产物的东西，已原样保留："
                       + ", ".join(sorted(bad)[:8])
                       + (f" 等 {len(bad)} 项" if len(bad) > 8 else "")), 0
    removed = 0
    try:
        for item in sorted(internal.iterdir()):
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed += 1
            except OSError as e:
                return False, f"删不掉 {item.name}: {e}", removed
    except OSError as e:
        return False, f"读不了 {internal}: {e}", removed
    return True, f"已清空 {removed} 项（_internal 目录本身保留）", removed


def _exe_matches(src: Path, dst: Path) -> tuple[bool, str]:
    """落地后的 exe 与构建源是否**逐字节**一致（大小 + md5）。"""
    try:
        ss, ds = src.stat(), dst.stat()
    except OSError as e:
        return False, f"读不到文件: {e}"
    if ss.st_size != ds.st_size:
        return False, f"大小对不上（{ds.st_size:,} vs 源 {ss.st_size:,}）"
    try:
        if _md5_of_file(dst) != _md5_of_file(src):
            return False, "md5 对不上（内容被改过）"
    except OSError as e:
        return False, f"算 md5 失败: {e}"
    return True, "大小与 md5 都一致"


def shim_install(src_dir=None, launcher=None, force: bool = False,
                 impersonate: bool = False) -> int:
    """把 shim 部署到 Steam 安装目录。

    顺序很重要：先拆旧联接（rmdir 只删链接本身，官服目录不受影响），
    再放 shim。官服目录自始至终不被改动。

    impersonate=True 时把 shim_config.json 的 impersonate 打开（Games.exe 伪装）。
    **只在调用方显式要求时才开** —— 该方案能否通过启动鉴权尚未实测。

    force=True **不再**触发整目录改名（2026-10-19 起）。安装目录里只要有
    「有实际内容」的项，本函数一律返回 2 并说明出路 —— 见下面 _partition_install_dir
    的注释。空目录残留会被跳过，既不拦部署也不改名。
    """
    if src_dir:
        src = clean_path(src_dir)
        tried = [src]
    else:
        # 打包后的工具 GUI 里，shim 构建产物是**打进包里**的（sys._MEIPASS 下）。
        # 这里按优先级逐个试候选路径，一个都没命中才报错。
        src, tried = resolve_shim_src_dir()
    src_exe = (src / SHIM_EXE_NAME) if src is not None else None
    if src is None or not src_exe.is_file():
        print(f"[!] 找不到 shim 构建产物: {SHIM_EXE_NAME}")
        for c in tried:
            print(f"    试过: {c}")
        print("    先构建: python build_shim.py")
        print("    （打包版工具自带 shim；源码版需要先构建）")
        return 1

    resolve_game_paths()

    # --- 0/4 路径自检：盘符根目录一定是安装目录算错了 ---
    # 不拦的话就会在 E:\ 这类地方留下一个**光杆 exe**（没有 _internal），
    # 谁双击它谁就会看到 "Failed to load Python DLL 'E:\_internal\python314.dll'"。
    if _is_drive_root(INSTALL_DIR):
        print(f"[!] 安装目录算成了盘符根目录: {INSTALL_DIR}")
        print("    这不可能是 Steam 的安装目录 —— 拒绝往盘符根目录里写文件。")
        print("    （只落地一个 exe、没有 _internal 的话，点开始游戏会报：")
        print("      Failed to load Python DLL '...\\\\_internal\\\\python314.dll'）")
        return 2

    # --- 1/4 拆掉旧联接 ---
    info = junction_info()
    if info["is_junction"]:
        tgt = info.get("target")
        print(f"[1/4] 发现旧目录联接，先拆除: {INSTALL_DIR}")
        print(f"      指向: {tgt}")
        rc = remove_junction(INSTALL_DIR)
        if rc != 0:
            return rc
        print(f"      官服目录未被改动: {tgt}")
    elif INSTALL_DIR.is_dir():
        benign, extras = _partition_install_dir()
        if benign:
            # 空目录骨架：直接跳过。不计入 extras，不改名，不删除 ——
            # 它没有任何内容，删不删都不影响部署，删了反而可能踩到别的程序。
            names = ", ".join(p.name for p in benign[:10])
            more = f" 等 {len(benign)} 个" if len(benign) > 10 else ""
            print(f"[1/4] 跳过 {len(benign)} 个空目录残留（里面没有文件，原样留着）: "
                  f"{names}{more}")
        if extras:
            # **不论有没有 --force，这里都拒绝。**
            # 曾经的做法是 `INSTALL_DIR.rename(backup)`：在真装了游戏的库上，
            # `--force` 会把整个游戏目录改名 —— 那台机器没出事只是因为它
            # 恰好没装游戏本体。这不是「备份」，是拿用户的游戏文件赌。
            print(f"[!] {INSTALL_DIR} 里有 {len(extras)} 项**有实际内容**的东西：")
            for p in extras[:10]:
                print(f"      {p.name}")
            if len(extras) > 10:
                print(f"      …… 另有 {len(extras) - 10} 项")
            print()
            print("[!] 这很可能是一次**真实的游戏安装**（或别人的程序留下的数据），")
            print("    所以这里拒绝部署 —— 加 --force 也不行。")
            print()
            print("    为什么不给 --force 开门：整个目录改名会把你这一整个游戏")
            print("    搬走。本项目上一轮就是这么在 steamapps\\common 下留了个")
            print("    21 MB 的 .bak_ 目录。当时没出事，只是因为那台机器的 Steam")
            print("    目录里本来就没有游戏本体 —— 那是特例，不能当默认。")
            print()
            print("    该怎么办（按你的实际情况选一条）：")
            print("      A. 这确实是残留垃圾，人工确认后自己删掉那个目录里的内容，")
            print("         再重跑 shim install。")
            print("      B. 这是真正的游戏安装 —— 别用本方案。请在 Steam 里")
            print("         「卸载」它（或删掉 appmanifest 让 Steam 重新校验），")
            print("         然后用官服客户端自己玩 —— 联接那条老路已退役，")
            print("         不要再走它（鉴权过不去，且会整目录改名）。")
            print("      C. 只想清掉 shim 自己那几个文件：用")
            print("         python endfield_updater.py shim remove")
            print("         它**逐个文件**删 Endfield.exe / _internal / shim.log /")
            print("         shim_config.json，其余一律不碰，绝不整目录改名。")
            return 2
    else:
        print("[1/4] 无联接，目录不存在，直接创建")

    # --- 2/4 复制 ---
    print(f"[2/4] 复制 shim -> {INSTALL_DIR}")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    src_internal = src / SHIM_INTERNAL_DIR
    dst_internal = INSTALL_DIR / SHIM_INTERNAL_DIR
    # 构建产物是单文件版（onefile）时，源目录里**没有** _internal\，
    # 部署出去的就只有一个 Endfield.exe。
    onefile = not src_internal.is_dir()
    if onefile:
        shutil.copy2(str(src_exe), str(INSTALL_DIR / SHIM_EXE_NAME))
        print(f"      单文件版：只复制 {SHIM_EXE_NAME}"
              f"（{src_exe.stat().st_size:,} 字节，自包含）")
    else:
        for item in src.iterdir():
            dst = INSTALL_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        print(f"      目录版：整个目录（含 {SHIM_INTERNAL_DIR}/）")

    # --- 2.4/4 清掉上一版（onedir）留下的 _internal ---
    # 上一版部署出去的是 exe + _internal\（19 MB）。换成单文件版之后，
    # 那 19 MB 就是**纯垃圾**了 —— 但必须**安全**地清：
    #   * 只删 _internal **里面**的东西，_internal 目录本身留着
    #     （Steam 那边可能有它自己的目录骨架）；
    #   * 删之前先确认里面没有**不是**我们产物的东西 ——
    #     有的话一个字节都不删，并把名字报出来。
    #
    # **只在单文件版时做**：目录版（onedir）刚把新的 _internal\ 拷进去，
    # 这时候再清就等于把刚拷的东西删了（copytree 覆盖不了的多余旧文件
    # 留着无害 —— 见 _missing_payload_files 的注释）。
    if onefile and dst_internal.is_dir():
        extra_ok = pyi_archive_payload_names(src_exe)
        ok, detail, n = clear_internal_dir(dst_internal, extra_ok)
        if not ok:
            print(f"[!] {SHIM_INTERNAL_DIR}/ 清理失败：{detail}")
            print("    部署中止 —— 宁可不动，也不删不是本工具产出的东西。")
            print("    处理办法：人工确认后删掉那个目录里的内容，再重跑 shim install。")
            return 2
        if n:
            print(f"      已清掉上一版遗留的 {SHIM_INTERNAL_DIR}/ 内 {n} 项"
                  f"（{SHIM_INTERNAL_DIR} 目录本身保留）")
        else:
            print(f"      {SHIM_INTERNAL_DIR}/ 已经是空的，不用清")

    # --- 2.5/4 校验落地内容是完整的 ---
    # 这是「只落地 exe、没有 _internal」这类事故的直接防线。
    # PyInstaller 的 onedir 版 exe 启动时去**自己所在目录**找 _internal，
    # 找不到就报 Failed to load Python DLL '...\_internal\python314.dll'。
    # 以前只在 [4/4] 查 exe 在不在，等于把一个**必然起不来**的部署报成成功。
    #
    # 单文件版没有 _internal，完整性就靠 **exe 本身**保证：
    # 落地的那份必须与构建源**大小一致、md5 一致**。缺了或对不上 -> 失败。
    if not onefile:
        missing = _missing_payload_files(src_internal, dst_internal)
        if missing:
            print(f"[!] {SHIM_INTERNAL_DIR}/ 没复制完整，缺 {len(missing)} 个文件：")
            for rel in missing[:10]:
                print(f"      {rel}")
            if len(missing) > 10:
                print(f"      …… 另有 {len(missing) - 10} 个")
            print()
            print("    只有 exe、没有 _internal 的 shim 是**必然起不来**的：")
            print("      Steam 点开始游戏 -> Failed to load Python DLL "
                  "'...\\\\_internal\\\\python314.dll'")
            print("    部署中止。检查构建产物是否完整（重新构建: python build_shim.py）。")
            return 2
    else:
        dst_exe = INSTALL_DIR / SHIM_EXE_NAME
        if not dst_exe.is_file():
            print(f"[!] 复制后仍找不到 {dst_exe}")
            print("    单文件版的 shim 就是一个 exe；它不在就等于没部署成功。")
            return 2
        same, why = _exe_matches(src_exe, dst_exe)
        if not same:
            print(f"[!] 落地后的 {SHIM_EXE_NAME} 与构建源不一致：{why}")
            print("    单文件版的 shim 全靠这一个 exe —— 它不对就等于没部署。")
            print("    部署中止。重新构建: python build_shim.py")
            return 2
        print(f"      {SHIM_EXE_NAME} 与构建源逐字节一致（大小 + md5 都对上了）")
        # 源目录里本来就没有 _internal —— 那是**单文件版**（现在的默认形态）。
        # 单文件版的 Endfield.exe 自带整个 Python 运行时，
        # **单独拷到任何地方都能用**（这正是改成 onefile 的目的）。
        print(f"[i] 构建产物里没有 {SHIM_INTERNAL_DIR}/ —— 这是**单文件版** shim。")
        print("    Endfield.exe 自带整个运行时，单独拷走也能用"
              "（不会再报 Failed to load Python DLL）。")

    # --- 3/4 写配置 ---
    if launcher is None:
        launcher = find_official_launcher()
    if launcher is None:
        print("[注意] 没找到官方启动器 Launcher.exe。shim 走协议深链，"
              "并不需要这个路径，仅用于诊断与兜底。")
    scheme = find_deep_link_scheme()
    if scheme is None:
        print("[!] 注册表里没有找到 hypergryphlauncher-* 协议。")
        print("    官方启动器可能没装或没装全 —— shim 会回落到内置 scheme，")
        print("    如果那条路也不通就只会打开启动器界面，需要你手动点「启动游戏」。")

    # --- game_dir：伪装模式下 shim 要靠它找到官服游戏本体 ---
    # 只读探测，写进配置。探测不到就写空串 —— shim 在伪装模式下会明确报错，
    # 而不会拿一个猜出来的路径去拉起错误的 exe。
    game_dir = ""
    probe = OFFICIAL_GAME_DIR
    if probe is not None and (probe / GAME_EXE_NAME).is_file():
        game_dir = str(probe)
    elif probe is not None:
        print(f"[!] 官服目录里没找到 {GAME_EXE_NAME}: {probe}")
    else:
        print("[!] 没探测到官服游戏目录。")
    if impersonate and not game_dir:
        # 显式要伪装却给不出 game_dir = 必然失败，早说比让用户点开始游戏
        # 看到 Steam 报错强。
        print("[!] --impersonate 需要 game_dir，但没能探测到官服游戏目录。")
        print("    用 --game-dir \"<官服游戏目录>\" 显式指定，或先跑一次 status 看探测结果。")
        return 2

    # --- launch_mode：挂起父进程 / 深链 ---
    # 已有配置里写了合法取值就**沿用**它：用户手动挑过启动方式，
    # 一次 shim install 不该悄悄把它打回默认（那等于换了行为还不说）。
    launch_mode = SHIM_LAUNCH_MODE_DEFAULT
    cfg_path = INSTALL_DIR / SHIM_CONFIG_NAME
    prev_cfg: dict = {}
    if cfg_path.is_file():
        try:
            prev = json.loads(cfg_path.read_text(encoding="utf-8"))
            prev_cfg = prev if isinstance(prev, dict) else {}
            lm = prev_cfg.get("launch_mode")
            if isinstance(lm, str) and lm.strip().lower() in SHIM_LAUNCH_MODES:
                launch_mode = lm.strip().lower()
                print(f"[i] 沿用已有 launch_mode = {launch_mode}")
        except (OSError, ValueError):
            pass

    # --- launch_source / parent_whitelist / parent_match_depth：按父进程分流 ---
    # 同样沿用已有配置：用户可能特意把分流固定成某一边。
    launch_source = SHIM_LAUNCH_SOURCE_DEFAULT
    wl = list(SHIM_PARENT_WHITELIST_DEFAULT)
    ls = prev_cfg.get("launch_source")
    if isinstance(ls, str) and ls.strip().lower() in SHIM_LAUNCH_SOURCES:
        launch_source = ls.strip().lower()
    pw = prev_cfg.get("parent_whitelist")
    if isinstance(pw, str):
        pw = [x for x in re.split(r"[,;\s]+", pw) if x]
    if isinstance(pw, (list, tuple)):
        names = []
        for n in pw:
            if isinstance(n, str) and n.strip():
                n = n.strip()
                if n.lower() not in {x.lower() for x in names}:
                    names.append(n)
        if names:
            wl = names
    # 祖先链深度：用户改过就沿用
    match_depth = _match_depth_of(prev_cfg.get("parent_match_depth"))

    # --- 配置侧开哪个页面 ---
    # 同样沿用已有配置：用户可能特意固定成 builtin（不想被拉起 GUI）。
    config_page = SHIM_CONFIG_PAGE_DEFAULT
    cp = prev_cfg.get("config_page")
    if isinstance(cp, str) and cp.strip().lower() in SHIM_CONFIG_PAGES:
        config_page = cp.strip().lower()
        print(f"[i] 沿用已有 config_page = {config_page}")

    # --- %TEMP%\_MEI* 残留清理 ---
    # 单文件版每次启动都往 %TEMP% 解包 ~19 MB；用户硬杀游戏时 PyInstaller
    # 来不及清理，会留下残留（历史上漏过 2.16 GB）。默认**开**清理。
    # 用户手动关掉过就沿用他的选择。
    cleanup_mei = SHIM_CLEANUP_MEI_DEFAULT
    cm = prev_cfg.get(SHIM_CLEANUP_MEI_KEY)
    if cm is not None:
        if isinstance(cm, bool):
            cleanup_mei = cm
        elif isinstance(cm, (int, float)):
            cleanup_mei = bool(cm)
        elif isinstance(cm, str):
            low = cm.strip().lower()
            if low in ("true", "1", "yes", "on"):
                cleanup_mei = True
            elif low in ("false", "0", "no", "off"):
                cleanup_mei = False
    cleanup_mei_age = SHIM_CLEANUP_MEI_AGE_DEFAULT
    ca = prev_cfg.get(SHIM_CLEANUP_MEI_AGE_KEY)
    try:
        if ca is not None:
            cleanup_mei_age = max(0.1, float(ca))
    except (TypeError, ValueError):
        pass

    # --- 工具 GUI 的绝对路径 ---
    # 优先级：用户自己填过的（且文件还在） > 本次找到的 release\\ 里的那个。
    # shim 装在 Steam 库里，和工具所在的目录八竿子打不着，
    # **必须**把绝对路径写进配置，否则双击时 shim 根本找不到工具。
    tool_path = ""
    prev_tool = prev_cfg.get("tool_path")
    if isinstance(prev_tool, str) and prev_tool.strip():
        cand = Path(prev_tool.strip().strip('" '))
        try:
            if cand.is_file():
                tool_path = str(cand)
                print(f"[i] 沿用已有 tool_path = {tool_path}")
        except OSError:
            pass
    if not tool_path:
        found_tool = find_tool_exe()
        if found_tool is not None:
            tool_path = str(found_tool)
            print(f"[i] 工具 GUI = {tool_path}")
        else:
            print(f"[!] 没在 {_tool_release_dir()} 找到 {TOOL_EXE_NAME}。")
            print("    双击 shim 时会回落到内置简易配置页。")
            print("    可以在 shim_config.json 的 tool_path 里手填完整路径。")

    cfg = {
        "launcher": str(clean_path(launcher)) if launcher else "",
        "scheme": scheme or "",
        "deep_link": (scheme + SHIM_DEEP_LINK_TAIL) if scheme else None,
        "version": SHIM_VERSION,
        "app_id": APP_ID,
        "written": int(time.time()),
        # 伪装开关。默认 false：能否通过鉴权还没实测（启动鉴权判定实验 B 组），
        # 缺省必须是「不开启」。
        "impersonate": bool(impersonate),
        # 启动方式。auto = 先试挂起父进程（不弹启动器界面），失败回落深链。
        "launch_mode": launch_mode,
        # 分流方式。parent = 按父进程判断（默认）：父进程是 steam.exe 就启动
        # 游戏，其它情况（含双击）打开配置主页。
        "launch_source": launch_source,
        # 允许走「启动游戏」侧的父进程文件名（只比文件名，大小写不敏感）。
        "parent_whitelist": wl,
        # 祖先链往上走几级（含直接父进程）。链上任意一级命中白名单都算命中。
        "parent_match_depth": match_depth,
        # 配置侧开哪个页面：tool（默认，先拉起工具 GUI，失败回落内置页）
        # / builtin（强制内置页）/ auto（同 tool）。
        "config_page": config_page,
        # 工具 GUI 的**绝对路径**。shim 装在 Steam 库里，靠「向上找」找不到
        # 工具，所以必须在这里写死。
        "tool_path": tool_path,
        # 官服游戏目录（只读定位点，shim 从不往里写东西）
        "game_dir": game_dir,
        # %TEMP%\_MEI* 残留清理（onefile 的代价）。默认开。
        SHIM_CLEANUP_MEI_KEY: cleanup_mei,
        SHIM_CLEANUP_MEI_AGE_KEY: cleanup_mei_age,
        **SHIM_HIDE_DEFAULTS,
    }
    (INSTALL_DIR / SHIM_CONFIG_NAME).write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/4] 已写 {INSTALL_DIR / SHIM_CONFIG_NAME}")
    print(f"      深链 = {cfg['deep_link'] or '(未找到，shim 会走内置兜底)'}")
    print(f"      launcher = {cfg['launcher'] or '(未找到，仅诊断用)'}")
    print(f"      game_dir = {cfg['game_dir'] or '(未探测到，伪装模式会报错)'}")
    print(f"      启动方式 = {cfg['launch_mode']}"
          f"（auto = 先试挂起父进程，不弹启动器界面；失败回落深链）")
    print(f"      分流方式 = {cfg['launch_source']}"
          f"（parent = 按父进程判断：父进程是 steam.exe 就启动游戏，"
          f"其它情况打开配置主页）")
    print(f"      父进程白名单 = {cfg['parent_whitelist']}")
    print(f"      祖先链深度 = {cfg['parent_match_depth']}"
          f"（含直接父进程；链上任意一级命中白名单都算命中）")
    print(f"      配置侧页面 = {cfg['config_page']}"
          f"（tool = 双击 shim 时拉起工具 GUI；拉不起来才回落内置简易页）")
    print(f"      工具 GUI = {cfg['tool_path'] or '(未找到 —— 双击会回落到内置简易页)'}")
    print(f"      藏窗口 = {'开' if cfg['hide_launcher'] else '关'}"
          f"（hide_ms={cfg['hide_ms']} settle_ms={cfg['hide_settle_ms']}"
          f" grace_ms={cfg['hide_grace_ms']}）")
    if cfg["hide_launcher"]:
        print("      [注意] 2026-10-17 实测：开着藏窗口时 Games.exe 起不来"
              "（DoMainBtnClicked/cgsu 都没出现）。")
        print("            确认游戏能正常启动之后，再考虑要不要留着。")
    else:
        print("      想开启就把 shim_config.json 里 hide_launcher 改成 true")
    if cfg["impersonate"]:
        print("      伪装   = **开**（Games.exe 改名 spawn，不经过官方启动器）")
        print(f"      首次点开始游戏会在 Steam 安装目录生成一个 "
              f"{SHIM_IMPERSONATED_EXE_NAME}（本 shim 的副本）")
        verdict = lab_impersonate_verdict()
        if verdict is False:
            print("      [!!] 启动鉴权判定实验的 B 组实测 FAIL：SDK 比的是**完整路径**。")
            print("           这个方案一定 errorCode 10100，别用它启动游戏。")
        elif verdict is None:
            print("      [!] 该方案依赖「启动鉴权判定实验」的 B 组结论，"
                  "但那次实验（2026-10-17）**没有得出结论**")
            print("          （A 组阳性对照本身无效，判定被中止）。")
            print("          实验脚本与报告已随清理删除，结论保留在 README。")
            print("          结论出来之前别开 —— 没过就是 errorCode 10100。")
        print("          关掉：把 shim_config.json 里 impersonate 改成 false 后"
              "重新 shim install")
    else:
        print("      伪装   = 关（走官方启动器深链，行为与以前完全一致）")

    # --- 4/4 校验 ---
    print("[4/4] 校验")
    exe = INSTALL_DIR / SHIM_EXE_NAME
    if not exe.is_file():
        print(f"[!] 复制后仍找不到 {exe}")
        return 2
    print(f"      exe   {exe.stat().st_size:,} bytes")
    internal = INSTALL_DIR / SHIM_INTERNAL_DIR
    if internal.is_dir():
        cnt = sum(1 for _ in internal.rglob("*") if _.is_file())
        if cnt:
            print(f"      {SHIM_INTERNAL_DIR}/ 还在（{cnt} 个文件）——"
                  f"这不是本工具该留的东西，请人工确认")
        else:
            print(f"      {SHIM_INTERNAL_DIR}/ 存在但是空的（目录骨架保留，正常）")
    else:
        # 单文件版的正常形态：Steam 库里只有 Endfield.exe + shim_config.json
        print(f"      {SHIM_INTERNAL_DIR}/ 不存在（单文件版，正常）")
    if onefile:
        same, why = _exe_matches(src_exe, exe)
        print(f"      完整性  单文件 exe 与构建源{'一致 ✓' if same else '**不一致 ✗**'}"
              f"（{why}）")
        if not same:
            return 2
    admin = _exe_requires_admin(exe)
    if admin is True:
        print("      清单  requireAdministrator ✓")
    elif admin is False:
        print("[!] 清单是 asInvoker！整条链会在 Endfield.exe 那步 WinError 740。")
        print(f"    重新构建: python build_shim.py  （必须带 --uac-admin）")
        return 2
    else:
        print("      清单  读不出 requestedExecutionLevel，请人工确认是否提权")

    if not ACF_PATH.is_file():
        print(f"\n[!] appmanifest 不存在: {ACF_PATH}")
        print(f"    Steam 里不会显示「开始游戏」。先跑: python {Path(__file__).name} apply")
        return 2

    print(f"\n[完成] Steam 库中的「终末地」现在会经官方启动器启动。")
    print(f"       ACF 已在: {ACF_PATH}")
    print("       若 Steam 已在运行，请完全退出再重启（否则它会用内存里的旧状态）。")
    return 0


def shim_remove(delete_files: bool = True) -> int:
    """从 Steam 安装目录移除 shim。只删 shim 自己写的文件，其余一律不动。"""
    # 盘符根目录一定是安装目录算错了。在那儿「删 shim 自己的文件」=
    # 在盘根上删 Endfield.exe / _internal / shim.log / shim_config.json ——
    # 那不是清理，是误伤。直接拒绝，一个字节都不动。
    if _is_drive_root(INSTALL_DIR):
        print(f"[!] 安装目录算成了盘符根目录: {INSTALL_DIR}")
        print("    拒绝在盘符根目录上执行清理 —— 这一定是路径算错了。")
        return 1
    if junction_info()["is_junction"]:
        print(f"[!] {INSTALL_DIR} 是目录联接而不是 shim，请用: junction remove")
        return 1
    if not INSTALL_DIR.is_dir():
        print(f"[i] 目录不存在，无需清理: {INSTALL_DIR}")
        return 0

    gone, left = [], []
    for p in _shim_installed_files():
        if not p.exists():
            continue
        try:
            if p.is_dir():
                # _internal\ 是上一版（onedir）留下的依赖目录。
                # 现在是单文件版，它要么是空的（目录骨架），要么装着上一版的
                # 运行时依赖。两种都能安全删；**但**里面要是有不是本工具
                # 产物的东西，就一个字节都不删，只把名字报出来。
                if p.name == SHIM_INTERNAL_DIR:
                    # _internal\ 是上一版（onedir）留下的依赖目录。
                    # 现在是单文件版，它要么是空的（目录骨架），要么装着上一版的
                    # 运行时依赖。两种都能整个删掉；**但**里面要是有不是本工具
                    # 产物的东西，就一个字节都不删，只把名字报出来。
                    bad = _internal_foreign_entries(p)
                    if bad:
                        left.append(f"{p.name} (里有不是本工具产物的东西，已保留："
                                    + ", ".join(sorted(bad)[:5]) + ")")
                        continue
                    shutil.rmtree(p)
                else:
                    shutil.rmtree(p)
            else:
                p.unlink()
            gone.append(p.name)
        except OSError as e:
            left.append(f"{p.name} ({e})")

    if gone:
        print(f"[清理] 已删除: {', '.join(gone)}")
    if left:
        print(f"[!] 部分删除失败: {'; '.join(left)}")
    rest = [p.name for p in INSTALL_DIR.iterdir()] if INSTALL_DIR.is_dir() else []
    if rest:
        print(f"[i] 目录里还有其它内容，未触碰: {rest}")
    if not delete_files:
        print("[i] --keep-files：已跳过实际删除（仅列出）")
    return 0 if not left else 2


def _describe_launch_mode(mode: str) -> str:
    """把 launch_mode 说成人话。认不出的值不猜，直接原样报出来。"""
    return {
        "auto": "挂起父进程优先（auto），失败回落官方启动器深链",
        "suspended_parent": "挂起父进程（suspended_parent），**不经过启动器界面**，失败报错",
        "deep_link": "官方启动器深链（deep_link），**会弹出启动器界面**",
    }.get(mode, f"{mode}（不认识的取值，shim 会当 auto 处理）")


def _describe_launch_source(source: str) -> str:
    """把 launch_source 说成人话。"""
    return {
        "parent": "按父进程判断（parent）—— **祖先链**上任意一级的文件名是 "
                  "steam.exe 就启动游戏，其它情况打开配置主页",
        "always_launch": "永远启动游戏（always_launch），旁路父进程判断",
        "always_config": "永远打开配置主页（always_config），旁路父进程判断",
    }.get(source, f"{source}（不认识的取值，shim 会当 parent 处理）")


def cmd_shim(args: argparse.Namespace) -> int:
    # 先解析库/官服目录，status 才拿得到准确的 OFFICIAL_GAME_DIR 和安装目录
    # （shim_install 内部还会再调一次，幂等）。
    resolve_game_paths()
    action = args.action
    if action == "status":
        st = shim_status()
        if getattr(args, "json", False):
            print(json.dumps(st, ensure_ascii=False, indent=2))
            return 0
        print(f"Steam 安装目录 : {st['install_dir']}"
              f"{'' if st['install_dir_exists'] else '  (不存在)'}")
        if st["is_junction"]:
            print(f"  [旧方案]    目录联接 -> {st['junction_target']}")
            print("               这个方案已被实测证伪，跑 shim install 会先拆掉它")
        print(f"shim           : {'已部署' if st['shim_installed'] else '未部署'}"
              f"{'  ' + format(st['shim_bytes'], ',') + ' bytes' if st['shim_installed'] else ''}")
        print(f"  部署形态    : {st['shim_mode_text']}")
        if st["shim_payload_ok"] is not None:
            print(f"  完整性      : {'与构建源逐字节一致 ✓' if st['shim_payload_ok'] else '**与构建源不一致 ✗**'}"
                  f"（{st['shim_payload_note']}）")
        print(f"  shim 配置   : {st['shim_config'] or '(无)'}")
        print(f"  深链 scheme : {st['shim_config_scheme'] or st['deep_link_scheme'] or '(未找到)'}")
        print(f"  深链 URL    : {st['deep_link'] or '(未找到 —— 只会打开启动器界面)'}")
        print(f"  启动器      : {st['shim_config_launcher'] or st['official_launcher'] or '(未找到)'}")
        print(f"  启动方式    : {'Games.exe 伪装（实验性，不经官方启动器）' if st['impersonate'] else _describe_launch_mode(st['launch_mode'])}")
        print(f"  分流方式    : {_describe_launch_source(st['launch_source'])}")
        print(f"  父进程白名单: {st['parent_whitelist']}（祖先链上任意一级的文件名"
              f"命中这些名字就启动游戏，否则打开配置主页）")
        print(f"  祖先链深度  : {st['parent_match_depth']}（含直接父进程；"
              f"链上任意一级命中白名单都算命中）")
        print(f"  配置侧页面  : {st['config_page']}"
              f"（tool = 双击 shim 时拉起工具 GUI；拉不起来才回落内置简易页）")
        print(f"  工具 GUI    : {st['tool_path'] or '(未配置 —— 双击会回落到内置简易页)'}")
        print(f"  game_dir    : {st['game_dir'] or '(未配置，伪装模式会报错)'}")
        if st["impersonate"]:
            print(f"  Games.exe   : {st['impersonated_exe']} "
                  f"{'✓ 已生成' if st['impersonated_exe_exists'] else '× 尚未生成（首次启动时创建）'}")
            if not st["impersonate_verified"]:
                print("    [!] 该方案依赖「启动鉴权判定实验」的 B 组结论；"
                      "没过就是 errorCode 10100。")
        print(f"官服游戏目录  : {st['official_game_dir'] or '(未找到)'}")
        print(f"官方启动器    : {st['official_launcher'] or '(未找到)'}")
        print(f"appmanifest   : {st['acf']} {'✓' if st['acf_exists'] else '× 缺失'}")
        print(f"构建产物      : {st['src_dir']} {'✓' if st['src_available'] else '× 未构建'}")
        return 0 if st["shim_installed"] else 1

    if action == "install":
        return shim_install(src_dir=getattr(args, "src", None),
                            launcher=getattr(args, "launcher", None),
                            force=args.force,
                            impersonate=getattr(args, "impersonate", False))

    if action == "remove":
        return shim_remove(delete_files=not getattr(args, "keep_files", False))

    if action == "unlink":
        return remove_junction(INSTALL_DIR)

    return 2


# ---------------------------------------------------------------------------
# 从零创建 ACF
# ---------------------------------------------------------------------------

def build_acf_template(
    manifest: str,
    buildid: str,
    size_on_disk: int,
    last_updated: int | None = None,
    language: str = DEFAULT_LANGUAGE,
    launcher_path: str | None = None,
) -> dict:
    """生成一份 Steam 能识别的 appmanifest 内容。"""
    last_updated = int(last_updated or time.time())
    size_s = str(int(size_on_disk))
    return {
        "appid": str(APP_ID),
        "universe": "1",
        "LauncherPath": launcher_path or r"C:\\Program Files (x86)\\Steam\\steam.exe",
        "name": GAME_DISPLAY,
        "StateFlags": "4",
        "installdir": GAME_NAME,
        "LastUpdated": str(last_updated),
        "SizeOnDisk": size_s,
        "StagingSize": "0",
        "buildid": str(buildid),
        "TargetBuildID": str(buildid),
        "LastOwner": "0",
        "DownloadType": "0",
        "UpdateResult": "0",
        "BytesToDownload": "0",
        "BytesDownloaded": "0",
        "BytesToStage": "0",
        "BytesStaged": "0",
        "AutoUpdateBehavior": "1",
        "AllowOtherDownloadsWhileRunning": "2",
        "AllowLinuxRuntime": "0",
        "ScheduledAutoUpdate": "0",
        "InstalledDepots": {
            DEPOT_ID: {
                "manifest": str(manifest),
                "size": size_s,
            }
        },
        "UserConfig": {"language": language},
        "MountedConfig": {"language": language},
    }


def create_acf(manifest: str, buildid: str, size_on_disk: int, last_updated: int | None = None) -> int:
    if ACF_PATH.exists():
        bak = BACKUP_DIR / BACKUP_NAME
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ACF_PATH, bak)
        print(f"[备份] {ACF_PATH} -> {bak}")
    launcher = str(STEAM_ROOT / "steam.exe") if (STEAM_ROOT / "steam.exe").exists() else None
    data = build_acf_template(manifest, buildid, size_on_disk, last_updated, launcher_path=launcher)
    ACF_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_acf(ACF_PATH, data)
    print(f"[ACF] 已创建: {ACF_PATH}")
    print(f"      appid={APP_ID} name={GAME_DISPLAY} installdir={GAME_NAME}")
    print(f"      buildid={buildid} manifest={manifest} size={size_on_disk}")
    return 0


# ---------------------------------------------------------------------------
# 启动项
# ---------------------------------------------------------------------------

def set_launch_options(text: str) -> int:
    """
    终末地的 DX 切换：appinfo 的 config.launch 定义了
        0: Endfield.exe
        1: Endfield.exe -force-d3d11   (DirectX 11)
    Steam 的 localconfig.vdf 是二进制 VDF，稳妥做法是让用户自己在
    Steam 界面里填。这里只负责记录 + 给出准确提示。
    """
    text = clean_text(text or "")
    note = BACKUP_DIR / "launch_options.txt"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    note.write_text(text + "\n", encoding="utf-8")
    print(f"[启动项] 已记录到 {note}")
    print("请在 Steam 中：右键「终末地」→ 属性 → 启动选项 填入：")
    if text.strip():
        print(f"  {text.strip()}")
    else:
        print("  （留空 = 默认 DX12）")
    return 0


# ---------------------------------------------------------------------------
# 一键完整转换（ACF + mklink）
# ---------------------------------------------------------------------------

def cmd_convert(args: argparse.Namespace) -> int:
    """完整流程：生成/更新 ACF + 部署 shim（默认）。"""
    resolve_game_paths()
    print("=== 完整转换：Hypergryph 官服 → Steam 启动 ===")
    game_dir = clean_path(args.game_dir) if args.game_dir else None

    if game_dir is not None and not game_dir.exists():
        print(f"[!] 官服游戏目录不存在: {game_dir}")
        return 2

    if game_dir:
        global OFFICIAL_GAME_DIR
        OFFICIAL_GAME_DIR = game_dir

    manifest, buildid, depot_size, last_updated, source = _resolve_manifest_buildid(args)
    print(f"[来源] manifest/buildid 取自: {source}")

    # 体积：量官服目录本体，而不是 Steam 侧目录（后者装的是 shim，只有几 MB）
    size_src = game_dir if game_dir and game_dir.exists() else (OFFICIAL_GAME_DIR or INSTALL_DIR)
    print(f"[体积] 统计 {size_src} …")
    size = folder_size(size_src) if size_src.exists() else 0
    if size <= 0:
        size = int(depot_size or 0)
    print(f"[体积] {size} ({size / 1024**3:.2f} GiB)")

    # 步骤 1：ACF
    print("\n--- 步骤 1/2：写入 appmanifest ---")
    if not ACF_PATH.exists() or args.recreate_acf:
        rc = create_acf(manifest, buildid, size, last_updated)
        if rc != 0:
            return rc
    ns = argparse.Namespace(
        manifest=manifest,
        buildid=buildid,
        depot_size=str(size) if size else None,
        from_steamdb=None,
        last_updated=last_updated,
        auto_update_behavior=1,
        use_target_as_build=False,
        force_backup=False,
    )
    rc = cmd_apply(ns)
    if rc != 0:
        return rc

    # 步骤 2：shim
    if args.force_junction:
        # 联接方案已退役。这里**必须**在写 ACF 之前就拒绝掉吗？不 ——
        # 但绝对不能再去建联接（那段会整目录改名的代码已经删掉了）。
        # 明确报错并指路，不静默改走别的路径。
        print("\n--- 步骤 2/2：目录联接 (mklink /j) 【已退役】---")
        print(JUNCTION_RETIRED_NOTICE)
        print()
        print("[退役] convert --force-junction 已停用，未做任何文件操作。")
        print("[退役] 改用: python endfield_updater.py shim install")
        return JUNCTION_RETIRED_EXIT_CODE

    print("\n--- 步骤 2/2：部署 shim ---")
    _src_hit, _src_tried = resolve_shim_src_dir()
    if _src_hit is None:
        print(f"[!] shim 还没构建。试过的路径:")
        for _c in _src_tried:
            print(f"    试过: {_c}")
        print(f"    先跑: python build_shim.py")
        return 1
    rc = shim_install(force=getattr(args, "force", False))
    if rc != 0:
        return rc

    if args.launch_options is not None:
        set_launch_options(args.launch_options)

    print("[注意] 终末地带 ACE 反作弊，Steam 卸载不会清驱动，卸载后请手动清理。")
    return 0


# ---------------------------------------------------------------------------
# 目录指纹（测试污染保险）
# ---------------------------------------------------------------------------
#
# 为什么需要它（2026-10-20 事故复盘）：
#   上一轮的自测在真实 Steam 库里写进了 7 字节的假 exe，还把
#   `_internal\python314.dll` 覆盖成了别的东西。事后加的保险只对
#   `Endfield.exe` **一个文件**取 md5 —— 抓不到 `_internal\` 里任何东西，
#   也抓不到 `shim_config.json` 被改。
#
#   所以这里做的是**整棵目录树的递归指纹**：
#     相对路径 -> {类型, 大小, md5}
#   前后一比，新增 / 删除 / 改大小 / 改内容 / 目录增删**全都**能报出来，
#   而且能指名道姓地报出「哪一条路径变了」。

FINGERPRINT_FILE = "F"
FINGERPRINT_DIR = "D"
FINGERPRINT_LINK = "L"


class FingerprintError(RuntimeError):
    """目录指纹取不到。

    这是**硬错误**：保险取不到快照时绝不能当成「没变化」悄悄放过去，
    必须让调用方把它当成失败报出来。
    """


def _md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_link_like(p: Path) -> bool:
    """这个路径是不是联接（junction）或符号链接。

    联接**不能跟着走** —— 跟着走就等于去读官服游戏本体（几十 GB），
    而且官服那边一变，这边的指纹就跟着变，保险会误报。
    """
    if p.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            return bool(isjunction(p))
        except OSError:
            return False
    return False


def _link_target_text(p: Path) -> str:
    try:
        return os.readlink(p)
    except OSError:
        try:
            return str(p.resolve())
        except OSError:
            return ""


def dir_tree_fingerprint(root: Path | str, missing_ok: bool = True) -> dict:
    """给整棵目录树做一份递归指纹。

    返回 ``{相对路径: {"t": 类型, "s": 大小, "m": md5}}``：
      * ``"."`` 是根目录自己 —— 有这个键 = 根目录存在；没有 = 根目录不存在。
        这样「目录不存在」和「目录存在但空」才分得开。
      * 文件 -> ``t="F"``，``s`` 是字节数，``m`` 是内容 md5。
      * 目录 -> ``t="D"``。
      * 联接/符号链接 -> ``t="L"``，``m`` 里放链接目标（**不跟随进去**）。

    ``missing_ok=True``（默认）时根目录不存在就返回空字典；
    ``missing_ok=False`` 时根目录不存在会抛 :class:`FingerprintError`。

    读不动（权限不足、文件被占用、IO 错误）一律抛
    :class:`FingerprintError` —— **绝不**把「读不到」当成「没变化」。
    """
    root = Path(root)
    if not os.path.lexists(str(root)):
        if missing_ok:
            return {}
        raise FingerprintError(f"目录不存在: {root}")
    if not root.is_dir():
        raise FingerprintError(f"不是目录: {root}")

    out: dict[str, dict] = {".": {"t": FINGERPRINT_DIR, "s": 0, "m": ""}}
    stack: list[Path] = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError as e:
            raise FingerprintError(f"读不了目录 {d}: {e}") from e
        for p in entries:
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            try:
                if _is_link_like(p):
                    out[rel] = {"t": FINGERPRINT_LINK, "s": 0,
                                "m": _link_target_text(p)}
                elif p.is_dir():
                    out[rel] = {"t": FINGERPRINT_DIR, "s": 0, "m": ""}
                    stack.append(p)
                elif p.is_file():
                    st = p.stat()
                    out[rel] = {"t": FINGERPRINT_FILE, "s": st.st_size,
                                "m": _md5_of_file(p)}
                else:
                    out[rel] = {"t": "?", "s": 0, "m": ""}
            except OSError as e:
                raise FingerprintError(f"读不了 {p}: {e}") from e
    return out


def _describe_fingerprint_entry(entry: dict) -> str:
    kind = entry.get("t")
    if kind == FINGERPRINT_FILE:
        return f"文件 {entry.get('s', 0)}B md5={str(entry.get('m', ''))[:8]}"
    if kind == FINGERPRINT_DIR:
        return "目录"
    if kind == FINGERPRINT_LINK:
        return f"链接 -> {entry.get('m', '')}"
    return "未知类型"


def diff_dir_tree_fingerprint(before: dict, after: dict) -> list[str]:
    """比对两份目录指纹，返回**人类可读**的差异列表。

    空列表 = 一模一样。每一条差异都指名道姓地说清是哪个路径、
    怎么变的（新增 / 缺失 / 类型变了 / 大小变了 / 内容变了）。
    """
    diffs: list[str] = []
    for rel in sorted(set(before) | set(after)):
        b = before.get(rel)
        a = after.get(rel)
        if b is None and a is not None:
            diffs.append(f"新增 {rel}（{_describe_fingerprint_entry(a)}）")
        elif a is None and b is not None:
            diffs.append(f"缺失 {rel}（{_describe_fingerprint_entry(b)}）")
        elif b is not None and a is not None and b != a:
            diffs.append(
                f"变更 {rel}（{_describe_fingerprint_entry(b)} -> "
                f"{_describe_fingerprint_entry(a)}）"
            )
    return diffs


# ---------------------------------------------------------------------------
# Steam「非 Steam 快捷方式」陷阱检测（只读）
# ---------------------------------------------------------------------------
#
# 陷阱长这样：Steam 里除了正常的游戏条目（Arknights: Endfield，AppID 4732690，
# 指向我们部署的 shim），用户还可能有一个**非 Steam 快捷方式**（AppID 2422059311），
# 它的 Exe **直接指向官服游戏本体**：
#
#     C:\Program Files\Hypergryph Launcher\games\Endfield Game\Endfield.exe
#
# 点它的后果：完全绕过 shim —— 父进程不是 Games.exe，SDK 固定报
# errorCode 10100；游戏自己走深链重起第二实例，于是**启动器界面照样弹出来**。
#
# 本函数**只读**：全程不写、不改、不删 Steam 的任何文件。

SHORTCUTS_VDF_NAME = "shortcuts.vdf"

# Valve KeyValues 的类型码（**二进制**格式）。
# 本机实测：Steam 写的 userdata\<id>\config\shortcuts.vdf 就是二进制格式，
# 结构是「类型字节 + 键名\0 + 值」，嵌套块用 TYPE_NONE 开头、TYPE_END 收尾。
KV_TYPE_NONE = 0x00
KV_TYPE_STRING = 0x01
KV_TYPE_INT32 = 0x02
KV_TYPE_FLOAT32 = 0x03
KV_TYPE_POINTER = 0x04
KV_TYPE_WSTRING = 0x05
KV_TYPE_COLOR = 0x06
KV_TYPE_UINT64 = 0x07
KV_TYPE_END = 0x08
KV_TYPE_INT64 = 0x0A

_KV_FIXED_SIZES = {
    KV_TYPE_INT32: 4,
    KV_TYPE_FLOAT32: 4,
    KV_TYPE_POINTER: 4,
    KV_TYPE_COLOR: 4,
    KV_TYPE_UINT64: 8,
    KV_TYPE_INT64: 8,
}


def load_shim_config(install_dir: Path | str | None = None) -> dict:
    """读部署目录里的 ``shim_config.json``。读不到 / 坏掉就返回 ``{}``。"""
    base = Path(install_dir) if install_dir is not None else INSTALL_DIR
    try:
        data = json.loads((base / SHIM_CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _kv_binary_block(data: bytes, pos: int, depth: int,
                     max_depth: int = 32) -> tuple[dict, int, bool]:
    """解析二进制 KeyValues 的一个块。返回 (dict, 新位置, 是否正常收尾)。

    **容错**：遇到截断 / 未知类型就返回「已解析的部分」，绝不抛异常。
    """
    out: dict = {}
    n = len(data)
    while pos < n:
        t = data[pos]
        pos += 1
        if t == KV_TYPE_END:
            return out, pos, True
        end = data.find(b"\x00", pos)
        if end < 0:
            return out, n, False
        key = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        if t == KV_TYPE_NONE:
            if depth >= max_depth:
                return out, pos, False
            sub, pos, ok = _kv_binary_block(data, pos, depth + 1, max_depth)
            out[key] = sub
            if not ok:
                return out, pos, False
            continue
        if t == KV_TYPE_STRING:
            end = data.find(b"\x00", pos)
            if end < 0:
                out[key] = ""
                return out, n, False
            out[key] = data[pos:end].decode("utf-8", "replace")
            pos = end + 1
            continue
        if t == KV_TYPE_WSTRING:
            if pos + 2 > n:
                return out, n, False
            cnt = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
            raw = data[pos:pos + cnt * 2]
            pos += cnt * 2
            if pos < n and data[pos] == 0:
                pos += 1
            out[key] = raw.decode("utf-16-le", "replace")
            continue
        size = _KV_FIXED_SIZES.get(t)
        if size is None:
            return out, pos, False            # 未知类型：停下，别猜
        if pos + size > n:
            return out, n, False
        raw = data[pos:pos + size]
        pos += size
        if t == KV_TYPE_INT32:
            out[key] = int.from_bytes(raw, "little", signed=False)
        elif t == KV_TYPE_UINT64:
            out[key] = int.from_bytes(raw, "little", signed=False)
        elif t == KV_TYPE_INT64:
            out[key] = int.from_bytes(raw, "little", signed=True)
        elif t == KV_TYPE_FLOAT32:
            out[key] = struct.unpack("<f", raw)[0]
        else:
            out[key] = raw.hex()
    return out, pos, False


def parse_keyvalues_binary(data: bytes) -> dict:
    """解析 Valve **二进制** KeyValues。截断 / 畸形时返回已解析的部分。"""
    obj, _pos, _ok = _kv_binary_block(data, 0, 0)
    return obj


def _kv_tokenize(text: str) -> list[str]:
    """把 Valve KeyValues **文本**切成 token（容错版）。

    处理：带引号的串（引号永远结束字符串）、花括号、以及不带引号的裸词。
    所有控制字符（含 Steam 写进 vdf 的 \x00 二进制标记）都当分隔符。

    **反斜杠按字面保留**（不当成转义符）：本机实测 Steam 写进 vdf 的路径就是
    单反斜杠形式。真按 Valve 文本格式把反斜杠吃掉，路径会被毁掉。
    路径比较另有 _norm_win_path 负责归一化。
    """
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace() or ord(ch) < 0x20:
            i += 1
            continue
        if ch == "{":
            tokens.append("{")
            i += 1
            continue
        if ch == "}":
            tokens.append("}")
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == '"':
                    # 引号**永远**结束字符串 —— 连着它前面那个反斜杠一起。
                    # 不这么做的话，"...\\Endfield Game\" 这种「结尾带反斜杠」
                    # 的路径会把后面的内容全吞进这个串里（Steam 存 StartDir
                    # 就是这个样子），后面的字段全部解析错位。
                    i += 1
                    break
                buf.append(c)
                i += 1
            tokens.append("".join(buf))
            continue
        # 裸词
        buf = []
        while i < n and not text[i].isspace() and ord(text[i]) >= 0x20 \
                and text[i] not in "{}":
            buf.append(text[i])
            i += 1
        if buf:
            tokens.append("".join(buf))
    return tokens


def parse_keyvalues(text: str) -> dict:
    """解析 Valve KeyValues **文本**，返回嵌套 dict（**容错**）。

    * 坏掉的 / 截断的文件不抛异常 —— 能解析多少算多少。
    * 同一个键出现多次时**后者覆盖前者**。
    * 缺值的键（后面直接是 } ）记成空串。
    """
    tokens = _kv_tokenize(text)
    pos = 0

    def parse_block(depth: int) -> tuple[dict, int]:
        nonlocal pos
        obj: dict = {}
        if depth > 64:              # 防畸形文件把栈撑爆
            return obj, pos
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return obj, pos
            if tok == "{":
                pos += 1
                continue
            key = tok
            pos += 1
            if pos < len(tokens) and tokens[pos] == "{":
                pos += 1
                sub, pos = parse_block(depth + 1)
                obj[key] = sub
            elif pos < len(tokens) and tokens[pos] != "}":
                val = tokens[pos]
                pos += 1
                obj[key] = val
            else:
                obj[key] = ""
        return obj, pos

    result, _ = parse_block(0)
    return result


def parse_shortcuts_vdf(data: bytes) -> tuple[dict, str]:
    """解析一份 shortcuts.vdf，返回 (解析结果, 用了哪种格式)。

    Steam 写出来的 ``shortcuts.vdf`` 是**二进制** KeyValues；也有人手工
    编辑成**文本**格式。这里两种都支持：先按二进制试，拿到 shortcuts 段
    就用它；否则退回文本格式。

    两种都失败时返回 ``({}, "无法解析")`` —— **不抛异常**。
    """
    if not data:
        return {}, "空文件"
    obj = parse_keyvalues_binary(data)
    shortcuts = obj.get("shortcuts")
    if isinstance(shortcuts, dict) and shortcuts:
        return obj, "二进制"
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        return {}, "无法解析"
    obj2 = parse_keyvalues(text)
    shortcuts2 = obj2.get("shortcuts")
    if isinstance(shortcuts2, dict) and shortcuts2:
        return obj2, "文本"
    if isinstance(shortcuts, dict):
        return obj, "二进制"
    if isinstance(shortcuts2, dict):
        return obj2, "文本"
    return {}, "无法解析"


def _norm_win_path(value: str) -> str:
    """把路径归一化成可比的形式（Windows 不区分大小写、去尾部斜杠）。"""
    try:
        s = os.path.normpath(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return ""
    s = s.replace("/", "\\").rstrip("\\")
    if len(s) > 3 and s[1:3] == ":\\":
        s = s[:3] + s[3:].lstrip("\\")
    return s.lower()


def _steam_shortcut_dirs(steam_roots: list[Path] | None = None) -> list[Path]:
    """列出所有可能有 ``userdata\\*\\config\\shortcuts.vdf`` 的目录。"""
    roots: list[Path] = []
    if steam_roots is not None:
        roots = [Path(r) for r in steam_roots]
    else:
        roots = list(find_steam_roots())
        try:
            for lib in discover_steam_libraries():
                p = Path(lib["library_path"])
                if p not in roots:
                    roots.append(p)
        except Exception:
            pass
        if not roots:
            roots = [DEFAULT_STEAM_ROOT]

    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        userdata = root / "userdata"
        try:
            if not userdata.is_dir():
                continue
            for account in sorted(userdata.iterdir(), key=lambda p: p.name):
                if not account.is_dir():
                    continue
                cfg = account / "config"
                if not cfg.is_dir():
                    continue
                key = str(cfg).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(cfg)
        except OSError:
            continue
    return out


def scan_steam_shortcuts(game_dir: Path | str | None = None,
                         steam_roots: list[Path] | None = None) -> dict:
    """只读扫描 Steam 的 ``userdata\\*\\config\\shortcuts.vdf``。

    返回::

        {
          "game_dir": "<官服游戏目录>" | "",
          "traps":   [ {name, appid, exe, exe_path, startdir,
                       launch_options, vdf, user}, ... ],
          "skipped": [ {path, error}, ... ],   # 解析失败/读不了的（跳过，不报错）
          "scanned": <扫过多少个 shortcuts.vdf>,
          "formats": {"二进制": n, "文本": m},  # 每种格式各读到几个
        }

    * ``game_dir`` 为空时**直接跳过检测**（不猜路径）。
    * ``traps`` 里的每一条，其 ``Exe`` 都**直接指向官服游戏本体的 exe**
      （也就是 ``game_dir\\Endfield.exe``）—— 点它会完全绕过 shim。
    * 全程**只读**：不写、不改、不删 Steam 的任何文件。
    """
    result: dict = {"game_dir": "", "traps": [], "skipped": [], "scanned": 0,
                    "formats": {}}
    if game_dir is None:
        cfg = load_shim_config()
        game_dir = cfg.get("game_dir") or ""
    game_dir = str(game_dir or "").strip()
    if not game_dir:
        # 没有 game_dir 就不猜 —— 宁可漏报也不要报错。
        return result
    result["game_dir"] = game_dir

    target_exe = _norm_win_path(str(Path(game_dir) / GAME_EXE_NAME))
    if not target_exe:
        return result

    for cfg_dir in _steam_shortcut_dirs(steam_roots):
        vdf = cfg_dir / SHORTCUTS_VDF_NAME
        if not vdf.is_file():
            continue
        result["scanned"] += 1
        try:
            raw = vdf.read_bytes()
        except OSError as e:
            result["skipped"].append({"path": str(vdf), "error": f"读不了: {e}"})
            continue
        try:
            data, fmt = parse_shortcuts_vdf(raw)
        except Exception as e:                       # noqa: BLE001
            result["skipped"].append({"path": str(vdf), "error": f"解析失败: {e}"})
            continue

        shortcuts = data.get("shortcuts")
        if not isinstance(shortcuts, dict):
            result["skipped"].append({
                "path": str(vdf),
                "error": f"格式不对：没有 shortcuts 段（{fmt}）",
            })
            continue
        result["formats"][fmt] = result["formats"].get(fmt, 0) + 1

        for key in sorted(shortcuts.keys(), key=lambda k: str(k)):
            entry = shortcuts.get(key)
            if not isinstance(entry, dict):
                continue
            exe = str(entry.get("Exe") or entry.get("exe") or "")
            if not exe:
                continue
            if _norm_win_path(exe) != target_exe:
                continue
            result["traps"].append({
                "name": str(entry.get("AppName") or entry.get("appname") or key),
                "appid": str(entry.get("appid") or ""),
                "exe": exe,
                # Steam 存的 Exe 带外层引号（"C:\...\Endfield.exe"），显示时去掉
                "exe_path": exe.strip().strip('"'),
                "startdir": str(entry.get("StartDir") or entry.get("startdir") or ""),
                "launch_options": str(entry.get("LaunchOptions")
                                      or entry.get("launchoptions") or ""),
                "vdf": str(vdf),
                "user": vdf.parent.parent.name,
            })
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=f"Steam {GAME_DISPLAY} appmanifest 更新器（{APP_ID} / depot {DEPOT_ID}）"
    )
    p.add_argument(
        "--library",
        help="指定 Steam 库根目录（如 E:\\SteamLibrary）。多库机器上必须指定，"
             "否则写错库 Steam 看不到游戏",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="查看当前 ACF 与自动 manifests")
    sp.add_argument("--json", action="store_true", help="附加 JSON 输出")

    sub.add_parser("check", help="环境自检（Steam / appinfo / 权限 / 联接状态）")

    ap = sub.add_parser("apply", help="写入 manifest/buildid，阻止 Steam 更新覆盖")
    ap.add_argument("--manifest", help="depot manifest id（覆盖自动值）")
    ap.add_argument("--buildid", help="buildid")
    ap.add_argument("--depot-size", help="depot size 字节（默认用目录实测）")
    ap.add_argument("--from-steamdb", help='粘贴 SteamDB 复制串，如 "-app 4732690 -depot 4732691 -manifest 123"')
    ap.add_argument("--last-updated", type=int, help="LastUpdated unix 时间戳")
    ap.add_argument("--auto-update-behavior", type=int, choices=[0, 1, 2], help="Steam AutoUpdateBehavior")
    ap.add_argument("--use-target-as-build", action="store_true", help="用 ACF 里的 TargetBuildID 作为 buildid")
    ap.add_argument("--force-backup", action="store_true", help="覆盖已有备份")

    sub.add_parser("restore", help="从备份恢复 ACF")

    fp = sub.add_parser("fetch", help="仅打印 appinfo 自动解析结果")
    fp.add_argument("--json", action="store_true")

    jp = sub.add_parser(
        "junction",
        help="[已退役] 目录联接方案（mklink /j）—— 只打印说明并返回非零，不做任何事",
        description="目录联接方案已退役：鉴权过不去（父进程是 steam.exe，"
                    "固定 10100），且建联接前的整目录改名会搬走你的游戏安装。"
                    "改用: shim install",
    )
    jp.add_argument("action", choices=["info", "create", "remove"],
                    help="操作（三个都已退役）")
    jp.add_argument("--game-dir", help="官服游戏目录（联接目标）—— 已退役，忽略")
    jp.add_argument("--force", action="store_true",
                    help="已退役，忽略（原来会整目录改名，那段代码已删）")

    sp2 = sub.add_parser(
        "shim",
        help="部署/移除 Steam 侧 shim（现行方案，取代目录联接）",
        description="在 Steam 安装目录放一个同名 Endfield.exe shim，"
                    "由它调起鹰角官方启动器并把启动交回去。",
    )
    sp2.add_argument("action", choices=["status", "install", "remove", "unlink"],
                     help="status=看状态 / install=部署 / remove=移除 shim / unlink=拆旧联接")
    sp2.add_argument("--json", action="store_true")
    sp2.add_argument("--src", help="shim 构建产物目录（默认 shim_build/dist/Endfield）")
    sp2.add_argument("--launcher", help="官方启动器 Launcher.exe 路径（默认自动探测）")
    sp2.add_argument("--force", action="store_true", help="安装目录非空时改名为备份后部署")
    sp2.add_argument("--keep-files", action="store_true", help="remove 时只列出不删")
    sp2.add_argument(
        "--impersonate", action="store_true",
        help="启用 Games.exe 伪装（调度器改名 spawn，完全不经官方启动器）。"
             "**实验性**：依赖「启动鉴权判定实验」的 B 组结论，未验证前别开。"
             "不加这个开关就是现行可用的深链方案。")

    cp = sub.add_parser("convert", help="一键完整转换（ACF + shim）")
    cp.add_argument("--game-dir",
                    help='官服游戏目录，例如 '
                         r'"C:\Program Files\Hypergryph Launcher\games\Endfield Game"')
    cp.add_argument("--manifest", help="manifest id")
    cp.add_argument("--buildid", help="buildid")
    cp.add_argument("--depot-size", help="depot size 字节")
    cp.add_argument("--from-steamdb", help="粘贴 SteamDB 复制串")
    cp.add_argument("--last-updated", type=int, help="LastUpdated unix 时间戳")
    cp.add_argument("--recreate-acf", action="store_true", help="强制重建 ACF")
    cp.add_argument("--force-junction", action="store_true",
                    help="[已退役] 用目录联接代替 shim —— 已停用，"
                         "会报错退出并指路 shim install")
    cp.add_argument("--force", action="store_true", help="安装目录非空时改名为备份后部署 shim")
    cp.add_argument("--launch-options", help=f'启动项，如 "{LAUNCH_DX11}"；留空字符串表示 DX12')

    fx = sub.add_parser("find", help="定位 Endfield.exe / Steam 库目标")
    fx.add_argument("--start", help="从该目录开始查找（官服「重新定位游戏」）")
    fx.add_argument("--json", action="store_true")

    tp = sub.add_parser("targets", help="列出创建联接时可用的 Steam 目标位置")
    tp.add_argument("--json", action="store_true")

    sub.add_parser("d3d11", help=f"快捷：记录 {LAUNCH_DX11} 启动项")
    sub.add_parser("d3d12", help="快捷：记录 DX12 启动项（即清空启动选项）")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.cmd

    if getattr(args, "library", None):
        pin_library(args.library)

    if cmd == "status":
        return cmd_status(args)
    if cmd == "check":
        return cmd_check(args)
    if cmd == "apply":
        return cmd_apply(args)
    if cmd == "restore":
        return cmd_restore(args)
    if cmd == "fetch":
        return cmd_fetch(args)
    if cmd == "junction":
        # 整条联接路线已退役。入口保留（老脚本可能还在调），但**只打印说明
        # 并返回非零**，一个文件都不碰 —— 包括那个会整目录改名的老逻辑，
        # 它已经从 create_junction 里删掉了。
        print(JUNCTION_RETIRED_NOTICE)
        print()
        print(f"[退役] 子命令: junction {getattr(args, 'action', '?')}")
        print("[退役] 未做任何文件操作。")
        return JUNCTION_RETIRED_EXIT_CODE
    if cmd == "shim":
        return cmd_shim(args)
    if cmd == "convert":
        return cmd_convert(args)
    if cmd == "d3d11":
        return set_launch_options(LAUNCH_DX11)
    if cmd == "d3d12":
        return set_launch_options(LAUNCH_DX12)
    if cmd == "find":
        paths = resolve_game_paths()
        start = Path(args.start) if args.start else None
        exes = find_endfield_exe(start)
        payload = {
            "resolved": paths,
            f"{GAME_EXE_NAME.lower().replace(' ', '_')}": [str(p) for p in exes],
            "game_folder": str(exes[0].parent) if exes else None,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Steam 根: {paths['steam_root']}")
            print(f"appmanifest: {paths['appmanifest']}")
            print(f"安装目录: {paths['install_dir']}")
            print(f"找到 {len(exes)} 个 {GAME_EXE_NAME}:")
            for x in exes:
                print(f"  {x}")
            if exes:
                print(f"游戏文件夹: {exes[0].parent}")
        return 0 if exes else 1
    if cmd == "targets":
        rows = steam_link_targets()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print("创建 mklink 时可用的 Steam 目标：")
            for i, r in enumerate(rows):
                mark = " [有游戏]" if r["has_game"] else ""
                print(f"{i}. {r['label']}{mark}")
                print(f"   appmanifest: {r['appmanifest']} {'✓' if r['appmanifest_exists'] else '×'}")
                print(f"   install_dir: {r['install_dir']} {'✓' if r['install_dir_exists'] else '×'}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())