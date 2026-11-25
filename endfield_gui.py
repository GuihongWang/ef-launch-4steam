#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Steam 终末地更新器 — GUI。"""

from __future__ import annotations

import io
import queue
import sys
import threading
import traceback
from pathlib import Path

# 允许打包后找到同目录的 core
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).parent
else:
    BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import tkinter as tk
from tkinter import messagebox, ttk

import endfield_updater as core


APP_TITLE = "Steam 终末地更新器"
APP_VER = "1.0.0"


def _ns(**kw) -> object:
    """造一个带默认值的 Namespace，省得每处都列全字段。"""
    defaults = dict(
        manifest=None,
        buildid=None,
        depot_size=None,
        from_steamdb=None,
        last_updated=None,
        auto_update_behavior=1,
        use_target_as_build=False,
        force_backup=False,
        game_dir=None,
        recreate_acf=False,
        force_junction=False,
        launch_options=None,
        action=None,
        force=False,
        start=None,
        json=False,
    )
    defaults.update(kw)
    return type("NS", (), defaults)()


def shortcut_trap_warning_text(traps: list) -> str:
    """生成「非 Steam 快捷方式陷阱」的警告文案（**纯函数**，不碰 Tk）。

    抽成纯函数是为了能**不开窗口**就测这段文案 —— 用户实际看到的就是它。
    """
    if not traps:
        return ""
    lines = ["⚠ 检测到 %d 个「非 Steam 快捷方式」会绕过 shim：" % len(traps)]
    for t in traps:
        lines.append("")
        lines.append("   快捷方式: 「%s」   AppID: %s"
                     % (t.get("name", "?"), t.get("appid", "?")))
        lines.append("   指向:     %s" % (t.get("exe_path") or t.get("exe", "?")))
        if t.get("launch_options"):
            lines.append("   启动参数: %s" % t["launch_options"])
        lines.append("   点它会【完全绕过 shim】：父进程不是 Games.exe，")
        lines.append("   U8 SDK 报 errorCode 10100，游戏自己走深链重起第二实例，")
        lines.append("   【会弹出启动器界面】。")
    lines.append("")
    lines.append("   ✅ 请从 Steam 库里的「Arknights: Endfield」启动 —— 那才是我们的 shim。")
    return "\n".join(lines)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VER}")
        self.geometry("660x760")
        self.minsize(580, 640)
        self._build_style()
        self._build_ui()
        self._capture_stdout()
        self._targets: list[dict] = []
        # worker 线程一律通过 Queue 回主线程更新 UI。
        # 原因：tkinter 的 after() 跨线程调用要求主线程正处于 mainloop()，
        # 否则抛 RuntimeError("main thread is not in main loop")。
        # 一旦 worker 里抛了，run_async 的 except 又会调 log() -> 再抛 ->
        # set_busy(False) 永远执行不到，界面直接卡死、所有按钮变灰。
        self._ui_q: queue.Queue = queue.Queue()
        self._ui_alive = True
        self.after(40, self._drain_ui)
        self.after(80, self.refresh_status)
        self.after(250, self.on_detect)

    # ---- 跨线程 UI 调度 ----
    def post_ui(self, fn) -> None:
        """任意线程可调用：把 UI 更新排到主线程执行。"""
        try:
            self._ui_q.put_nowait(fn)
        except Exception:
            pass

    def _drain_ui(self) -> None:
        """主线程轮询队列（在 __init__ 里由主线程 after 启动，安全）。"""
        while True:
            try:
                fn = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                # UI 回调本身出错不能拖垮轮询，否则后续更新全丢
                pass
        if self._ui_alive:
            try:
                self.after(40, self._drain_ui)
            except tk.TclError:
                self._ui_alive = False

    def _capture_stdout(self) -> None:
        """把 core 的 print 接到日志框（windowed exe 没有控制台）。"""
        app = self

        class _Tee(io.TextIOBase):
            def __init__(self, orig: io.TextIOBase | None) -> None:
                self._orig = orig

            def write(self, s: str) -> int:
                if s and s.strip():
                    app.log(s)
                if self._orig is not None:
                    try:
                        self._orig.write(s)
                    except Exception:
                        pass
                return len(s)

            def flush(self) -> None:
                if self._orig is not None:
                    try:
                        self._orig.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee(sys.stdout)
        sys.stderr = _Tee(sys.stderr)

    # ---- UI ----
    def _build_style(self) -> None:
        style = ttk.Style(self)
        if sys.platform == "win32":
            try:
                style.theme_use("vista")
            except tk.TclError:
                pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("H2.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Mono.TLabel", font=("Consolas", 9))
        style.configure("Ok.TLabel", foreground="#0a7a32")
        style.configure("Bad.TLabel", foreground="#b00020")
        style.configure("Warn.TLabel", foreground="#9a6700")
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=10)

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x", **pad)
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text=(
                f"{core.GAME_DISPLAY}（App {core.APP_ID} / Depot {core.DEPOT_ID}）\n"
                "自动写入 InstalledDepots.manifest / buildid，"
                "阻止 Steam 官方版本覆盖 Hypergryph 启动器下载的文件"
            ),
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        # 状态区
        status_frame = ttk.LabelFrame(root, text="当前状态", padding=10)
        status_frame.pack(fill="x", **pad)

        self.var_acf = tk.StringVar(value="ACF: …")
        self.var_depots = tk.StringVar(value="InstalledDepots: …")
        self.var_dir = tk.StringVar(value="Steam 安装目录: …")
        self.var_official = tk.StringVar(value="官服目录: …")
        self.var_link = tk.StringVar(value="目录联接: …")
        self.var_info = tk.StringVar(value="appinfo: …")
        self.var_sync = tk.StringVar(value="检查中…")

        ttk.Label(status_frame, textvariable=self.var_acf, style="Mono.TLabel").pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.var_depots, style="Mono.TLabel").pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.var_dir, style="Mono.TLabel").pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.var_official, style="Mono.TLabel").pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.var_link, style="Mono.TLabel").pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.var_info, style="Mono.TLabel").pack(anchor="w")
        self.lbl_sync = ttk.Label(status_frame, textvariable=self.var_sync, style="H2.TLabel")
        self.lbl_sync.pack(anchor="w", pady=(8, 0))

        # 非 Steam 快捷方式陷阱警告（**只读检测**，检测不到就不显示）
        self.lbl_trap = tk.Label(
            status_frame,
            text="",
            justify="left",
            anchor="w",
            bg="#fff3cd",
            fg="#7a4b00",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            wraplength=600,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        # 默认不显示（pack_forget）；有陷阱时才 pack 出来。

        # 操作区
        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill="x", **pad)

        self.btn_apply = ttk.Button(
            btn_frame, text="一键伪装（阻止更新）", style="Big.TButton", command=self.on_apply
        )
        self.btn_apply.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_restore = ttk.Button(btn_frame, text="恢复备份", command=self.on_restore)
        self.btn_restore.pack(side="left", padx=3)

        self.btn_refresh = ttk.Button(btn_frame, text="刷新", command=self.refresh_status)
        self.btn_refresh.pack(side="left", padx=3)

        # 部署区
        link_frame = ttk.LabelFrame(
            root, text="官服 → Steam：部署 shim（启动器接管）", padding=10
        )
        link_frame.pack(fill="x", **pad)

        row_auto = ttk.Frame(link_frame)
        row_auto.pack(fill="x", pady=2)
        self.btn_detect = ttk.Button(row_auto, text="自动检测 Steam 库", command=self.on_detect)
        self.btn_detect.pack(side="left", padx=(0, 6))
        self.btn_findexe = ttk.Button(
            row_auto, text=f"查找 {core.GAME_EXE_NAME}…", command=self.on_find_exe
        )
        self.btn_findexe.pack(side="left", padx=3)

        row_g = ttk.Frame(link_frame)
        row_g.pack(fill="x", pady=2)
        ttk.Label(row_g, text="官服目录", width=10).pack(side="left")
        self.ent_gamedir = ttk.Entry(row_g)
        self.ent_gamedir.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.ent_gamedir.insert(0, core.OFFICIAL_DIR_HINTS[0])
        ttk.Button(row_g, text="浏览…", command=self.on_browse_gamedir).pack(side="left")

        row_t = ttk.Frame(link_frame)
        row_t.pack(fill="x", pady=2)
        ttk.Label(row_t, text="Steam库", width=10).pack(side="left")
        self.cmb_targets = ttk.Combobox(row_t, state="readonly", width=34)
        self.cmb_targets.pack(side="left", padx=(0, 4))
        self.cmb_targets.bind("<<ComboboxSelected>>", self.on_target_selected)
        ttk.Button(row_t, text="自动", width=6, command=self.on_auto_library).pack(side="left", padx=2)
        ttk.Button(row_t, text="刷新", width=6, command=self.on_detect).pack(side="left", padx=2)

        row_l = ttk.Frame(link_frame)
        row_l.pack(fill="x", pady=4)
        self.btn_shim = ttk.Button(row_l, text="部署 shim", command=self.on_install_shim)
        self.btn_shim.pack(side="left", padx=(0, 6))
        self.btn_unshim = ttk.Button(row_l, text="移除 shim", command=self.on_remove_shim)
        self.btn_unshim.pack(side="left", padx=3)
        self.btn_link = ttk.Button(row_l, text="目录联接（已退役）",
                                   command=self.on_make_link)
        self.btn_link.pack(side="left", padx=3)
        self.btn_unlink = ttk.Button(row_l, text="拆掉遗留联接",
                                     command=self.on_del_link)
        self.btn_unlink.pack(side="left", padx=3)
        self.btn_convert = ttk.Button(
            row_l, text="一键完整转换", style="Big.TButton", command=self.on_convert
        )
        self.btn_convert.pack(side="left", padx=3)

        row_dx = ttk.Frame(link_frame)
        row_dx.pack(fill="x", pady=2)
        ttk.Label(row_dx, text="启动项", width=10).pack(side="left")
        self.ent_dx = ttk.Entry(row_dx, width=14)
        self.ent_dx.pack(side="left")
        self.ent_dx.insert(0, core.LAUNCH_DX11)
        ttk.Button(row_dx, text="记录 DX 启动项", command=self.on_launch_opts).pack(side="left", padx=6)
        ttk.Label(
            row_dx,
            text=f"（留空 = DX12。Steam 属性里也能手动填 {core.LAUNCH_DX11}）",
            wraplength=380,
        ).pack(side="left")

        # 手动覆盖
        man_frame = ttk.LabelFrame(root, text="手动覆盖（可选，一般不用）", padding=10)
        man_frame.pack(fill="x", **pad)

        row1 = ttk.Frame(man_frame)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="manifest", width=10).pack(side="left")
        self.ent_manifest = ttk.Entry(row1)
        self.ent_manifest.pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(man_frame)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="buildid", width=10).pack(side="left")
        self.ent_buildid = ttk.Entry(row2)
        self.ent_buildid.pack(side="left", fill="x", expand=True)

        row3 = ttk.Frame(man_frame)
        row3.pack(fill="x", pady=2)
        ttk.Label(row3, text="SteamDB粘贴", width=10).pack(side="left")
        self.ent_steamdb = ttk.Entry(row3)
        self.ent_steamdb.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row3, text="解析并填入", command=self.on_parse_steamdb).pack(side="left")

        # 日志
        log_frame = ttk.LabelFrame(root, text="日志", padding=4)
        log_frame.pack(fill="both", expand=True, **pad)

        self.txt_log = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Consolas", 9),
            relief="flat",
        )
        self.txt_log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.txt_log.yview)
        scroll.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=scroll.set)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- helpers ----
    def log(self, msg: str) -> None:
        def _append() -> None:
            self.txt_log.insert("end", msg.rstrip() + "\n")
            self.txt_log.see("end")

        self.post_ui(_append)

    def show_shortcut_traps(self, traps: list) -> None:
        """在状态区显示（或隐藏）非 Steam 快捷方式陷阱警告。

        **只读**：这里只把检测结果画出来，**绝不**去改 Steam 的任何配置。
        检测不到就什么都不显示（也不报错）。
        """
        if not traps:
            self.lbl_trap.pack_forget()
            return
        self.lbl_trap.configure(text=shortcut_trap_warning_text(traps))
        self.lbl_trap.pack(fill="x", pady=(8, 0), before=self.lbl_sync)

    def set_sync(self, text: str, ok: bool | None) -> None:
        self.var_sync.set(text)
        style = "Ok.TLabel" if ok is True else "Bad.TLabel" if ok is False else "H2.TLabel"
        self.lbl_sync.configure(style=style)

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for b in (
            self.btn_apply, self.btn_restore, self.btn_refresh,
            self.btn_shim, self.btn_unshim,
            self.btn_link, self.btn_unlink, self.btn_convert,
            self.btn_detect, self.btn_findexe,
        ):
            b.configure(state=state)

    def collect_manual(self) -> dict:
        out: dict = {}
        m = self.ent_manifest.get().strip()
        b = self.ent_buildid.get().strip()
        if m:
            out["manifest"] = m
        if b:
            out["buildid"] = b
        s = self.ent_steamdb.get().strip()
        if s:
            parsed = core.parse_steamdb_copy(s)
            out.setdefault("manifest", parsed.get("manifest"))
            out.setdefault("buildid", parsed.get("buildid"))
            out = {k: v for k, v in out.items() if v}
        return out

    def run_async(self, fn) -> None:
        self.set_busy(True)

        def worker() -> None:
            try:
                fn()
            except Exception:
                self.log("[错误]\n" + traceback.format_exc())
            finally:
                # 必须走 post_ui：这里若用 after()，主线程一旦不在 mainloop
                # 就会抛异常，finally 反而把 set_busy(False) 吞掉。
                self.post_ui(lambda: self.set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    # ---- actions ----
    def refresh_status(self) -> None:
        def work() -> None:
            rep = core.check_environment()
            paths = rep["paths"]
            self.post_ui(lambda: self.log(f"[paths] steam_root={paths['steam_root']}"))

            state: dict = {}
            acf_error: str | None = None
            try:
                acf = core.read_acf(core.ACF_PATH)
                state = acf.get("AppState", acf)
            except FileNotFoundError:
                acf_error = "不存在"
            except Exception as e:
                acf_error = f"读取失败 — {e}"

            if acf_error is not None:
                # 没有 ACF 时也照样把目录/联接/appinfo 显示出来，
                # 否则首次运行（还没点「一键伪装」）整个状态区是空的，
                # 用户看不到已检测到的路径。
                self.post_ui(lambda: self.var_acf.set(f"ACF: {acf_error} — {core.ACF_PATH}"))
                self.post_ui(lambda: self.var_depots.set("InstalledDepots: 尚未写入"))
            else:
                depots = state.get("InstalledDepots") or {}
                d = depots.get(core.DEPOT_ID, {})
                if not isinstance(d, dict):
                    d = {}
                buildid = state.get("buildid", "?")
                target = state.get("TargetBuildID", "?")
                size = state.get("SizeOnDisk", "?")
                manifest = d.get("manifest", "?")
                dsize = d.get("size", "?")
                self.post_ui(lambda: self.var_acf.set(
                    f"ACF: buildid={buildid}  TargetBuildID={target}  SizeOnDisk={size}"
                ))
                self.post_ui(lambda: self.var_depots.set(
                    f"InstalledDepots[{core.DEPOT_ID}]: manifest={manifest}  size={dsize}"
                ))

            if core.INSTALL_DIR.exists():
                sz = core.folder_size(core.INSTALL_DIR)
                note = "（放 shim 的地方）" if sz < 1024 ** 3 else "（看起来是真实安装，非 shim）"
                self.post_ui(lambda: self.var_dir.set(
                    f"Steam 安装目录: {core.INSTALL_DIR}\n"
                    f"                {note}实测={sz} ({sz / 1024**3:.2f} GiB)"
                ))
            else:
                self.post_ui(lambda: self.var_dir.set(
                    f"Steam 安装目录: {core.INSTALL_DIR}\n"
                    "                尚不存在，部署 shim 时会自动创建"
                ))

            official = rep.get("official_dir")
            if official:
                same = official.lower() == str(core.INSTALL_DIR).lower()
                self.post_ui(lambda: self.var_official.set(
                    f"官服目录: {official}\n"
                    f"            {'（与 Steam 目录相同）' if same else '（游戏本体，只读不改）'}"
                ))
                # 自动把官服目录填进输入框，但只填空白的那次
                def fill_official() -> None:
                    if not self.ent_gamedir.get().strip():
                        self.ent_gamedir.insert(0, official)
                self.post_ui(fill_official)
            else:
                self.post_ui(lambda: self.var_official.set("官服目录: 未找到，请手动填写"))

            # 非 Steam 快捷方式陷阱检测（**只读**）。
            # 找不到 game_dir 时 core 那边的检测会自动跳过，不会报错。
            try:
                sc = core.scan_steam_shortcuts()
            except Exception as e:                       # noqa: BLE001
                sc = {"traps": [], "skipped": [{"path": "-", "error": repr(e)}]}
            self.post_ui(lambda: self.show_shortcut_traps(sc.get("traps", [])))
            for s in sc.get("skipped", []):
                self.log(f"[快捷方式] 跳过 {s.get('path')}: {s.get('error')}")

            jinfo = core.junction_info()
            if jinfo["is_junction"]:
                self.post_ui(lambda: self.var_link.set(
                    f"目录联接（已退役的旧方案）: {core.INSTALL_DIR}\n"
                    f"        -> {jinfo.get('target')}\n"
                    f"        部署 shim 会自动拆掉它"
                ))
            elif core.shim_installed_marker():
                self.post_ui(lambda: self.var_link.set(
                    f"shim: 已部署于 {core.INSTALL_DIR}\n"
                    f"      Steam 点「开始游戏」→ shim → 官方启动器 → 游戏"
                ))
            else:
                self.post_ui(lambda: self.var_link.set("shim: 未部署（点「部署 shim」）"))

            app = core.parse_appinfo()
            info = core.extract_manifest_info(app)
            self.post_ui(lambda: self.var_info.set(
                f"appinfo: buildid={info.get('buildid')}  manifest={info.get('manifest')}"
            ))

            if acf_error is not None:
                self.post_ui(lambda: self.set_sync(
                    "未找到 appmanifest，点「一键伪装」自动创建", False
                ))
                return
            if not app:
                self.post_ui(lambda: self.set_sync(
                    "⚠ 读不到 appinfo.vdf，请先联网启动一次 Steam，或手动填 manifest/buildid", False
                ))
                return

            if not rep["admin"]:
                self.post_ui(lambda: self.log("[环境] 当前非管理员；改名 Program Files 下的目录会失败"))

            ok = (
                info.get("manifest")
                and manifest == info.get("manifest")
                and str(buildid) == str(info.get("buildid"))
                and str(target) == str(buildid)
            )
            if ok:
                self.post_ui(lambda: self.set_sync("✓ 已与 Steam 最新记录一致，更新应被阻止", True))
                self.log("[状态] manifest/buildid 已一致")
            else:
                self.post_ui(lambda: self.set_sync(
                    "⚠ 与 Steam 最新记录不一致，建议执行「一键伪装」", False
                ))
                self.log("[状态] 需要伪装：buildid/manifest 与 appinfo 不一致")

            for w in rep["warnings"]:
                self.log(f"[警告] {w}")
            for p in rep["problems"]:
                self.log(f"[阻塞] {p}")

        self.run_async(work)

    def on_apply(self) -> None:
        manual = self.collect_manual()

        def work() -> None:
            self.log("[apply] 开始写入伪装 manifests…")
            rc = core.cmd_apply(_ns(
                manifest=manual.get("manifest"),
                buildid=manual.get("buildid"),
                from_steamdb=self.ent_steamdb.get().strip() or None,
            ))
            if rc == 0:
                self.log("[apply] 完成。请完全退出并重启 Steam。")
            else:
                self.log(f"[apply] 失败，退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_restore(self) -> None:
        if not messagebox.askyesno("恢复备份", "确定用备份覆盖当前 appmanifest？"):
            return

        def work() -> None:
            self.log("[restore] 恢复备份…")
            rc = core.cmd_restore(_ns())
            self.log(f"[restore] 退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_parse_steamdb(self) -> None:
        text = self.ent_steamdb.get().strip()
        if not text:
            messagebox.showinfo("解析", "请先粘贴 SteamDB 复制的内容")
            return
        parsed = core.parse_steamdb_copy(text)
        if parsed.get("manifest"):
            self.ent_manifest.delete(0, "end")
            self.ent_manifest.insert(0, parsed["manifest"])
        if parsed.get("buildid"):
            self.ent_buildid.delete(0, "end")
            self.ent_buildid.insert(0, parsed["buildid"])
        if parsed:
            self.log(f"[steamdb] 解析结果: {parsed}")
        else:
            messagebox.showwarning(
                "解析",
                "未能识别，请用类似格式：\n"
                f"-app {core.APP_ID} -depot {core.DEPOT_ID} -manifest 3024011413268238677",
            )

    def on_detect(self) -> None:
        def work() -> None:
            self.log("[detect] 扫描 Steam 库与游戏位置…")
            paths = core.resolve_game_paths()
            targets = core.steam_link_targets()
            self.log(f"[detect] Steam 根: {paths.get('steam_root')}"
                         + ("  [已锁定]" if core.PINNED_LIBRARY else ""))
            self.log(f"[detect] appmanifest: {paths.get('appmanifest')}")
            self.log(f"[detect] 安装目录: {paths.get('install_dir')}")
            labels = []
            self._targets = targets
            for i, t in enumerate(targets):
                mark = " [有游戏]" if t.get("has_game") else ""
                labels.append(f"{t['label']}{mark}")
                self.log(
                    f"  目标{i}: ACF={t['appmanifest']} "
                    f"{'✓' if t.get('appmanifest_exists') else '×'}  "
                    f"DIR={t['install_dir']} "
                    f"{'✓' if t.get('install_dir_exists') else '×'}"
                )

            def ui() -> None:
                self.cmb_targets["values"] = labels
                # 显示当前实际生效的库，但不触发锁定
                active = core.STEAM_ROOT
                want = f"{active}"
                for i, lb in enumerate(labels):
                    if lb.startswith(want):
                        self.cmb_targets.current(i)
                        break
                else:
                    self.cmb_targets.set(want)

            self.post_ui(ui)

        self.run_async(work)

    def on_find_exe(self) -> None:
        from tkinter import filedialog

        start = filedialog.askdirectory(
            title=f"选择包含 “{core.GAME_EXE_NAME}” 的文件夹（官服「重新定位游戏」）"
        )
        if not start:
            return

        def work() -> None:
            self.log(f"[find] 在 {start} 查找 {core.GAME_EXE_NAME} …")
            exes = core.find_endfield_exe(Path(start))
            if not exes:
                self.log(f"[find] 未找到 {core.GAME_EXE_NAME}")
                self.post_ui(lambda: messagebox.showwarning(
                    "查找", f"未找到 {core.GAME_EXE_NAME}\n请选择包含该 exe 的文件夹。"
                ))
                return
            for x in exes:
                self.log(f"[find] 命中: {x}")
            folder = exes[0].parent

            def ui() -> None:
                self.ent_gamedir.delete(0, "end")
                self.ent_gamedir.insert(0, str(folder))
                messagebox.showinfo(
                    "查找", f"已定位官服游戏目录:\n{folder}\n\n共 {len(exes)} 个 {core.GAME_EXE_NAME}"
                )

            self.post_ui(ui)

        self.run_async(work)

    def on_auto_library(self) -> None:
        core.unpin_library()
        self.log("[库] 恢复自动选择 Steam 库")
        self.cmb_targets.set("")
        self.post_ui(self.refresh_status)

    def on_target_selected(self, _event=None) -> None:
        """真正锁定目标库 —— 多库机器上写错库 Steam 根本看不到游戏。"""
        idx = self.cmb_targets.current()
        targets = getattr(self, "_targets", None) or []
        if idx is None or idx < 0 or idx >= len(targets):
            return
        t = targets[idx]
        core.pin_library(t["label"])
        self.log(f"[target] 选中并锁定: {t['label']}")
        self.log(f"  ACF={t['appmanifest']}  ({'存在' if t['appmanifest_exists'] else '不存在'})")
        self.log(f"  DIR={t['install_dir']}  ({'存在' if t['install_dir_exists'] else '不存在'})")
        self.post_ui(self.refresh_status)

    def on_browse_gamedir(self) -> None:
        from tkinter import filedialog

        d = filedialog.askdirectory(
            title=f"选择官服游戏目录（含 {core.GAME_EXE_NAME} 的那层）"
        )
        if d:
            self.ent_gamedir.delete(0, "end")
            self.ent_gamedir.insert(0, d)

    def on_install_shim(self) -> None:
        # 打包后的工具里，shim 构建产物是**打进包里**的（sys._MEIPASS 下）。
        # 这里按优先级逐个试候选路径；一个都没命中才报错，并把**每一个**
        # 试过的路径都列出来 —— 只报一个等于让用户去猜。
        src, tried = core.resolve_shim_src_dir()
        if src is None:
            lines = "\n".join(f"  · {c}" for c in tried)
            messagebox.showerror(
                "部署 shim",
                f"找不到 shim 构建产物（{core.SHIM_EXE_NAME}）。\n\n"
                f"试过的路径：\n{lines}\n\n"
                "打包版的工具自带 shim；若这版没有，请在命令行运行：\n"
                "    python build_shim.py",
            )
            self.log(f"[shim] 找不到构建产物，试过: {tried}")
            return
        if not messagebox.askyesno(
            "部署 shim",
            "将执行：\n"
            f"1) 拆掉 Steam 安装目录里的旧目录联接（若有）\n"
            f"2) 把 shim 复制到 {core.INSTALL_DIR}\n\n"
            f"构建产物: {src}\n"
            f"启动深链：{core.find_deep_link_scheme() or '(未找到)'}{core.SHIM_DEEP_LINK_TAIL}\n\n"
            "官服游戏目录不会被改动。继续？",
        ):
            return

        def work() -> None:
            self.log("=== 部署 shim ===")
            rc = core.shim_install()
            self.log(f"[shim] 退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_remove_shim(self) -> None:
        if not messagebox.askyesno(
            "移除 shim",
            f"从 Steam 安装目录移除 shim？\n{core.INSTALL_DIR}\n\n"
            "只删除 Endfield.exe / _internal / shim_config.json / shim.log，"
            "目录里其它内容不动。",
        ):
            return

        def work() -> None:
            self.log("=== 移除 shim ===")
            rc = core.shim_remove()
            self.log(f"[shim] 退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_make_link(self) -> None:
        """目录联接方案已退役。这个按钮**不再创建任何东西**。

        保留按钮只是为了让老用户知道「这条路为什么不能走了」，点它只会看到
        一段说明。真正能用的是「部署 shim」。
        """
        messagebox.showwarning(
            "目录联接（已退役）",
            "目录联接（mklink /j）方案已退役，本工具**不再创建联接**。\n\n"
            "退役原因：\n"
            "1) 联接下 Steam 直接拉起游戏，Endfield.exe 的直接父进程是 "
            "steam.exe，U8 启动鉴权固定返回 10100「请通过官方启动器打开游戏」"
            "（16 组实测无一例外）。\n"
            "2) 建联接前要把已存在的真实目录整目录改名让位 —— 那会把你整个"
            "游戏安装搬走。那段代码已经删掉，不会再执行。\n\n"
            "现行方案是「部署 shim」：在 Steam 安装目录放一个同名 "
            "Endfield.exe，由它按父进程分流。",
        )
        self.log("[junction] 联接方案已退役，未做任何文件操作。")

    def on_del_link(self) -> None:
        """拆掉历史遗留的联接。删的只是链接本身，不动目标目录。"""
        if not messagebox.askyesno(
            "拆掉遗留联接",
            "删除 Steam 游戏目录里的遗留联接？\n"
            f"{core.INSTALL_DIR}\n\n"
            "只删链接本身（rmdir），官服本体文件不受影响。",
        ):
            return

        def work() -> None:
            self.log("[junction] 删除遗留联接…")
            rc = core.remove_junction(core.INSTALL_DIR)
            self.log(f"[junction] 退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_convert(self) -> None:
        game_dir = self.ent_gamedir.get().strip()
        if not game_dir:
            messagebox.showwarning("转换", "请填写官服游戏目录")
            return
        if not Path(game_dir).exists():
            messagebox.showerror("转换", f"目录不存在:\n{game_dir}")
            return
        force = messagebox.askyesno(
            "一键完整转换",
            "将执行完整流程：\n"
            "1) 写入/更新 appmanifest（manifest + buildid + 实测体积）\n"
            "2) 部署 shim 到 Steam 安装目录（会先拆掉旧联接）\n\n"
            "启动链路：steam → shim → 官方启动器 → 游戏\n"
            "官服游戏目录不会被改动。\n\n"
            f"{core.GAME_DISPLAY} 使用 ACE 反作弊，Steam 卸载不会清理驱动。\n继续？",
        )

        def work() -> None:
            self.log("=== 一键完整转换 ===")
            manual = self.collect_manual()
            rc = core.cmd_convert(_ns(
                game_dir=game_dir,
                manifest=manual.get("manifest"),
                buildid=manual.get("buildid"),
                from_steamdb=self.ent_steamdb.get().strip() or None,
                force_junction=False,
                force=False,
                launch_options=self.ent_dx.get().strip() or None,
            ))
            self.log(f"[convert] 退出码 {rc}")
            self.post_ui(self.refresh_status)

        self.run_async(work)

    def on_launch_opts(self) -> None:
        val = self.ent_dx.get().strip()
        core.set_launch_options(val)
        messagebox.showinfo(
            "启动项",
            "请在 Steam 中：右键「终末地」→ 属性 → 启动选项\n"
            f"填入：{val if val else '（留空 = DX12）'}",
        )

    def on_close(self) -> None:
        self.destroy()


def main() -> int:
    try:
        app = App()
        app.mainloop()
        return 0
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror(APP_TITLE, traceback.format_exc())
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())