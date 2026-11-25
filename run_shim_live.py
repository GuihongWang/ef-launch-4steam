#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直接运行真实部署的 shim exe，盯着 shim.log 和进程树看它到底干了什么。

shim 是 requireAdministrator，所以会弹 UAC —— 需要你在屏幕上点「是」。
本脚本不等待，只负责起它然后轮询观察。
"""
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield\Endfield.exe")
LOG = EXE.parent / "shim.log"

WATCH = ("steam.exe", "Endfield.exe", "Launcher.exe", "Games.exe")


def procs() -> dict[str, list[str]]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    d: dict[str, list[str]] = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            d.setdefault(parts[0].lower(), []).append(parts[1])
    return d


print(f"启动: {EXE}")
print(f"大小: {EXE.stat().st_size:,} bytes")
print()
print(">>> 屏幕上应该弹出 UAC，请点「是」 <<<")
print()

before = procs()
# 用 ShellExecuteEx 而不是 CreateProcess —— 后者对 requireAdministrator 的
# exe 直接 WinError 740，连 UAC 都不弹（刚踩过）。
# 这也是 shim 内部用来调启动器的同一个 API。
import ctypes

rc = ctypes.windll.shell32.ShellExecuteW(None, "open", str(EXE), None,
                                         str(EXE.parent), 1)
print(f"ShellExecuteW rc={rc}" + ("（成功）" if rc > 32 else "（失败）"))
print()

t0 = time.time()
seen_launcher = False
seen_games = False
seen_game = False
while time.time() - t0 < 75:
    time.sleep(3)
    p = procs()
    now = time.time() - t0

    if p.get("launcher.exe") and not seen_launcher:
        seen_launcher = True
        print(f"[{now:5.1f}s] *** Launcher.exe 起来了  pid={','.join(p['launcher.exe'])}")
    if p.get("games.exe") and not seen_games:
        seen_games = True
        print(f"[{now:5.1f}s] *** Games.exe 起来了    pid={','.join(p['games.exe'])}")
    if p.get("endfield.exe") and not seen_game:
        seen_game = True
        print(f"[{now:5.1f}s] *** Endfield.exe 起来了  pid={','.join(p['endfield.exe'])}")
    if LOG.is_file() and LOG.stat().st_mtime > t0:
        pass  # 日志在最后统一打印

print()
print("=" * 70)
print("shim.log 内容")
print("=" * 70)
if LOG.is_file():
    for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]:
        print("  " + l)
else:
    print("!! shim.log 没生成 —— exe 压根没跑起来（UAC 被拒？还是没点？）")

print()
print("=" * 70)
print("进程链结论")
print("=" * 70)
p = procs()
for name in WATCH:
    pids = p.get(name, [])
    print(f"  {name:16s} {'运行中 pid=' + ','.join(pids) if pids else '未运行'}")

chain_ok = bool(p.get("games.exe")) or bool(p.get("endfield.exe"))
print()
if chain_ok:
    print(">> 启动链路起来了。shim → Launcher → Games 这段已打通。")
else:
    print(">> 启动链路没起来。")
    if not seen_launcher:
        print("   Launcher.exe 从没出现过 —— shim 要么没执行，要么 ShellExecuteW 失败。")