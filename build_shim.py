#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 Steam 侧 shim，产出名必须是 Endfield.exe（Steam 按这个名字拉起）。

**默认 onefile（单文件）**：产物只有一个 Endfield.exe，拷到任何地方都能用。
onedir 保留（``--onedir``），但**不再是默认**。

为什么改成 onefile：
  onedir 的 Endfield.exe 必须和同级的 ``_internal\\``（60 个文件 / 19 MB）
  一起在 Steam 库里。用户把 exe 单独拷走 -> 必然报
  ``Failed to load Python DLL 'E:\\_internal\\python314.dll'``（踩过一次）。

onefile 的代价：每次启动往 %TEMP%\\_MEIxxxx 解包 ~19 MB。shim 在挂起父进程
模式下会活到游戏退出，用户点 Steam「停止」是硬杀 -> PyInstaller 来不及清理
-> 每停一次漏 ~19 MB（本机历史上漏过 2.16 GB）。
对策：shim 启动早期做**保守**的陈旧 _MEI* 清理（见 shim/endfield_shim.py
里的 purge_stale_mei）。两者是一套，缺一不可。

两种形态的产物路径**故意保持一致**：``shim_build\\dist\\Endfield\\Endfield.exe``
（onefile 时 ``shim_build\\dist\\Endfield.exe`` 会被挪进这个目录）。
这样 ``endfield_updater.SHIM_SRC_REL`` 和 ``build_exe.py`` 的 --add-data
都不用改，打包工具也照样能把 shim 塞进自己肚子里。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv-build"
PY = VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")

SRC = HERE / "shim" / "endfield_shim.py"
MANIFEST = HERE / "shim" / "shim.manifest"
OUT_NAME = "Endfield"
DIST = HERE / "shim_build"
# 产物统一放在这个目录里（两种形态都一样）
OUT_DIR = DIST / "dist" / OUT_NAME


def run(cmd) -> None:
    print(">>", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd])


def ensure_pyinstaller() -> str:
    r = subprocess.run([sys.executable, "-c",
                        "import PyInstaller;print(PyInstaller.__version__)"],
                       capture_output=True, text=True, errors="replace")
    if r.returncode == 0:
        print(f"[i] 用系统 Python 的 PyInstaller v{r.stdout.strip()}")
        return sys.executable
    if not PY.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    run([str(PY), "-m", "pip", "install", "pyinstaller"])
    return str(PY)


def normalize_output(onefile: bool) -> Path:
    """把产物挪成统一路径 ``shim_build/dist/Endfield/Endfield.exe``。

    onedir 时 PyInstaller 直接产出到那个位置；onefile 时产出在
    ``shim_build/dist/Endfield.exe``，这里挪进去。

    **关键**：切到 onefile 时必须把 onedir 遗留的 ``_internal\\`` 删掉。
    留着它会让部署工具以为「这是 onedir 源码」，于是把 19 MB 的依赖
    又拷一遍到 Steam 库里 —— 正好是要消灭的东西。
    """
    final = OUT_DIR / f"{OUT_NAME}.exe"
    if onefile:
        produced = DIST / "dist" / f"{OUT_NAME}.exe"
        if produced.is_file() and produced.resolve() != final.resolve():
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            if final.exists():
                final.unlink()
            shutil.move(str(produced), str(final))
        # onedir 遗留：_internal\ 和多出来的文件一律清掉
        if OUT_DIR.is_dir():
            for item in sorted(OUT_DIR.iterdir()):
                if item.name == f"{OUT_NAME}.exe":
                    continue
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    print(f"[i] 清掉 onedir 遗留: {item.name}")
                except OSError as e:
                    print(f"[!] 清不掉 {item}: {e}")
    return final


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="构建 Endfield shim")
    p.add_argument("--onefile", action="store_true",
                   help="打成单文件（**默认**，可省略）")
    p.add_argument("--onedir", action="store_true",
                   help="打成目录版（exe + _internal\\），测试/对照用")
    p.add_argument("--clean", action="store_true", default=True)
    args = p.parse_args(argv)

    if args.onedir and args.onefile:
        print("[!] --onefile 和 --onedir 不能同时给")
        return 1
    onefile = not args.onedir          # **默认 onefile**

    for f in (SRC, MANIFEST):
        if not f.exists():
            print(f"[!] 缺少 {f}")
            return 1

    py = ensure_pyinstaller()

    mode = ["--onefile"] if onefile else []
    print(f"[i] 打包形态: {'onefile（单文件，默认）' if onefile else 'onedir（目录版）'}")
    run([
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        *mode,
        "--windowed",
        "--name", OUT_NAME,
        "--manifest", str(MANIFEST),
        # 必须加：PyInstaller 在没开这个开关时会往 manifest 里塞 asInvoker，
        # 把我们自己的 requireAdministrator 覆盖掉（实测踩过）。
        "--uac-admin",
        "--distpath", str(DIST / "dist"),
        "--workpath", str(DIST / "build"),
        "--specpath", str(DIST),
        "--paths", str(HERE),
        str(SRC),
    ])

    exe = normalize_output(onefile)
    if not exe.exists():
        print(f"[!] 没找到产物: {exe}")
        if exe.parent.exists():
            print("   dist 内容:", [x.name for x in exe.parent.iterdir()])
        return 1

    size = exe.stat().st_size
    print(f"\n[OK] {exe}  ({size:,} 字节)")
    print(f"     形态: {'onefile（自包含，拷走也能用）' if onefile else 'onedir'}")

    if not onefile:
        internal = OUT_DIR / "_internal"
        n = sum(1 for _ in internal.rglob("*") if _.is_file()) if internal.is_dir() else 0
        b = sum(x.stat().st_size for x in internal.rglob("*") if x.is_file()) \
            if internal.is_dir() else 0
        print(f"     _internal\\: {n} 个文件 / {b:,} 字节")

    # --- 验证 manifest 真的打进去了 ---------------------------------
    # onefile 下：requireAdministrator 写在**引导进程**的 manifest 里，
    # 也就是顶层那个 exe 的 PE 资源里。顶层 exe 里同时还嵌着子进程的
    # manifest（PyInstaller 把主脚本的 exe 也塞进包里），所以直接搜
    # 整个文件的字节流即可 —— 两个 manifest 都在里面。
    blob = exe.read_bytes()
    want = b'requestedExecutionLevel level="requireAdministrator"'
    found = want in blob
    print(f"     manifest requireAdministrator: {'已找到 ✓' if found else '**未找到** ✗'}")
    for m in re.finditer(rb"<requestedExecutionLevel[^>]*>", blob):
        print("     " + m.group().decode("utf-8", "replace"))
    if not found:
        print("[!] manifest 里没有 requireAdministrator —— 整条链会在 "
              "Endfield.exe 那步 WinError 740。")
        return 1

    print(f"\n部署目录: {OUT_DIR}")
    print("下一步: python endfield_updater.py shim install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
