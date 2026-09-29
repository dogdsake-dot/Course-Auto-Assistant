"""Chinese Windows desktop interface; no Playwright API is called from Tk."""
from __future__ import annotations

import os
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .browser import BrowserWorker
from .core import (CENTER, InstanceLock, NeedsUser, SiteConfig, data_dir,
                   load_json, save_json, starter_config)


class Application:
    def __init__(self, root: tk.Tk, home: Path):
        self.root, self.home = root, home
        self.busy, self.closing = False, False
        self.events = queue.Queue()
        self.worker = BrowserWorker(home, self.events)
        self.config_path = home / "site-config.json"
        if not self.config_path.exists():
            save_json(self.config_path, starter_config())
        root.title(f"刷课小助手 · Course Auto Assistant v{__version__}")
        root.geometry("980x740")
        root.minsize(820, 640)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.status = tk.StringVar(value="就绪")
        self.adapter_label = tk.StringVar()
        self.progress_label = tk.StringVar(value="本机观察时长：0秒（不是平台学分）")
        body = ttk.Frame(root, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="刷课小助手", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(body, text="英盛专用 · 自动课程队列 · 1倍速真实播放 · 登录仅保存在本机").pack(anchor="w", pady=(3, 10))
        ttk.Label(body, textvariable=self.adapter_label, wraplength=900).pack(anchor="w")
        ttk.Label(body, text=CENTER).pack(anchor="w", pady=(4, 12))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.idle_buttons = []
        for label, command in (("① 打开课程中心 / 登录", lambda: self.send("open")),
                               ("② 自动处理未完成课程", lambda: self.send("auto")),
                               ("关闭专用浏览器", lambda: self.send("close"))):
            b = ttk.Button(actions, text=label, command=command)
            b.pack(side="left", padx=(0, 8), pady=3)
            self.idle_buttons.append(b)
        control = ttk.Frame(body)
        control.pack(fill="x", pady=8)
        ttk.Button(control, text="暂停", command=self.worker.pause_job.set).pack(side="left", padx=(0, 8))
        ttk.Button(control, text="继续", command=self.worker.pause_job.clear).pack(side="left", padx=(0, 8))
        ttk.Button(control, text="停止队列", command=self.worker.stop_job.set).pack(side="left", padx=(0, 14))
        ttk.Label(control, textvariable=self.status).pack(side="left")
        ttk.Label(body, textvariable=self.progress_label).pack(anchor="w", pady=(0, 8))
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.pack(fill="x")
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True, pady=12)
        queue_panel = ttk.Frame(notebook, padding=8)
        notebook.add(queue_panel, text="课程队列")
        ttk.Label(queue_panel, text="平台状态不明确时停止；视频结束不等于课程完成或学分到账。").pack(anchor="w")
        self.tree = ttk.Treeview(queue_panel, columns=("title", "status"), show="headings", height=6)
        self.tree.heading("title", text="课程")
        self.tree.heading("status", text="扫描时的平台状态")
        self.tree.column("title", width=580)
        self.tree.column("status", width=190)
        self.tree.pack(fill="both", expand=True, pady=6)
        setup = ttk.Frame(notebook, padding=12)
        notebook.add(setup, text="英盛页面适配")
        ttk.Label(setup, text=("首版未获得英盛登录后页面结构，自动入口默认停用。\n"
                               "请在专用浏览器里分别进入课程中心、课程详情/章节列表、播放器，采集三份结构。\n"
                               "报告只在本机保存，不包含Cookie、输入框内容或网络请求；分享前仍需检查课程名和ID。"),
                  wraplength=880, justify="left").pack(anchor="w", pady=(0, 10))
        page_row = ttk.Frame(setup)
        page_row.pack(fill="x", pady=5)
        self.page_choice = ttk.Combobox(page_row, state="readonly", width=70)
        self.page_choice.pack(side="left", fill="x", expand=True)
        b = ttk.Button(page_row, text="刷新标签页", command=lambda: self.send("pages"))
        b.pack(side="left", padx=6)
        self.idle_buttons.append(b)
        capture = ttk.Frame(setup)
        capture.pack(anchor="w", pady=6)
        for label, stage in (("采集课程中心", "center"), ("采集章节列表", "lessons"), ("采集播放器", "player")):
            b = ttk.Button(capture, text=label, command=lambda s=stage: self.capture(s))
            b.pack(side="left", padx=(0, 8))
            self.idle_buttons.append(b)
        options = ttk.Frame(setup)
        options.pack(anchor="w", pady=6)
        for label, fn in (("打开本机资料目录", self.open_home), ("导入已核实的适配配置", self.import_config),
                          ("清除本机登录", self.clear_login)):
            b = ttk.Button(options, text=label, command=fn)
            b.pack(side="left", padx=(0, 8))
            self.idle_buttons.append(b)
        ttk.Label(setup, text=f"本机资料目录：{home}", wraplength=880).pack(anchor="w", pady=8)
        ttk.Label(body, text="运行记录（不上传）").pack(anchor="w")
        self.log = ScrolledText(body, height=7, wrap="word", state="disabled")
        self.log.pack(fill="x", pady=(4, 0))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_config_label()
        self.worker.start()
        root.after(150, self.poll)

    def refresh_config_label(self):
        try:
            cfg = SiteConfig.parse(load_json(self.config_path))
            cfg.validate_run()
            self.adapter_label.set("适配配置已提供；实际运行仍逐步核对页面状态，不保证网站改版后有效。")
        except (NeedsUser, ValueError, OSError):
            self.adapter_label.set("v0.1.0 基础版｜英盛实站适配：待核实。可以登录和采集页面，尚不能直接全自动跑课。")

    def set_busy(self, busy):
        self.busy = busy
        for button in self.idle_buttons:
            button.configure(state="disabled" if busy else "normal")

    def send(self, command, value=None):
        if self.busy or self.closing:
            return
        if not self.worker.is_alive():
            messagebox.showerror("引擎未启动", "请退出后重新运行助手。")
            return
        self.set_busy(True)
        self.worker.send(command, value)

    def capture(self, stage):
        if self.page_choice.current() < 0:
            messagebox.showinfo("选择页面", "请先打开浏览器并刷新标签页列表。")
            return
        if messagebox.askokcancel("仅本机采集", "将读取当前标签页的链接、按钮文字和元素结构。不会读取密码或Cookie。报告可能仍含课程名或ID，发送前请检查。"):
            self.send("inspect", (self.page_choice.current(), stage))

    def open_home(self):
        if os.name == "nt":
            os.startfile(str(self.home))
        else:
            messagebox.showinfo("资料目录", str(self.home))

    def import_config(self):
        path = filedialog.askopenfilename(title="选择已核实的英盛配置", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            raw = load_json(Path(path))
            SiteConfig.parse(raw).validate_run()
            if messagebox.askyesno("应用适配", "此配置必须来自对当前英盛页面的核实。确定替换本机适配配置吗？"):
                save_json(self.config_path, raw)
                self.refresh_config_label()
        except (ValueError, OSError, NeedsUser) as exc:
            messagebox.showerror("配置未通过检查", str(exc))

    def clear_login(self):
        if messagebox.askyesno("清除登录", "将关闭专用浏览器并删除助手的本地浏览器资料（含登录、缓存）。下次需重新登录。日常Edge不受影响。"):
            self.send("clear_login")

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text) + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 1000:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "busy":
                self.set_busy(value)
            elif kind == "status":
                self.status.set(value)
            elif kind == "queue":
                for item in self.tree.get_children():
                    self.tree.delete(item)
                for entry in value:
                    self.tree.insert("", "end", values=(entry["title"], "已完成" if entry["status"] == "complete" else "未完成"))
            elif kind == "pages":
                self.page_choice["values"] = [f"{i+1} | {url}" for i, url in value]
                if value:
                    self.page_choice.current(len(value) - 1)
            elif kind == "progress":
                current, duration = value["current"], value["duration"]
                if duration and current is not None:
                    self.progress["value"] = min(100, current * 100 / duration)
                self.progress_label.set(f"本机观察时长：{value['seconds']:.0f}秒（不是平台学分）")
            elif kind in ("log", "notice"):
                self.append_log(value)
                if kind == "notice" and not self.closing:
                    messagebox.showinfo("需要处理", value)
        if self.closing and not self.worker.is_alive():
            self.root.destroy()
            return
        self.root.after(150, self.poll)

    def on_close(self):
        self.closing = True
        self.status.set("正在关闭专用浏览器并保存本机登录…")
        self.set_busy(True)
        self.worker.stop_job.set()
        self.worker.shutdown.set()
        # Keep Tk responsive while the worker closes its own Playwright objects.


def run_gui():
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    lock = None
    try:
        home = data_dir()
        lock = InstanceLock(home)
        lock.acquire()
        Application(root, home)
        root.mainloop()
    except (OSError, NeedsUser, ValueError) as exc:
        messagebox.showerror("无法启动", str(exc), parent=root)
        root.destroy()
    finally:
        if lock:
            lock.close()
