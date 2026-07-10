#!/usr/bin/env python3
"""Small desktop GUI for the Eagle thumbnail workflow."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, HORIZONTAL, LEFT, RIGHT, X, BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from one_click_eagle_thumbnail import FOLDERS_PATH, load_library_folders


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "one_click_eagle_thumbnail.py"
REPORT_PATH = ROOT / "exports" / "one_click_report.json"
STATE_PATH = ROOT / "exports" / "one_click_state.json"


class EagleThumbnailApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Eagle 视频封面套图工具")
        self.root.geometry("1120x760")
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.folders: list[dict] = []

        self.library_var = StringVar(value="")
        self.folder_var = StringVar(value="")
        self.limit_var = IntVar(value=20)
        self.frames_var = IntVar(value=8)
        self.columns_var = IntVar(value=4)
        self.width_var = IntVar(value=1920)
        self.history_match_var = BooleanVar(value=True)
        self.no_danmaku_var = BooleanVar(value=False)
        self.include_children_var = BooleanVar(value=True)
        self.skip_custom_var = BooleanVar(value=False)
        self.force_var = BooleanVar(value=False)
        self.apply_var = BooleanVar(value=False)
        self.progress_var = IntVar(value=0)
        self.status_var = StringVar(value="选择 Eagle .library 后开始。")

        self.build_ui()
        self.root.after(120, self.drain_queue)

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=BOTH, expand=True)

        top = ttk.LabelFrame(outer, text="库与范围", padding=10)
        top.pack(fill=X)

        row = ttk.Frame(top)
        row.pack(fill=X, pady=4)
        ttk.Label(row, text="Eagle 库").pack(side=LEFT)
        ttk.Entry(row, textvariable=self.library_var).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(row, text="选择", command=self.choose_library).pack(side=LEFT)
        ttk.Button(row, text="读取文件夹", command=self.load_folders).pack(side=LEFT, padx=(8, 0))

        row = ttk.Frame(top)
        row.pack(fill=X, pady=4)
        ttk.Label(row, text="处理文件夹").pack(side=LEFT)
        self.folder_combo = ttk.Combobox(row, textvariable=self.folder_var, state="readonly")
        self.folder_combo.pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Checkbutton(row, text="包含子文件夹", variable=self.include_children_var).pack(side=LEFT)

        opts = ttk.LabelFrame(outer, text="选项", padding=10)
        opts.pack(fill=X, pady=(12, 0))

        row = ttk.Frame(opts)
        row.pack(fill=X, pady=4)
        for label, var, width in [
            ("数量限制", self.limit_var, 8),
            ("静帧数", self.frames_var, 8),
            ("列数", self.columns_var, 8),
            ("基准宽度", self.width_var, 10),
        ]:
            ttk.Label(row, text=label).pack(side=LEFT, padx=(0, 4))
            ttk.Entry(row, textvariable=var, width=width).pack(side=LEFT, padx=(0, 16))

        row = ttk.Frame(opts)
        row.pack(fill=X, pady=4)
        ttk.Checkbutton(row, text="使用历史 BV + 高置信标题匹配", variable=self.history_match_var).pack(side=LEFT, padx=(0, 14))
        ttk.Checkbutton(row, text="不读取弹幕", variable=self.no_danmaku_var).pack(side=LEFT, padx=(0, 14))
        ttk.Checkbutton(row, text="跳过已有自定义封面", variable=self.skip_custom_var).pack(side=LEFT, padx=(0, 14))
        ttk.Checkbutton(row, text="强制重做", variable=self.force_var).pack(side=LEFT, padx=(0, 14))
        ttk.Checkbutton(row, text="直接应用到 Eagle", variable=self.apply_var).pack(side=LEFT)

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=(12, 0))
        self.run_btn = ttk.Button(actions, text="开始执行", command=self.start_run)
        self.run_btn.pack(side=LEFT)
        self.stop_btn = ttk.Button(actions, text="停止", command=self.stop_run, state="disabled")
        self.stop_btn.pack(side=LEFT, padx=8)
        ttk.Button(actions, text="打开报告", command=self.open_report).pack(side=LEFT, padx=8)
        ttk.Button(actions, text="清空日志", command=lambda: self.log.delete("1.0", END)).pack(side=RIGHT)

        prog = ttk.Frame(outer)
        prog.pack(fill=X, pady=(12, 0))
        ttk.Progressbar(prog, variable=self.progress_var, maximum=100, orient=HORIZONTAL).pack(fill=X, expand=True)
        ttk.Label(prog, textvariable=self.status_var).pack(fill=X, pady=(4, 0))

        log_frame = ttk.LabelFrame(outer, text="日志", padding=8)
        log_frame.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.log = ScrolledText(log_frame, height=20, wrap="word")
        self.log.pack(fill=BOTH, expand=True)

    def choose_library(self) -> None:
        path = filedialog.askdirectory(title="选择 Eagle .library 文件夹")
        if path:
            self.library_var.set(path)
            self.load_folders()

    def load_folders(self) -> None:
        library = Path(self.library_var.get().strip())
        if not library.exists():
            messagebox.showwarning("提示", "请先选择有效的 Eagle .library 文件夹。")
            return
        self.folders = load_library_folders(library)
        values = ["全部视频文件"] + [f"{item.get('path')}  [{item.get('id')}]" for item in self.folders]
        self.folder_combo["values"] = values
        if values:
            video_folder = next((v for v in values if v.startswith("视频类 ")), values[0])
            self.folder_var.set(video_folder)
        self.append_log(f"[folders] 已读取 {len(self.folders)} 个文件夹")

    def selected_folder_id(self) -> str:
        value = self.folder_var.get()
        match = re.search(r"\[([0-9A-Z]+)\]\s*$", value)
        return match.group(1) if match else ""

    def build_command(self) -> list[str]:
        cmd = [sys.executable, str(SCRIPT), "--library-dir", self.library_var.get().strip()]
        folder_id = self.selected_folder_id()
        if folder_id:
            cmd += ["--folder-id", folder_id]
            if self.include_children_var.get():
                cmd.append("--include-child-folders")
        cmd += ["--limit", str(max(0, self.limit_var.get()))]
        cmd += ["--frames", str(max(1, self.frames_var.get()))]
        cmd += ["--columns", str(max(1, self.columns_var.get()))]
        cmd += ["--sheet-width", str(max(720, self.width_var.get()))]
        if self.history_match_var.get():
            cmd.append("--history-title-match")
        if self.no_danmaku_var.get():
            cmd.append("--no-danmaku")
        if self.skip_custom_var.get():
            cmd.append("--skip-custom")
        if self.force_var.get():
            cmd.append("--force")
        if self.apply_var.get():
            cmd.append("--apply")
        return cmd

    def start_run(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        library = Path(self.library_var.get().strip())
        if not library.exists():
            messagebox.showwarning("提示", "请先选择有效的 Eagle .library 文件夹。")
            return
        if self.apply_var.get():
            ok = messagebox.askyesno("确认", "应用模式会修改 Eagle 库文件。请确认 Eagle 已关闭。继续吗？")
            if not ok:
                return
        cmd = self.build_command()
        self.append_log("\n$ " + " ".join(f'"{x}"' if " " in x else x for x in cmd) + "\n")
        self.progress_var.set(0)
        self.status_var.set("运行中...")
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        thread = threading.Thread(target=self.worker, args=(cmd,), daemon=True)
        thread.start()

    def worker(self, cmd: list[str]) -> None:
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.queue.put(("log", line))
            code = self.proc.wait()
            self.queue.put(("done", str(code)))
        except Exception as exc:
            self.queue.put(("log", f"[gui-error] {exc}\n"))
            self.queue.put(("done", "1"))

    def stop_run(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.append_log("[stop] 已请求停止，已完成项会保留在断点状态中。\n")

    def drain_queue(self) -> None:
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(value)
                self.parse_progress(value)
            elif kind == "done":
                self.run_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
                self.status_var.set("完成" if value == "0" else f"结束，退出码 {value}")
        self.root.after(120, self.drain_queue)

    def parse_progress(self, line: str) -> None:
        match = re.search(r"\[progress\]\s+(\d+)/(\d+).*?%\s*(.*)", line)
        if not match:
            return
        current, total, msg = int(match.group(1)), int(match.group(2)), match.group(3).strip()
        if total:
            self.progress_var.set(int(current * 100 / total))
        self.status_var.set(msg)

    def append_log(self, text: str) -> None:
        self.log.insert(END, text)
        self.log.see(END)

    def open_report(self) -> None:
        path = REPORT_PATH if REPORT_PATH.exists() else STATE_PATH
        if not path.exists():
            messagebox.showinfo("提示", "还没有报告文件。")
            return
        try:
            import os

            os.startfile(path)
        except Exception as exc:
            messagebox.showerror("错误", str(exc))


def main() -> None:
    root = Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    app = EagleThumbnailApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
