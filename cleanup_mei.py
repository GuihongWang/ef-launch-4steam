#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理本工具测试留下的 _MEI* 临时目录。

只删 TEMP 下名为 _MEI* 且「当前没有进程占用」的目录 —— 用法就是 try rmtree，
被占用会抛 PermissionError，自然跳过。

同时报告清理前后 TEMP 所在磁盘的可用空间。
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tmp = Path(tempfile.gettempdir())


def free_bytes() -> int:
    return shutil.disk_usage(tmp.anchor).free


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


before = free_bytes()
print(f"TEMP      : {tmp}")
print(f"清理前可用: {human(before)}")

# 只处理 24 小时内的残留（更早的可能是别的 PyInstaller 应用还需要的）
now = time.time()
freed = 0
skipped = 0
for p in sorted(tmp.glob("_MEI*")):
    if not p.is_dir():
        continue
    try:
        age_h = (now - p.stat().st_mtime) / 3600
    except OSError:
        continue
    if age_h > 24:
        print(f"  跳过（{age_h:.1f} 小时前，非本次测试）: {p.name}")
        continue
    sz = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    try:
        shutil.rmtree(p)
        freed += sz
        print(f"  删除 {p.name}  ({human(sz)}, {age_h:.1f} 小时前)")
    except OSError as e:
        skipped += 1
        print(f"  跳过 {p.name}（被占用）: {e.strerror}")

after = free_bytes()
print(f"\n清理掉    : {human(freed)}   跳过 {skipped} 个")
print(f"清理后可用: {human(after)}   (净增 {human(after - before)})")

left = list(tmp.glob("_MEI*"))
print(f"剩余 _MEI*: {len(left)} 个")