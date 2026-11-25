#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精确找出并结束本工具自己的残留进程（绝不误伤 steam.exe / Endfield.exe 等）。

用 Win32 API 按「进程可执行文件完整路径 == 本工具 exe 路径」匹配，
而不是按进程名子串匹配 —— 之前按名字 kill 有误伤风险。
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
TARGET_DIR = (HERE / "release").resolve()

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def exe_path(pid: int) -> str | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        # 新版 Windows 只在 kernel32 导出，psapi 那个可能不存在
        fn = getattr(kernel32, "QueryFullProcessImageNameW", None)
        if fn is None:
            fn = psapi.QueryFullProcessImageNameW
        if fn(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(h)


out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                     capture_output=True, text=True, errors="replace").stdout
pids = []
for line in out.splitlines():
    parts = [x.strip().strip('"') for x in line.split('","')]
    if len(parts) >= 2 and parts[1].isdigit():
        pids.append(int(parts[1]))
print(f"枚举到 {len(pids)} 个进程")

print(f"目标目录: {TARGET_DIR}\n")
print("=== 扫描本工具自己的残留进程 ===")
mine = []
for pid in set(pids):
    path = exe_path(pid)
    if not path:
        continue
    try:
        same = Path(path).resolve().parent == TARGET_DIR
    except OSError:
        continue
    if same:
        mine.append((pid, path))
        print(f"  pid={pid}  {path}")

if not mine:
    print("  无残留")
else:
    for pid, path in mine:
        print(f"\n结束 pid={pid} ...")
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, errors="replace")
        print("  " + (r.stdout or r.stderr).strip().replace("\n", "\n  "))

print("\n=== 确认 Steam / 游戏进程未被动过（只报告，不做任何操作）===")
out2 = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                      capture_output=True, text=True, errors="replace").stdout
names = set()
for line in out2.splitlines():
    parts = [x.strip().strip('"') for x in line.split('","')]
    if parts:
        names.add(parts[0].lower())
for want in ("steam.exe", "endfield.exe", "games.exe", "steam++.exe",
             "steamwebhelper.exe", "platformprocess.exe"):
    print(f"  {want:22s} {'运行中' if want in names else '未运行'}")