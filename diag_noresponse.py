#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断「点开始游戏没反应」。"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STEAM_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield")
STEAM = Path(r"C:\Program Files (x86)\Steam")
OFFICIAL = Path(r"C:\Program Files\Hypergryph Launcher\games\Endfield Game")


def head(t):
    print()
    print("=" * 70)
    print(t)
    print("=" * 70)


head("1. shim.log（shim 有没有被拉起来过）")
log = STEAM_DIR / "shim.log"
if log.is_file():
    txt = log.read_text(encoding="utf-8", errors="replace")
    lines = txt.splitlines()
    print(f"存在，{len(lines)} 行，mtime={time.strftime('%H:%M:%S', time.localtime(log.stat().st_mtime))}")
    for l in lines[-25:]:
        print("  " + l)
else:
    print("!! 不存在 —— shim 从来没跑起来过（或跑起来但没权限写日志）")

head("2. Steam 侧目录当前内容")
if STEAM_DIR.is_dir():
    for p in sorted(STEAM_DIR.iterdir()):
        mt = time.strftime("%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        print(f"  {p.name:22s} {p.stat().st_size:>12,}   {mt}")
else:
    print("!! 目录不存在")

head("3. 相关进程是否在跑")
try:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    for line in out.splitlines():
        low = line.lower()
        if any(k in low for k in ("steam.exe", "launcher.exe", "games.exe",
                                  "endfield.exe", "acereport", "acebase")):
            print("  " + line)
except Exception as e:
    print("  tasklist 失败:", e)

head("4. Steam 日志（最近的启动相关）")
logs = STEAM / "logs"
if logs.is_dir():
    interesting = ["gameprocess_log.txt", "content_log.txt", "console_log.txt",
                   "bootstrap_log.txt", "connection_log.txt"]
    for name in interesting:
        p = logs / name
        if not p.is_file():
            continue
        mt = p.stat().st_mtime
        age = time.time() - mt
        if age > 3600 * 6:
            print(f"  [跳过] {name}  最后修改 {age / 3600:.1f} 小时前")
            continue
        print(f"\n  --- {name}  (最后修改 {time.strftime('%m-%d %H:%M:%S', time.localtime(mt))}) ---")
        txt = p.read_text(encoding="utf-8", errors="replace").splitlines()
        hits = [l for l in txt
                if any(k in l.lower() for k in ("4732690", "endfield", "app id 4732690"))]
        for l in hits[-20:]:
            print("    " + l[:200])
        if not hits:
            print("    （无 4732690/Endfield 相关行）")
else:
    print("!! 日志目录不存在")

head("5. ACF 现状")
acf = STEAM / "steamapps" / "appmanifest_4732690.acf"
if acf.is_file():
    print(f"  mtime={time.strftime('%m-%d %H:%M:%S', time.localtime(acf.stat().st_mtime))}")
    t = acf.read_text(encoding="utf-8", errors="replace")
    for line in t.splitlines():
        s = line.strip()
        if s.startswith('"'):
            print("   ", s[:110])
else:
    print("!! ACF 不存在")

head("6. 游戏侧日志（最近一次启动）")
sdk = Path.home() / "AppData" / "LocalLow" / "Hypergryph" / "Endfield" / "sdklogs"
for name in ("OneChannelLC.log", "u8sdk_pc.log"):
    p = sdk / name
    if not p.is_file():
        continue
    print(f"\n  --- {name}  (最后修改 {time.strftime('%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime))}) ---")
    for l in p.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]:
        print("    " + l[:200])