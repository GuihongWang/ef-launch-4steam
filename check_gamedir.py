#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查官服目录当前状态：stub 是否还在、备份是否完好。"""
import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GAME = Path(r"C:\Program Files\Hypergryph Launcher\games\Endfield Game")
BACKUP = Path(__file__).resolve().parent / "stub_backup"
REAL = BACKUP / "Endfield.exe.real"
STUB_MD5 = "cca2ebe25dfb94ce54e5b7db3ea072b5"
REAL_MD5 = "78e282a7431e38ebcd15e64c868c11be"


def md5(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


exe = GAME / "Endfield.exe"
print("=== 官服目录 ===")
print(f"  {exe}")
print(f"  is_file = {exe.is_file()}")
if exe.is_file():
    m = md5(exe)
    print(f"  size = {exe.stat().st_size:,}")
    print(f"  md5  = {m}")
    if m == REAL_MD5:
        print("  -> 真实游戏本体 ✓")
    elif m == STUB_MD5:
        print("  -> !! 是注入观测 stub，游戏当前无法正常启动")
    else:
        print("  -> ?? 既不是真实本体也不是已知 stub")

print()
print("=== stub 留下的东西 ===")
for name in ("stub_report.txt", "stub_report.json", "_internal",
             "Endfield.exe.real", "verify_files.json.orig"):
    p = GAME / name
    print(f"  {name:26s} {'存在' if p.exists() else '无'}"
          f"{'  ' + str(p.stat().st_size) + ' bytes' if p.is_file() else ''}")

print()
print("=== 备份目录 ===")
print(f"  {BACKUP}")
if BACKUP.is_dir():
    for p in sorted(BACKUP.iterdir()):
        print(f"    {p.name:28s} {p.stat().st_size:,}  md5={md5(p) if p.is_file() else '-'}")
print()
if REAL.is_file():
    print(f"  真实本体备份 md5 = {md5(REAL)}")
    print(f"  与预期(78e2...)一致: {md5(REAL) == REAL_MD5}")

print()
print("=== verify_files.json 当前记录 ===")
import json
vf = GAME / "verify_files.json"
try:
    data = json.loads(vf.read_text(encoding="utf-8"))
    for e in data.get("move", []):
        if isinstance(e, dict) and e.get("path") == "Endfield.exe":
            print(f"  md5={e.get('md5')}  size={e.get('size')}")
            if exe.is_file():
                ok = e.get("md5") == md5(exe)
                print(f"  与磁盘实际一致: {ok}")
except Exception as e:
    print(f"  读取失败: {e}")