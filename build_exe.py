#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 endfield_gui 为独立 exe。

默认 onefile（单文件，方便分发）。
C 盘空间紧张时用 --onedir：onefile 每次启动都要往 %TEMP% 解压一份
**实测 36,723,629 字节（35.0 MiB，992 个文件）**，
磁盘接近满时会直接报 "Failed to extract _tcl_data\\auto.tcl: decompression
resulted in return code -1!"，onedir 不解压到 TEMP，可绕过这个问题。
（这个数**不含** PYZ —— PYZ 不落盘，它直接从 exe 里读。）

**shim 构建产物会被打进包里**（--add-data）：
打包成 onefile 之后，冻结模块的 __file__ 指向 %TEMP% 下的解包目录，
那里**永远没有** shim_build\\dist\\Endfield —— 工具 GUI 里的「部署 shim」
按钮就会变成死路（让用户去敲命令行）。把整个 shim_build\\dist\\Endfield
目录作为附加数据塞进去，运行时 frozen 模式改指 sys._MEIPASS 下那份，
工具 GUI 就**自包含**了：拷到任何地方都能部署 shim。

**打进去的必须是 shim 的 onefile 形态**（一个 Endfield.exe，没有 _internal\\）：
shim 从 onedir 改成 onefile（2026-10-21）之后，如果还用
旧负载打包，用户在工具 GUI 里点一次「部署 shim」就会把 19 MB 的
_internal\\ 重新塞回 Steam 库，**把刚做好的 onefile 部署推翻**。
shim_data_args() 在打包前就会拦下这种负载（见那里）。

代价（2026-10-21 实测；同一台机器、同一个 PyInstaller 6.22.3 / Python 3.14.1）：

    负载形态                  内嵌条目数   负载解压后      负载压缩后   工具 exe 大小
    onedir（exe + 60 个 DLL）      61     21,295,553     9,517,639    22,355,421
    onefile（就一个 Endfield.exe）  1      9,682,707     9,490,500    22,322,787

> onedir 那一行是**历史记录** —— onedir 形态早就不用了，现在不会再构建它，
> 所以那两个数字**不会更新**（也**不需要**更新）。

> ⚠ **表里的字节数是某一次构建的快照** —— 每次重建都会变（源码里多一行
> 注释、PyInstaller 版本动一下，数字就跟着动）。**别拿它当不变量**。
> 真正稳定的判据有两个：
>
> 1. **内嵌条目数**（61 -> 1）—— 这才是「包里是哪种形态」的判据；
> 2. **相对不变量**：「工具 exe 里内嵌的负载 == `shim_build\\dist\\Endfield\\Endfield.exe`
>    == 部署目录里的 `Endfield.exe`」，三者**逐字节相同**。
>
> 三个绝对 md5 / 字节数**不要**写进任何断言里 —— 重建一次就得重写一遍，
> 忘了改就变成「假失败」。

两个负载**压缩后只差 27,139 字节** —— onedir 的 _internal\\ 全是 DLL，压得动；
onefile 的 shim 自身就是个压缩包，压不动。
所以**别拿 exe 的大小去判断包里的 shim 是什么形态**，要看 CArchive 的条目数
（61 -> 1）。

对照包的负载当年取自一个一次性对比构建（onedir 形态的旧构建）——
那个对比构建脚本已随 2026-10-21 的清理删除，**那份对照负载也不再存在**；
权威产物取自 shim_build\\dist\\Endfield（现行 onefile 构建）。

历史事实：R1 缺陷那版工具 exe 是 **22,338,134 字节、内嵌 61 条目**
（内嵌的是 21,286,274 字节的 onedir 构建）。形态一样，只是字节数略有出入。
> 这行是**历史记录**，不是目标值 —— 今天不用（也不该）再复现那个字节数。

**唯一权威位置是 release\\**：
PyInstaller 直接把产物输出到 release\\（--distpath），不再留 dist\\ 那份拷贝。
两处并存就会出现「拷贝没跟上、部署的是旧货」这种事 —— 本项目的 R1 缺陷
（工具 exe 里内嵌的是过时的 onedir 负载）就是这么来的。

用法：
    python build_exe.py              # onefile -> release/Steam终末地更新器.exe
    python build_exe.py --onedir     # onedir  -> release/Steam终末地更新器/Steam终末地更新器.exe
    python build_exe.py --dist D:\\tmp\\out     # -> D:\\tmp\\out\\release\\...
    python build_exe.py --no-embed-shim   # 不把 shim 打进包里（会失去「部署 shim」能力）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv-build"
PY = VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
EXE_NAME = "Steam终末地更新器"

# 要打进工具 exe 的 shim 构建产物（onefile：一个 Endfield.exe，没有 _internal\）。
# 目标路径必须与 endfield_updater.SHIM_SRC_REL 一致：
# frozen 模式下 SHIM_SRC_DIR = sys._MEIPASS / <这个相对路径>。
SHIM_DIST = HERE / "shim_build" / "dist" / "Endfield"
SHIM_DATA_DEST = "shim_build/dist/Endfield"
SHIM_EXE_NAME = "Endfield.exe"
# shim 的 _internal\ 目录名（onedir 形态才会出现）。
SHIM_INTERNAL_NAME = "_internal"


def run(cmd: list[str], **kw) -> None:
    print(">>", " ".join(str(c) for c in cmd))
    subprocess.check_call([str(c) for c in cmd], **kw)


def _has_pyinstaller(python: Path) -> str | None:
    """返回已安装的 PyInstaller 版本，没装则返回 None。"""
    r = subprocess.run(
        [str(python), "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True, text=True, errors="replace",
    )
    return r.stdout.strip() if r.returncode == 0 else None


def ensure_venv(retries: int = 3) -> Path:
    """
    幂等地准备构建环境，返回用于打包的 python 可执行文件。

    优先复用系统 Python 里已装的 PyInstaller —— 省掉建 venv + 下载依赖，
    也避开镜像源下载中断（pip 报 IncompleteRead 直接让整个构建挂掉）。
    只有系统没装时才退回 .venv-build 自行安装。

    返回值是一个可调用对象，直接替换原来的 PY 全局。
    """
    global PY

    ver = _has_pyinstaller(sys.executable)
    if ver:
        print(f"[i] 复用系统 Python 的 PyInstaller v{ver}: {sys.executable}")
        return sys.executable

    if not PY.exists():
        print(f"[i] 创建虚拟环境 {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])

    for attempt in range(1, retries + 1):
        ver = _has_pyinstaller(PY)
        if ver:
            print(f"[i] 虚拟环境 PyInstaller v{ver} 就绪: {PY}")
            return PY
        print(f"[i] 虚拟环境未装 PyInstaller，安装中（第 {attempt}/{retries} 次）")
        try:
            run([str(PY), "-m", "pip", "install", "pyinstaller"])
        except subprocess.CalledProcessError as e:
            print(f"[!] 第 {attempt}/{retries} 次失败: {e}")
            if attempt < retries:
                print("    等待 5 秒后重试…")
                time.sleep(5)

    raise SystemExit(
        "[!] 无法安装 PyInstaller。二选一：\n"
        f'    1) 系统 Python 装一个:  "{sys.executable}" -m pip install pyinstaller\n'
        f'    2) 换镜像源后重试:    "{PY}" -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple'
    )


def check_temp_space(required_mb: int = 120) -> None:
    """onefile 需要在 TEMP 里解压一份；空间不够就提前警告，别等构建/运行时才炸。"""
    import tempfile

    tmp = Path(tempfile.gettempdir())
    try:
        usage = shutil.disk_usage(tmp)
    except OSError as e:
        print(f"[!] 读不到 TEMP 空间: {e}")
        return
    free_mb = usage.free / 1024 ** 2
    print(f"[i] TEMP = {tmp}")
    print(f"[i] TEMP 所在磁盘可用 = {free_mb:,.0f} MB "
          f"(共 {usage.total / 1024 ** 3:.1f} GB, 已用 {usage.used / usage.total * 100:.1f}%)")
    if free_mb < required_mb:
        print(f"[!] 可用空间不足 {required_mb} MB。onefile 运行时仍可能解压失败，")
        print("    建议改用:  python build_exe.py --onedir")


def shim_data_args(embed_shim: bool) -> list[str]:
    """--add-data 参数：把 shim 构建产物整个目录打进工具 exe。

    打包后的工具 GUI 里，「部署 shim」按钮靠的就是这份数据 ——
    不打进去，那个按钮在 onefile 下就是死路（见模块 docstring）。

    **只接受 onefile 形态的 shim**（一个 Endfield.exe，没有 _internal\\）：
    shim 早就从 onedir 改成 onefile 了。还在打 onedir 负载的话，用户在
    工具 GUI 里点一次「部署 shim」，就会把 19 MB 的 _internal\\ 重新塞回
    Steam 库，把刚做好的 onefile 部署**推翻**（R1 缺陷）。
    """
    if not embed_shim:
        print("[!] --no-embed-shim：工具 exe 里**不含** shim，"
              "「部署 shim」按钮将不可用")
        return []
    if not (SHIM_DIST / SHIM_EXE_NAME).is_file():
        print("[!] 找不到 shim 构建产物: " + str(SHIM_DIST / SHIM_EXE_NAME))
        print("    先构建: python build_shim.py")
        print("    （不构建也能打包，但工具里的「部署 shim」按钮会报错）")
        return []
    # --- 结构性防线：负载里不许出现 _internal\ 的任何文件 ---
    internal = SHIM_DIST / SHIM_INTERNAL_NAME
    bad = []
    if internal.is_dir():
        bad = [p for p in internal.rglob("*") if p.is_file()]
    if bad:
        print("[!] shim 构建产物还是 **onedir** 形态：")
        print(f"    {internal} 里有 {len(bad)} 个文件"
              f"（共 {sum(p.stat().st_size for p in bad):,} 字节）")
        print("    把它打进工具 exe = 用户点一次「部署 shim」就把这 "
              f"{sum(p.stat().st_size for p in bad) / 1024 / 1024:.1f} MB "
              "重新塞回 Steam 库，")
        print("    **把刚做好的 onefile 部署推翻**。")
        print("    重新构建成 onefile: python build_shim.py")
        raise SystemExit("[!] 拒绝打包 onedir 形态的 shim 负载。")
    files = [p for p in SHIM_DIST.rglob("*") if p.is_file()]
    print(f"[i] 把 shim 构建产物打进包里: {SHIM_DIST}  ({len(files)} 个文件)")
    for p in files:
        print(f"    {p.relative_to(SHIM_DIST)}  {p.stat().st_size:,} 字节")
    print(f"    -> 运行时解包到 sys._MEIPASS/{SHIM_DATA_DEST}")
    return ["--add-data", f"{SHIM_DIST}{os.pathsep}{SHIM_DATA_DEST}"]


def build(onedir: bool, dist_root: Path, python: Path, embed_shim: bool = True) -> Path:
    # **唯一权威位置 = release\\**。PyInstaller 直接输出到那儿，
    # 不再留 dist\\ 那份拷贝（两份并存迟早会有一份是旧的）。
    dist = Path(dist_root) / "release"
    work = Path(dist_root) / "build"
    mode = [] if onedir else ["--onefile"]
    dist.mkdir(parents=True, exist_ok=True)
    check_temp_space()

    run([
        str(python), "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        *mode,
        "--windowed",
        "--name", EXE_NAME,
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(Path(dist_root)),
        "--paths", str(Path(dist_root)),
        # tkinter 由 PyInstaller 自动收集；显式加上更稳
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.messagebox",
        *shim_data_args(embed_shim),
        str(Path(dist_root) / "endfield_gui.py"),
    ])

    exe = dist / (f"{EXE_NAME}.exe" if not onedir else f"{EXE_NAME}/{EXE_NAME}.exe")
    if not exe.exists():
        print(f"[!] 没找到预期产物: {exe}")
        print(f"    产物目录内容: {[p.name for p in dist.iterdir()] if dist.exists() else '无'}")
        return exe
    print(f"\n[OK] 输出: {exe}  ({exe.stat().st_size:,} bytes)")
    return exe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="打包 Steam 终末地更新器")
    p.add_argument("--onedir", action="store_true",
                   help="生成目录版而非单文件（不占 TEMP 空间，适合 C 盘紧张）")
    p.add_argument("--dist", default=str(HERE), help="构建根目录（默认当前目录）")
    p.add_argument("--python", help="指定用于打包的 python（默认自动挑）")
    p.add_argument("--no-embed-shim", action="store_true",
                   help="不把 shim_build\\dist\\Endfield 打进包里"
                        "（打进去工具才自包含，「部署 shim」按钮才可用）")
    args = p.parse_args(argv)

    if args.python:
        python = Path(args.python)
    else:
        python = ensure_venv()
    build(args.onedir, Path(args.dist), python, embed_shim=not args.no_embed_shim)

    # 产物**直接**落在 release\ 里（build() 的 --distpath 就是它），
    # 这里不再往别处拷第二份 —— 避免「两份 exe，有一份是旧的」。
    release = Path(args.dist) / "release"
    src = release / (EXE_NAME + (".exe" if not args.onedir else f"/{EXE_NAME}.exe"))
    if src.exists():
        print(f"[OK] 权威产物: {src}  ({src.stat().st_size:,} bytes)")
    else:
        print(f"[!] release\\ 下没有产物: {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
