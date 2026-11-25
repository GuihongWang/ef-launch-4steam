#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署产物体检 —— 替代 2026-10-22 删掉的那些测试套件里最有用的两条断言。

查三件事：

  1. **三者逐字节相同**：
         构建源  shim_build\\dist\\Endfield\\Endfield.exe
       == 部署品  <Steam库>\\Arknights Endfield\\Endfield.exe
       == 工具内嵌负载  release\\Steam终末地更新器.exe 的 CArchive 里那份
     这是**相对不变量**（不是「等于某个常量」）—— 重建一次绝对 md5 就变，
     但这三者必须始终一样。

  2. **内嵌负载是 onefile**：CArchive 里 `shim_build/dist/Endfield/` 下
     **恰好 1 个条目**、就是 `Endfield.exe`、**不含任何 `_internal` 条目**。
     这是 R1 的回归检查：负载一旦退回 onedir，用户在工具 GUI 里点一次
     「部署 shim」就会把 19 MB 的 `_internal\\` 塞回 Steam 库，
     **把刚做好的 onefile 部署推翻**。

  3. **工具 exe 不比源码旧**：改了 `endfield_gui.py` / `endfield_updater.py`
     却忘了重跑 `build_exe.py`，用户双击 shim 拿到的还是旧版工具。
     （2026-10-21 就这么漏掉过一个真缺陷：exe 里的 `endfield_updater`
     还是旧的，用户被叫去跑一个不存在的脚本。）

顺带清掉上一轮失败留下的 shim.log。

用法：
    python -X utf8 zz_final.py
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SD = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Arknights Endfield")
SRC = HERE / "shim_build" / "dist" / "Endfield" / "Endfield.exe"
TOOL = HERE / "release" / "Steam终末地更新器.exe"
# 工具 exe 里内嵌的负载在 CArchive 里的路径前缀
PAYLOAD_PREFIX = "shim_build/dist/Endfield/"


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def embedded_payload(tool: Path) -> tuple[bytes | None, list[str]]:
    """从工具 exe 的 CArchive 里挖出内嵌的 shim 负载。

    返回 (Endfield.exe 的字节 or None, 负载目录下的全部条目名)。
    """
    try:
        blob = tool.read_bytes()
    except OSError:
        return None, []
    cookie = b"MEI\x0c\x0b\x0a\x0b\x0e"
    pos = blob.rfind(cookie)
    if pos < 0:
        return None, []
    names: list[str] = []
    data: bytes | None = None
    try:
        (_m, length, toc_off, toc_len, _py) = struct.unpack(
            "!8sIIII", blob[pos:pos + 24])
        pkg = pos + 88 - length
        p = pkg + toc_off
        end = p + toc_len
        while p < end:
            (elen, off, clen, ulen, flag, typ) = struct.unpack(
                "!IIIIBc", blob[p:p + 18])
            nm = blob[p + 18:p + elen].split(b"\0")[0].decode("utf-8", "replace")
            rel = nm.replace("\\", "/")
            if rel.startswith(PAYLOAD_PREFIX):
                names.append(rel)
                if rel == PAYLOAD_PREFIX + "Endfield.exe":
                    raw = blob[pkg + off:pkg + off + clen]
                    if flag == 1:
                        try:
                            raw = zlib.decompress(raw)
                        except zlib.error:
                            pass
                    data = raw
            p += elen
    except Exception:                                    # noqa: BLE001
        pass
    return data, names


def main() -> int:
    rc = 0
    print("=" * 68)
    print("部署产物体检")
    print("=" * 68)

    # --- 1) 三者逐字节相同 ---
    deployed = SD / "Endfield.exe"
    emb, names = embedded_payload(TOOL)
    rows = []
    if SRC.is_file():
        rows.append(("构建源        shim_build\\dist\\Endfield\\Endfield.exe",
                     md5_file(SRC), SRC.stat().st_size))
    else:
        print(f"[!] 找不到构建源: {SRC}\n    先跑: python build_shim.py")
        rc = 1
    if deployed.is_file():
        rows.append(("部署品        <Steam库>\\Arknights Endfield\\Endfield.exe",
                     md5_file(deployed), deployed.stat().st_size))
    else:
        print(f"[!] 找不到部署品: {deployed}\n    先跑: python endfield_updater.py shim install")
        rc = 1
    if emb is not None:
        rows.append(("工具内嵌负载  release\\Steam终末地更新器.exe (CArchive)",
                     md5_bytes(emb), len(emb)))
    else:
        print(f"[!] 工具 exe 里挖不出内嵌负载: {TOOL}\n    先跑: python build_exe.py")
        rc = 1

    if rows:
        print("\n-- 三者逐字节核对 --")
        for name, h, n in rows:
            print(f"  {name}\n      {n:>12,} bytes   md5 {h}")
        same = len({h for _, h, _ in rows}) == 1 and len(rows) == 3
        print(f"\n  三者逐字节相同: {'YES ✓' if same else 'NO ✗'}")
        if not same:
            rc = 1

    # --- 2) 内嵌负载是 onefile（R1 回归检查）---
    print("\n-- 内嵌负载形态（R1 回归检查）--")
    onefile = (names == [PAYLOAD_PREFIX + "Endfield.exe"])
    print(f"  条目数 = {len(names)}  {names}")
    print(f"  内嵌负载是 onefile（恰好 1 个条目、没有 _internal）: "
          f"{'YES ✓' if onefile else 'NO ✗'}")
    if not onefile:
        rc = 1

    # --- 3) 工具 exe 不比源码旧 ---
    print("\n-- 工具 exe 新鲜度 --")
    fresh = True
    if TOOL.is_file():
        t_mtime = TOOL.stat().st_mtime
        for src_name in ("endfield_gui.py", "endfield_updater.py",
                         "shim/endfield_shim.py"):
            sp = HERE / src_name
            if not sp.is_file():
                continue
            older = sp.stat().st_mtime > t_mtime
            print(f"  {src_name:26s} mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sp.stat().st_mtime))}"
                  f"  {'比工具 exe 新 ✗ 忘了重建' if older else 'OK ✓'}")
            if older:
                fresh = False
        print(f"  工具 exe 比源码新: {'YES ✓' if fresh else 'NO ✗'}")
        if not fresh:
            print("    修: python -X utf8 build_shim.py && python -X utf8 build_exe.py")
            rc = 1
    else:
        print(f"  [!] 找不到工具 exe: {TOOL}")
        rc = 1

    # --- 附带：清掉上一轮失败留下的 shim.log ---
    log = SD / "shim.log"
    if log.exists():
        mt = time.strftime('%H:%M:%S', time.localtime(log.stat().st_mtime))
        try:
            log.unlink()
            print(f"\n已清掉旧的 shim.log（{mt} 那份）")
        except OSError as e:
            print(f"\n[!] 清不掉 shim.log: {e}")

    print("\n--- Steam 侧最终状态 ---")
    if SD.is_dir():
        for p in sorted(SD.iterdir()):
            if p.is_dir():
                n = sum(1 for _ in p.rglob("*"))
                print(f"   [d] {p.name}  {n} 项")
            else:
                print(f"   [f] {p.name}  {p.stat().st_size:,}")
    else:
        print(f"   [!] 部署目录不存在: {SD}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
