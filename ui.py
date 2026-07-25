# ui.py
import datetime
import io
import math
import os
import random
import re
import shutil
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from tkinter import Menu, filedialog, messagebox, simpledialog

import customtkinter as ctk
import qrcode
import requests
from PIL import Image

from worker import DownloadWorker
from utils import BiliResolver, WbiSigner


class App(ctk.CTk):
    LIGHT = {
        "bg": "#F5F7FB",
        "surface": "#FFFFFF",
        "surface_2": "#EEF2F7",
        "line": "#D8DEE9",
        "text": "#172033",
        "muted": "#687386",
        "primary": "#FB7299",
        "primary_h": "#FF8EAE",
        "success": "#2EAD6B",
        "danger": "#E05A5A",
        "warning": "#D99026",
        "selected": "#FFF0F5",
        "done": "#EAF7EF",
        "hover": "#F9FBFF",
        "cover_bg": "#DDE5F0",
    }
    DARK = {
        "bg": "#111318",
        "surface": "#1B1F27",
        "surface_2": "#252B36",
        "line": "#333A46",
        "text": "#E8ECF3",
        "muted": "#9AA5B5",
        "primary": "#FB7299",
        "primary_h": "#FF8EAE",
        "success": "#65C987",
        "danger": "#EF7777",
        "warning": "#E3A64C",
        "selected": "#3A2632",
        "done": "#1D3126",
        "hover": "#222835",
        "cover_bg": "#303846",
    }

    def __init__(self, manager):
        super().__init__()
        self.mgr = manager
        self.title("BiliDownloader Studio")
        self.geometry("1440x900")
        self.minsize(1180, 760)

        ctk.set_appearance_mode("Light")
        ctk.set_default_color_theme("blue")
        self.palette = self.LIGHT

        self.fav_videos = []
        self.manual_videos = []
        self.filtered_view = []
        self.selected_indices = set()
        self.checkbox_vars = {}
        self.row_widgets = {}
        self.chart_regions = []
        self.cover_cache = {}
        self.cover_loading = set()
        self.cover_semaphore = threading.Semaphore(4)
        self.placeholder_cover = None
        self.themed_buttons = []
        self.themed_panels = []
        self.fav_data = {}
        self.current_page = 1
        self.page_size = 24
        self.source_mode = "fav"
        self.search_keyword = ""
        self.filter_month_target = None
        self.worker = None
        self.search_timer = None
        self.refresh_timer = None
        self._refresh_needs_stats = False
        self._fav_sync_running = False

        self.env_ffmpeg = False
        self.env_aria2 = False

        self.group_mode = ctk.BooleanVar(value=True)
        self.show_undone_only = ctk.BooleanVar(value=False)
        self.audio_only_mode = ctk.BooleanVar(value=False)
        self.dl_all_parts = ctk.BooleanVar(value=True)

        self.check_env_tools()
        self.setup_ui()
        self.setup_shortcuts()
        self.after(400, self.auto_login)

    # ---------- UI construction ----------
    def c(self, name):
        return self.palette[name]

    def setup_ui(self):
        self.configure(fg_color=self.c("bg"))
        self.grid_columnconfigure(0, weight=0, minsize=292)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=330)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self.build_topbar()
        self.build_sidebar()
        self.build_content()
        self.build_inspector()
        self.build_download_bar()
        self.apply_palette()

    def build_topbar(self):
        self.topbar = ctk.CTkFrame(self, height=64, corner_radius=0)
        self.topbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.topbar.grid_columnconfigure(1, weight=1)

        title_box = ctk.CTkFrame(self.topbar, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w", padx=22, pady=10)
        self.app_title = ctk.CTkLabel(title_box, text="BiliDownloader", font=("Segoe UI", 24, "bold"))
        self.app_title.pack(anchor="w")
        self.app_subtitle = ctk.CTkLabel(title_box, text="收藏夹批量下载工作台", font=("Microsoft YaHei UI", 12))
        self.app_subtitle.pack(anchor="w")

        search_box = ctk.CTkFrame(self.topbar, fg_color="transparent")
        search_box.grid(row=0, column=1, sticky="ew", padx=18)
        search_box.grid_columnconfigure(0, weight=1)
        self.search_entry = ctk.CTkEntry(search_box, height=38, placeholder_text="搜索标题 / BV 号")
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self.on_search_input)

        top_actions = ctk.CTkFrame(self.topbar, fg_color="transparent")
        top_actions.grid(row=0, column=2, sticky="e", padx=18)
        self.theme_btn = ctk.CTkButton(top_actions, text="深色", width=64, command=self.toggle_appearance)
        self.theme_btn.pack(side="left", padx=4)
        self.login_btn = ctk.CTkButton(top_actions, text="扫码登录", width=88, command=self.show_qr)
        self.login_btn.pack(side="left", padx=4)
        self.logout_btn = ctk.CTkButton(top_actions, text="退出", width=58, command=self.perform_logout)
        self.logout_btn.pack(side="left", padx=4)

    def build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=292, corner_radius=0)
        self.sidebar.grid(row=1, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        account = self.panel(self.sidebar)
        account.pack(fill="x", padx=16, pady=(16, 10))
        self.u_lbl = ctk.CTkLabel(account, text="未登录", font=("Microsoft YaHei UI", 15, "bold"), anchor="w")
        self.u_lbl.pack(fill="x", padx=14, pady=(12, 2))
        self.env_lbl = ctk.CTkLabel(account, text="", font=("Microsoft YaHei UI", 12), anchor="w")
        self.env_lbl.pack(fill="x", padx=14, pady=(0, 12))

        source_panel = self.panel(self.sidebar)
        source_panel.pack(fill="x", padx=16, pady=10)
        self.section_label(source_panel, "数据源").pack(anchor="w", padx=14, pady=(14, 8))
        self.fav_combo = ctk.CTkComboBox(source_panel, values=[], height=34)
        self.fav_combo.pack(fill="x", padx=14, pady=(0, 8))
        self.sync_bar = ctk.CTkProgressBar(source_panel, height=6)
        self.sync_bar.set(0)
        self.sync_bar.pack(fill="x", padx=14, pady=(0, 12))
        self.side_button(source_panel, "同步收藏夹", self.scrape_fav).pack(fill="x", padx=14, pady=4)
        self.side_button(source_panel, "导入外部收藏夹", self.import_external_fav).pack(fill="x", padx=14, pady=4)
        self.side_button(source_panel, "导入视频合集", self.import_collection_season).pack(fill="x", padx=14, pady=4)
        self.side_button(source_panel, "提取单个直链", self.open_direct_download).pack(fill="x", padx=14, pady=(4, 14))

        filter_panel = self.panel(self.sidebar)
        filter_panel.pack(fill="x", padx=16, pady=10)
        self.section_label(filter_panel, "筛选").pack(anchor="w", padx=14, pady=(14, 8))
        self.duration_combo = ctk.CTkComboBox(
            filter_panel,
            values=["全部时长", "<1分钟", "1-3分钟", "3-5分钟", "5-10分钟", ">10分钟"],
            height=34,
            command=lambda _: self.request_refresh(),
        )
        self.duration_combo.set("全部时长")
        self.duration_combo.pack(fill="x", padx=14, pady=(0, 8))
        self.group_switch = ctk.CTkSwitch(filter_panel, text="按月份分组", variable=self.group_mode, command=self.request_refresh)
        self.group_switch.pack(anchor="w", padx=14, pady=6)
        self.undone_switch = ctk.CTkSwitch(filter_panel, text="仅看未下载", variable=self.show_undone_only, command=self.request_refresh)
        self.undone_switch.pack(anchor="w", padx=14, pady=(6, 14))

        action_panel = self.panel(self.sidebar)
        action_panel.pack(fill="x", padx=16, pady=10)
        self.section_label(action_panel, "批量操作").pack(anchor="w", padx=14, pady=(14, 8))
        row1 = ctk.CTkFrame(action_panel, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=4)
        self.side_button(row1, "全选", self.select_all_view).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.side_button(row1, "全不选", self.deselect_all_view).pack(side="left", fill="x", expand=True, padx=(4, 0))
        row2 = ctk.CTkFrame(action_panel, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=4)
        self.side_button(row2, "标为已下", self.manual_mark_done).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.side_button(row2, "标为未下", self.manual_mark_undone).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.delete_btn = self.side_button(action_panel, "删除已选", self.delete_selected)
        self.delete_btn.pack(fill="x", padx=14, pady=(4, 14))

        self.backup_btn = self.side_button(self.sidebar, "导入备份数据", self.import_data_folder)
        self.backup_btn.pack(fill="x", padx=16, pady=(10, 16), side="bottom")

    def build_content(self):
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=16, pady=16)
        self.content.grid_rowconfigure(2, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        mode_bar = ctk.CTkFrame(self.content, height=48)
        mode_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        mode_bar.grid_columnconfigure(2, weight=1)
        self.source_seg = ctk.CTkSegmentedButton(
            mode_bar,
            values=["收藏夹", "手动"],
            command=self.on_source_change,
        )
        self.source_seg.set("收藏夹")
        self.source_seg.grid(row=0, column=0, padx=12, pady=8)
        self.count_lbl = ctk.CTkLabel(mode_bar, text="0 项", font=("Microsoft YaHei UI", 12))
        self.count_lbl.grid(row=0, column=1, padx=8)
        self.p_info = ctk.CTkLabel(mode_bar, text="Page 1 / 1", font=("Consolas", 12))
        self.p_info.grid(row=0, column=3, padx=8)
        self.prev_btn = ctk.CTkButton(mode_bar, text="<", width=36, command=lambda: self.change_page(-1))
        self.prev_btn.grid(row=0, column=4, padx=(4, 2))
        self.next_btn = ctk.CTkButton(mode_bar, text=">", width=36, command=lambda: self.change_page(1))
        self.next_btn.grid(row=0, column=5, padx=(2, 12))

        self.metric_bar = ctk.CTkFrame(self.content, fg_color="transparent")
        self.metric_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for col in range(3):
            self.metric_bar.grid_columnconfigure(col, weight=1)
        self.total_metric = self.metric_card(self.metric_bar, "当前列表", "0")
        self.total_metric.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.done_metric = self.metric_card(self.metric_bar, "已下载", "0")
        self.done_metric.grid(row=0, column=1, sticky="ew", padx=8)
        self.selected_metric = self.metric_card(self.metric_bar, "已选择", "0")
        self.selected_metric.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        self.list_frame = ctk.CTkScrollableFrame(self.content, corner_radius=10)
        self.list_frame.grid(row=2, column=0, sticky="nsew")

    def build_inspector(self):
        self.inspector = ctk.CTkFrame(self, width=330, corner_radius=0)
        self.inspector.grid(row=1, column=2, sticky="nsew")
        self.inspector.grid_propagate(False)

        stats_panel = self.panel(self.inspector)
        stats_panel.pack(fill="x", padx=16, pady=(16, 10))
        head = ctk.CTkFrame(stats_panel, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(14, 8))
        self.section_label(head, "数据概览").pack(side="left")
        self.year_combo = ctk.CTkComboBox(head, values=[], width=106, command=lambda _: self.draw_stats())
        self.year_combo.pack(side="right")
        self.stats_canvas = ctk.CTkCanvas(stats_panel, height=230, highlightthickness=0)
        self.stats_canvas.pack(fill="x", padx=12, pady=(0, 12))
        self.stats_canvas.bind("<Button-1>", self.on_chart_click)

        log_panel = self.panel(self.inspector)
        log_panel.pack(fill="both", expand=True, padx=16, pady=(10, 16))
        self.section_label(log_panel, "运行日志").pack(anchor="w", padx=14, pady=(14, 8))
        self.log_box = ctk.CTkTextbox(log_panel, font=("Consolas", 10), corner_radius=8)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def build_download_bar(self):
        self.bottom_panel = ctk.CTkFrame(self, height=92, corner_radius=0)
        self.bottom_panel.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.bottom_panel.grid_columnconfigure(0, weight=1)
        self.bottom_panel.grid_columnconfigure(1, weight=0)

        progress_box = ctk.CTkFrame(self.bottom_panel, fg_color="transparent")
        progress_box.grid(row=0, column=0, sticky="ew", padx=22, pady=12)
        progress_box.grid_columnconfigure(0, weight=1)
        self.lbl_total_progress = ctk.CTkLabel(progress_box, text="等待任务", font=("Microsoft YaHei UI", 13, "bold"), anchor="w")
        self.lbl_total_progress.grid(row=0, column=0, sticky="ew")
        self.pb_total = ctk.CTkProgressBar(progress_box, height=8)
        self.pb_total.set(0)
        self.pb_total.grid(row=1, column=0, sticky="ew", pady=(6, 4))
        self.pb_file = ctk.CTkProgressBar(progress_box, height=5)
        self.pb_file.set(0)
        self.pb_file.grid(row=2, column=0, sticky="ew")
        self.lbl_status_text = ctk.CTkLabel(progress_box, text="Ready", font=("Consolas", 10), anchor="w")
        self.lbl_status_text.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.parallel_progress_frame = ctk.CTkFrame(progress_box, fg_color="transparent")
        self.parallel_progress_frame.grid(row=2, column=0, rowspan=2, sticky="ew")
        self.parallel_progress_frame.grid_remove()
        self.parallel_progress_widgets = {}
        self.parallel_progress_slots = []

        controls = ctk.CTkFrame(self.bottom_panel, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e", padx=20, pady=12)
        self.audio_switch = ctk.CTkSwitch(controls, text="仅音频", variable=self.audio_only_mode)
        self.audio_switch.pack(side="left", padx=5)
        self.parts_switch = ctk.CTkSwitch(controls, text="下载所有part", variable=self.dl_all_parts)
        self.parts_switch.pack(side="left", padx=5)
        self.quality_combo_dl = ctk.CTkComboBox(controls, values=["4K", "2K", "1080", "720", "480"], width=78)
        self.quality_combo_dl.set("1080")
        self.quality_combo_dl.pack(side="left", padx=5)
        self.speed_entry = ctk.CTkEntry(controls, width=72, placeholder_text="KB/s")
        self.speed_entry.pack(side="left", padx=5)
        ctk.CTkLabel(controls, text="并行").pack(side="left", padx=(6, 0))
        self.parallel_combo_dl = ctk.CTkComboBox(
            controls, values=[str(value) for value in range(1, 9)], width=58
        )
        self.parallel_combo_dl.set("2")
        self.parallel_combo_dl.pack(side="left", padx=5)
        self.btn_start = ctk.CTkButton(controls, text="启动", width=78, command=self.start_download)
        self.btn_start.pack(side="left", padx=5)
        self.btn_pause = ctk.CTkButton(controls, text="暂停", width=64, state="disabled", command=self.pause_task)
        self.btn_pause.pack(side="left", padx=5)
        self.btn_cancel = ctk.CTkButton(controls, text="停止", width=64, state="disabled", command=self.cancel_task)
        self.btn_cancel.pack(side="left", padx=5)

    def set_parallel_progress_mode(self, enabled, slot_count=0):
        for widgets in self.parallel_progress_slots:
            widgets["row"].destroy()
        self.parallel_progress_widgets.clear()
        self.parallel_progress_slots.clear()
        if enabled:
            self.pb_file.grid_remove()
            self.lbl_status_text.grid_remove()
            self.parallel_progress_frame.grid()
            for _ in range(max(1, int(slot_count or 1))):
                self._create_parallel_progress_slot()
        else:
            self.parallel_progress_frame.grid_remove()
            self.pb_file.grid()
            self.lbl_status_text.grid()

    def _create_parallel_progress_slot(self):
        row = ctk.CTkFrame(self.parallel_progress_frame, fg_color="transparent")
        row.pack(fill="x", pady=(0, 5))
        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x")
        label = ctk.CTkLabel(
            header, text="等待任务", font=("Microsoft YaHei UI", 10), anchor="w"
        )
        label.pack(side="left", fill="x", expand=True)
        detail = ctk.CTkLabel(header, text="0%", font=("Consolas", 9), anchor="e")
        detail.pack(side="right")
        bar = ctk.CTkProgressBar(row, height=5)
        bar.set(0)
        bar.pack(fill="x", pady=(2, 0))
        widgets = {
            "row": row,
            "label": label,
            "detail": detail,
            "bar": bar,
            "task_key": None,
        }
        self.parallel_progress_slots.append(widgets)
        return widgets

    def update_parallel_progress(self, task_key, title, percent, status, done=False):
        key = str(task_key)
        widgets = self.parallel_progress_widgets.get(key)
        if done:
            if widgets:
                self.parallel_progress_widgets.pop(key, None)
                widgets["task_key"] = None
                widgets["label"].configure(text=f"{title} · {status}")
                widgets["detail"].configure(text="100%")
                widgets["bar"].set(1)
            return

        if widgets is None:
            widgets = next(
                (slot for slot in self.parallel_progress_slots if slot["task_key"] is None),
                None,
            )
            if widgets is None:
                widgets = self._create_parallel_progress_slot()
            widgets["task_key"] = key
            self.parallel_progress_widgets[key] = widgets

        value = min(max(float(percent or 0), 0), 1)
        widgets["label"].configure(text=f"{title} · {status}")
        widgets["detail"].configure(text=f"{int(value * 100)}%")
        widgets["bar"].set(value)

    def panel(self, parent):
        frame = ctk.CTkFrame(parent, corner_radius=8, fg_color=self.c("surface"), border_color=self.c("line"), border_width=1)
        self.themed_panels.append(frame)
        return frame

    def section_label(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=("Microsoft YaHei UI", 13, "bold"), text_color=self.c("text"))

    def side_button(self, parent, text, command):
        button = ctk.CTkButton(parent, text=text, height=34, command=command, fg_color=self.c("surface_2"), hover_color=self.c("hover"), text_color=self.c("text"), border_color=self.c("line"), border_width=1)
        self.themed_buttons.append(button)
        return button

    def metric_card(self, parent, title, value):
        card = ctk.CTkFrame(parent, corner_radius=8)
        label = ctk.CTkLabel(card, text=title, font=("Microsoft YaHei UI", 11), anchor="w")
        label.pack(fill="x", padx=14, pady=(10, 0))
        number = ctk.CTkLabel(card, text=value, font=("Segoe UI", 24, "bold"), anchor="w")
        number.pack(fill="x", padx=14, pady=(0, 10))
        card._title_label = label
        card._number_label = number
        return card

    def apply_palette(self):
        self.configure(fg_color=self.c("bg"))
        for frame in [self.topbar, self.sidebar, self.inspector, self.bottom_panel]:
            frame.configure(fg_color=self.c("surface"))
        for frame in [self.content]:
            frame.configure(fg_color="transparent")
        self.list_frame.configure(fg_color=self.c("surface_2"))
        self.stats_canvas.configure(bg=self.c("surface"))
        self.placeholder_cover = None
        self.app_title.configure(text_color=self.c("primary"))
        self.app_subtitle.configure(text_color=self.c("muted"))
        self.count_lbl.configure(text_color=self.c("muted"))
        self.p_info.configure(text_color=self.c("muted"))
        self.login_btn.configure(fg_color=self.c("primary"), hover_color=self.c("primary_h"))
        self.btn_start.configure(fg_color=self.c("primary"), hover_color=self.c("primary_h"))
        for panel in self.themed_panels:
            panel.configure(fg_color=self.c("surface"), border_color=self.c("line"))
        for button in self.themed_buttons:
            button.configure(fg_color=self.c("surface_2"), hover_color=self.c("hover"), text_color=self.c("text"), border_color=self.c("line"))
        for card in [self.total_metric, self.done_metric, self.selected_metric]:
            card.configure(fg_color=self.c("surface"), border_color=self.c("line"), border_width=1)
            card._title_label.configure(text_color=self.c("muted"))
            card._number_label.configure(text_color=self.c("text"))
        self.delete_btn.configure(fg_color=self.c("danger"), hover_color=self.c("danger"))
        self.theme_btn.configure(text="浅色" if self.palette is self.DARK else "深色")
        self.update_env_label()
        self.refresh_list()
        self.draw_stats()

    # ---------- Shortcuts and refresh ----------
    def setup_shortcuts(self):
        shortcuts = {
            "<Control-a>": self.on_ctrl_a, "<Control-A>": self.on_ctrl_a,
            "<Control-f>": self.on_ctrl_f, "<Control-F>": self.on_ctrl_f,
            "<Control-c>": self.on_ctrl_c, "<Control-C>": self.on_ctrl_c,
            "<Delete>": self.on_delete_key,
            "<F5>": self.on_refresh_key,
            "<Control-r>": self.on_refresh_key, "<Control-R>": self.on_refresh_key,
            "<Escape>": self.on_escape_key,
            "<space>": self.on_space_key,
            "<Prior>": lambda event: self._shortcut_change_page(-1),
            "<Next>": lambda event: self._shortcut_change_page(1),
            "<Home>": lambda event: self._shortcut_first_page(),
            "<End>": lambda event: self._shortcut_last_page(),
        }
        for sequence, handler in shortcuts.items():
            self.bind_all(sequence, handler)

    def is_text_input_focused(self):
        widget = self.focus_get()
        return widget is not None and widget.winfo_class() in {"Entry", "Text", "TEntry", "CTkEntry", "CTkTextbox"}

    def request_refresh(self, delay=16, redraw_stats=False):
        if redraw_stats:
            self._refresh_needs_stats = True
        if self.refresh_timer:
            self.after_cancel(self.refresh_timer)
        def _run():
            self.refresh_timer = None
            self.refresh_list()
            if self._refresh_needs_stats:
                self._refresh_needs_stats = False
                self.draw_stats()
        self.refresh_timer = self.after(delay, _run)

    # ---------- List and filters ----------
    def get_current_list(self):
        return self.fav_videos if self.source_mode == "fav" else self.manual_videos

    def _filter_data(self, source_data):
        filtered = []
        for item in source_data:
            title = item.get("title", "").lower()
            bvid = item.get("bvid", "").lower()
            if self.search_keyword and self.search_keyword not in title and self.search_keyword not in bvid:
                continue
            if self.source_mode == "fav" and self.filter_month_target and item.get("month") != self.filter_month_target:
                continue
            if self.show_undone_only.get() and item["bvid"] in self.mgr.history:
                continue
            if not self.check_duration_filter(item.get("duration", 0)):
                continue
            filtered.append(item)
        return filtered

    def refresh_list(self):
        source_data = self.get_current_list()
        filtered = self._filter_data(source_data)
        self.filtered_view = filtered

        total_pages = math.ceil(len(filtered) / self.page_size) or 1
        self.current_page = max(1, min(self.current_page, total_pages))
        start = (self.current_page - 1) * self.page_size
        items = filtered[start:start + self.page_size]

        self.count_lbl.configure(text=f"{len(filtered)} / {len(source_data)} 项")
        self.p_info.configure(text=f"Page {self.current_page} / {total_pages}")
        self.update_metrics(source_data)

        for child in self.list_frame.winfo_children():
            child.destroy()
        self.checkbox_vars.clear()
        self.row_widgets.clear()

        if not items:
            empty = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            empty.pack(fill="both", expand=True, pady=80)
            ctk.CTkLabel(empty, text="没有可显示的视频", font=("Microsoft YaHei UI", 18, "bold"), text_color=self.c("muted")).pack()
            return

        current_month = None
        for item in items:
            if self.source_mode == "fav" and self.group_mode.get() and item.get("month") != current_month:
                current_month = item.get("month")
                self.create_month_header(current_month)
            self.create_row(item)

    def create_month_header(self, month):
        header = ctk.CTkFrame(self.list_frame, fg_color="transparent", height=28)
        header.pack(fill="x", padx=8, pady=(12, 4))
        ctk.CTkLabel(header, text=month, font=("Segoe UI", 13, "bold"), text_color=self.c("muted")).pack(side="left", padx=8)
        ctk.CTkFrame(header, height=1, fg_color=self.c("line")).pack(side="left", fill="x", expand=True, padx=8)

    def update_metrics(self, source_data=None):
        if source_data is None:
            source_data = self.get_current_list()
        done_count = sum(1 for item in source_data if item["bvid"] in self.mgr.history)
        self.total_metric._number_label.configure(text=str(len(source_data)), text_color=self.c("text"))
        self.done_metric._number_label.configure(text=str(done_count), text_color=self.c("success"))
        self.selected_metric._number_label.configure(text=str(len(self.selected_indices)), text_color=self.c("primary"))

    def create_row(self, item):
        bvid = item["bvid"]
        is_done = bvid in self.mgr.history
        is_selected = bvid in self.selected_indices
        bg = self._row_bg(bvid)
        border = self.c("primary") if is_selected else (self.c("success") if is_done else self.c("line"))

        row = ctk.CTkFrame(self.list_frame, fg_color=bg, border_color=border, border_width=1, corner_radius=8)
        row.pack(fill="x", padx=10, pady=6)
        row.grid_columnconfigure(3, weight=1)

        strip_color = self.c("primary") if is_selected else (self.c("success") if is_done else self.c("line"))
        strip = ctk.CTkFrame(row, width=5, fg_color=strip_color, corner_radius=0)
        strip.grid(row=0, column=0, rowspan=2, sticky="nsw")

        var = ctk.BooleanVar(value=is_selected)
        cb = ctk.CTkCheckBox(row, text="", width=26, variable=var, command=partial(self._on_checkbox_change, bvid, var))
        cb.grid(row=0, column=1, rowspan=2, padx=(14, 8), pady=16)

        cover_box = ctk.CTkFrame(row, width=160, height=90, corner_radius=8, fg_color=self.c("cover_bg"))
        cover_box.grid(row=0, column=2, rowspan=2, padx=(0, 14), pady=12)
        cover_box.grid_propagate(False)
        cover_lbl = ctk.CTkLabel(cover_box, text="封面", text_color=self.c("muted"), font=("Microsoft YaHei UI", 12, "bold"))
        cover_lbl.place(relx=0.5, rely=0.5, anchor="center")

        title = item.get("title", "")
        title_lbl = ctk.CTkLabel(row, text=title[:96], anchor="w", font=("Microsoft YaHei UI", 15, "bold"), text_color=self.c("text") if not is_done else self.c("success"))
        title_lbl.grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=(16, 2))

        dur = self.format_duration(item.get("duration", 0))
        sub = f"{item.get('date', '')}  |  {dur}  |  {bvid}"
        meta_lbl = ctk.CTkLabel(row, text=sub, anchor="w", font=("Segoe UI", 11), text_color=self.c("muted"))
        meta_lbl.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=(0, 16))

        state_text = "已下" if is_done else "未下"
        state_lbl = ctk.CTkLabel(row, text=state_text, width=48, text_color=self.c("success") if is_done else self.c("muted"), font=("Microsoft YaHei UI", 12, "bold"))
        state_lbl.grid(row=0, column=4, rowspan=2, padx=14)

        for widget in [row, strip, cover_box, cover_lbl, title_lbl, meta_lbl, state_lbl]:
            widget.bind("<Button-1>", partial(self._on_row_click, bvid=bvid, var=var))
            widget.bind("<Button-3>", partial(self._on_right_click, bvid=bvid))
            widget.bind("<Enter>", partial(self._on_row_hover, bvid=bvid, active=True))
            widget.bind("<Leave>", partial(self._on_row_hover, bvid=bvid, active=False))

        self.checkbox_vars[bvid] = var
        self.row_widgets[bvid] = {"row": row, "strip": strip, "title": title_lbl, "state": state_lbl, "var": var, "cover": cover_lbl, "cover_box": cover_box, "item": item}
        self.load_cover_for_item(item)

    def _row_bg(self, bvid, hover=False):
        if bvid in self.selected_indices:
            return self.c("selected")
        if bvid in self.mgr.history:
            return self.c("done")
        return self.c("hover") if hover else self.c("surface")

    def _on_row_hover(self, event, bvid, active):
        widgets = self.row_widgets.get(bvid)
        if not widgets:
            return
        widgets["row"].configure(fg_color=self._row_bg(bvid, hover=active))

    def get_placeholder_cover(self):
        if self.placeholder_cover is None:
            img = Image.new("RGB", (320, 180), self.c("cover_bg"))
            self.placeholder_cover = ctk.CTkImage(img, size=(160, 90))
        return self.placeholder_cover

    def normalize_cover_url(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://"):
            return "https://" + url[len("http://"):]
        return url

    def load_cover_for_item(self, item):
        bvid = item.get("bvid")
        url = self.normalize_cover_url(item.get("cover") or item.get("pic") or "")
        widgets = self.row_widgets.get(bvid)
        if not widgets:
            return
        if not url:
            widgets["cover"].configure(image=self.get_placeholder_cover(), text="")
            return
        if url in self.cover_cache:
            widgets["cover"].configure(image=self.cover_cache[url], text="")
            return
        widgets["cover"].configure(image=self.get_placeholder_cover(), text="")
        if url in self.cover_loading:
            return
        self.cover_loading.add(url)

        def _fetch():
            with self.cover_semaphore:
                time.sleep(random.uniform(0.05, 0.18))
                try:
                    headers = {
                        "User-Agent": self.mgr.session.headers.get("User-Agent", "Mozilla/5.0"),
                        "Referer": "https://www.bilibili.com/",
                    }
                    resp = requests.get(url, headers=headers, timeout=12)
                    resp.raise_for_status()
                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    img.thumbnail((320, 180))
                    canvas = Image.new("RGB", (320, 180), self.c("cover_bg"))
                    x = (320 - img.width) // 2
                    y = (180 - img.height) // 2
                    canvas.paste(img, (x, y))
                    cover_img = ctk.CTkImage(canvas, size=(160, 90))
                    def _apply():
                        self.cover_loading.discard(url)
                        self.cover_cache[url] = cover_img
                        for current in self.row_widgets.values():
                            current_url = self.normalize_cover_url(current.get("item", {}).get("cover") or current.get("item", {}).get("pic") or "")
                            if current_url == url:
                                current["cover"].configure(image=cover_img, text="")
                    self.after(0, _apply)
                except Exception:
                    self.after(0, lambda: self.cover_loading.discard(url))
        threading.Thread(target=_fetch, daemon=True).start()

    def update_row_style(self, bvid):
        widgets = self.row_widgets.get(bvid)
        if not widgets:
            return
        is_done = bvid in self.mgr.history
        is_selected = bvid in self.selected_indices
        bg = self._row_bg(bvid)
        border = self.c("primary") if is_selected else (self.c("success") if is_done else self.c("line"))
        strip = self.c("primary") if is_selected else (self.c("success") if is_done else self.c("line"))
        widgets["row"].configure(fg_color=bg, border_color=border)
        widgets["strip"].configure(fg_color=strip)
        widgets["title"].configure(text_color=self.c("text") if not is_done else self.c("success"))
        widgets["state"].configure(text="已下" if is_done else "未下", text_color=self.c("success") if is_done else self.c("muted"))
        widgets["var"].set(is_selected)

    def _on_checkbox_change(self, bvid, var):
        if var.get():
            self.selected_indices.add(bvid)
        else:
            self.selected_indices.discard(bvid)
        self.update_row_style(bvid)
        self.update_metrics()

    def _on_row_click(self, event, bvid, var):
        var.set(not var.get())
        self._on_checkbox_change(bvid, var)
        return "break"

    def _on_right_click(self, event, bvid):
        self.show_context_menu(event, bvid)
        return "break"

    # ---------- Commands ----------
    def on_source_change(self, value):
        self.source_mode = "fav" if value == "收藏夹" else "manual"
        self.selected_indices.clear()
        self.current_page = 1
        self.request_refresh()

    def on_search_input(self, event):
        if self.search_timer:
            self.after_cancel(self.search_timer)
        self.search_timer = self.after(150, self.perform_search)

    def perform_search(self):
        self.search_keyword = self.search_entry.get().strip().lower()
        self.current_page = 1
        self.request_refresh()

    def toggle_appearance(self, init=False):
        if self.palette is self.LIGHT:
            self.palette = self.DARK
            ctk.set_appearance_mode("Dark")
        else:
            self.palette = self.LIGHT
            ctk.set_appearance_mode("Light")
        self.apply_palette()

    def draw_stats(self):
        self.stats_canvas.delete("all")
        self.chart_regions = []
        self.stats_canvas.configure(bg=self.c("surface"))
        if not self.fav_videos:
            self.stats_canvas.create_text(165, 110, text="暂无收藏夹数据", fill=self.c("muted"), font=("Microsoft YaHei UI", 13))
            self.year_combo.configure(values=[])
            return
        years = sorted({v["year"] for v in self.fav_videos}, reverse=True)
        self.year_combo.configure(values=years)
        year = self.year_combo.get()
        if year not in years:
            year = years[0]
            self.year_combo.set(year)
        month_data = {f"{year}-{str(m).zfill(2)}": {"total": 0, "done": 0} for m in range(1, 13)}
        for item in self.fav_videos:
            if item["year"] == year:
                month_data[item["month"]]["total"] += 1
                if item["bvid"] in self.mgr.history:
                    month_data[item["month"]]["done"] += 1
        width = max(self.stats_canvas.winfo_width(), 300)
        height = max(self.stats_canvas.winfo_height(), 220)
        bottom = height - 30
        max_total = max([m["total"] for m in month_data.values()] + [1])
        bar_w = (width - 34) / 12
        for index, key in enumerate(sorted(month_data)):
            data = month_data[key]
            x0 = 16 + index * bar_w + 4
            x1 = x0 + bar_w - 8
            total_h = (data["total"] / max_total) * (height - 72)
            done_h = (data["done"] / max_total) * (height - 72)
            self.chart_regions.append({"x0": x0, "x1": x1, "month": key})
            self.stats_canvas.create_rectangle(x0, bottom - total_h, x1, bottom, fill=self.c("primary"), outline="")
            self.stats_canvas.create_rectangle(x0, bottom - done_h, x1, bottom, fill=self.c("success"), outline="")
            label_color = self.c("primary") if self.filter_month_target == key else self.c("muted")
            self.stats_canvas.create_text((x0 + x1) / 2, bottom + 14, text=key[-2:], fill=label_color, font=("Segoe UI", 9))
            if data["total"]:
                self.stats_canvas.create_text((x0 + x1) / 2, bottom - total_h - 10, text=f"{data['done']}/{data['total']}", fill=self.c("muted"), font=("Segoe UI", 8))

    def log(self, text):
        try:
            should_follow = float(self.log_box.yview()[1]) >= 0.999
        except Exception:
            should_follow = True
        self.log_box.insert("end", f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}\n")
        if should_follow:
            self.log_box.see("end")

    def format_duration(self, seconds):
        if not seconds:
            return "--:--"
        minutes, sec = divmod(int(seconds), 60)
        hour, minutes = divmod(minutes, 60)
        return f"{hour}:{minutes:02d}:{sec:02d}" if hour else f"{minutes:02d}:{sec:02d}"

    def select_all_view(self):
        for item in self.filtered_view:
            self.selected_indices.add(item["bvid"])
        for bvid in list(self.row_widgets):
            self.update_row_style(bvid)
        self.update_metrics()

    def deselect_all_view(self):
        self.selected_indices.clear()
        for bvid in list(self.row_widgets):
            self.update_row_style(bvid)
        self.update_metrics()

    def check_duration_filter(self, seconds):
        current = self.duration_combo.get()
        if current == "全部时长":
            return True
        if not seconds:
            return True
        if current == "<1分钟":
            return seconds < 60
        if current == "1-3分钟":
            return 60 <= seconds < 180
        if current == "3-5分钟":
            return 180 <= seconds < 300
        if current == "5-10分钟":
            return 300 <= seconds < 600
        return seconds >= 600

    def show_context_menu(self, event, bvid):
        menu = Menu(self, tearoff=0)
        menu.add_command(label="浏览器打开", command=lambda: webbrowser.open(f"https://www.bilibili.com/video/{bvid}"))
        menu.add_command(label="复制 BV 号", command=lambda: self.copy_text(bvid))
        menu.tk_popup(event.x_root, event.y_root)

    def copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def change_page(self, delta):
        self.current_page += delta
        self.request_refresh()

    def on_chart_click(self, event):
        for region in self.chart_regions:
            if region["x0"] <= event.x <= region["x1"]:
                month = region["month"]
                self.filter_month_target = None if self.filter_month_target == month else month
                self.source_seg.set("收藏夹")
                self.source_mode = "fav"
                self.current_page = 1
                self.request_refresh(redraw_stats=True)
                break

    # ---------- Bilibili data ----------
    def import_external_fav(self):
        text = simpledialog.askstring("导入外部收藏夹", "请输入收藏夹链接或 ID：")
        if not text:
            return
        fid = None
        for pattern in [r"fid=(\d+)", r"ml(\d+)", r"^(\d+)$"]:
            match = re.search(pattern, text.strip())
            if match:
                fid = match.group(1)
                break
        if not fid:
            messagebox.showerror("错误", "无法识别收藏夹 ID")
            return

        def _fetch_info():
            try:
                params = {"media_id": fid, "pn": 1, "ps": 1}
                img_key, sub_key = WbiSigner.get_wbi_keys(self.mgr.session)
                if img_key and sub_key:
                    params = WbiSigner.enc_wbi(params, img_key, sub_key)
                res = self.mgr.session.get("https://api.bilibili.com/x/v3/fav/resource/list", params=params, timeout=15).json()
                if res.get("code") != 0:
                    self.after(0, lambda: messagebox.showerror("失败", res.get("message", "未知错误")))
                    return
                title = res["data"]["info"]["title"]
                display_name = f"[外部] {title}"
                def _update():
                    self.fav_data[display_name] = fid
                    self.fav_combo.configure(values=list(self.fav_data.keys()))
                    self.fav_combo.set(display_name)
                    if messagebox.askyesno("导入成功", f"已识别收藏夹：{title}\n是否立即同步？"):
                        self.scrape_fav()
                self.after(0, _update)
            except Exception as exc:
                self.after(0, lambda err=str(exc): messagebox.showerror("错误", f"网络异常：{err}"))
        threading.Thread(target=_fetch_info, daemon=True).start()

    def import_collection_season(self):
        text = simpledialog.askstring("导入合集", "请输入合集中任意视频链接或 BV 号：")
        if not text:
            return
        match = re.search(r"(BV[a-zA-Z0-9]{10})", text) or re.search(r"bvid=(BV\w+)", text)
        if not match:
            messagebox.showerror("错误", "无法识别 BV 号")
            return
        bvid = match.group(1)

        def _fetch_collection():
            try:
                self.after(0, lambda: self.log(f"解析合集：{bvid}"))
                data = self.mgr.session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=15).json()
                if data.get("code") != 0:
                    self.after(0, lambda: messagebox.showerror("错误", data.get("message", "API 请求失败")))
                    return
                season = data["data"].get("ugc_season")
                if not season:
                    self.after(0, lambda: messagebox.showwarning("提示", "该视频不属于合集"))
                    return
                title = season.get("title", "未知合集")
                new_items = []
                existing = {v["bvid"] for v in self.manual_videos}
                for section in season.get("sections", []):
                    for episode in section.get("episodes", []):
                        ep_bvid = episode.get("bvid")
                        if not ep_bvid or ep_bvid in existing:
                            continue
                        arc = episode.get("arc", {})
                        dt = datetime.datetime.fromtimestamp(arc.get("pubdate", time.time()))
                        new_items.append({
                            "title": episode.get("title", ep_bvid),
                            "bvid": ep_bvid,
                            "date": dt.strftime("%Y-%m-%d"),
                        "year": dt.strftime("%Y"),
                        "month": title[:15],
                        "duration": arc.get("duration", 0),
                        "cover": episode.get("cover") or arc.get("pic") or "",
                        })
                def _finish():
                    for item in reversed(new_items):
                        self.manual_videos.insert(0, item)
                        self.selected_indices.add(item["bvid"])
                    self.source_seg.set("手动")
                    self.source_mode = "manual"
                    self.current_page = 1
                    self.request_refresh()
                    messagebox.showinfo("完成", f"已导入 {len(new_items)} 个视频")
                self.after(0, _finish)
            except Exception as exc:
                self.after(0, lambda err=str(exc): messagebox.showerror("异常", f"解析失败：{err}"))
        threading.Thread(target=_fetch_collection, daemon=True).start()

    def scrape_fav(self):
        if self._fav_sync_running:
            self.log("收藏夹同步正在进行")
            return
        if not self.fav_data:
            self.log("请先登录或导入收藏夹")
            return
        fid = self.fav_data.get(self.fav_combo.get())
        if not fid:
            return

        def run():
            self._fav_sync_running = True
            videos = []
            page_size = 20
            max_workers = 2
            request_gap = (0.12, 0.35)
            api_url = "https://api.bilibili.com/x/v3/fav/resource/list"
            img_key, sub_key = WbiSigner.get_wbi_keys(self.mgr.session)
            headers = dict(self.mgr.session.headers)
            cookies = self.mgr.session.cookies.get_dict()

            def build_params(page):
                params = {"media_id": fid, "pn": page, "ps": page_size, "keyword": "", "order": "mtime", "type": 0, "tid": 0, "platform": "web"}
                if img_key and sub_key:
                    params = WbiSigner.enc_wbi(params, img_key, sub_key)
                return params

            def parse_medias(medias):
                items = []
                for media in medias:
                    dt = datetime.datetime.fromtimestamp(media["fav_time"])
                    items.append({
                        "title": media["title"],
                        "bvid": media["bvid"],
                        "date": dt.strftime("%Y-%m-%d"),
                        "year": dt.strftime("%Y"),
                        "month": dt.strftime("%Y-%m"),
                        "duration": media.get("duration", 0),
                        "cover": media.get("cover") or media.get("pic") or "",
                    })
                return items

            def fetch_page(page):
                if page > 1:
                    time.sleep(random.uniform(*request_gap))
                sess = requests.Session()
                sess.headers.update(headers)
                sess.cookies.update(cookies)
                result = sess.get(api_url, params=build_params(page), timeout=15).json()
                if result.get("code") != 0:
                    if result.get("code") in (-412, -352, 412, 352):
                        raise RuntimeError(f"疑似触发风控，已停止同步：{result.get('message', '未知错误')}")
                    raise RuntimeError(result.get("message", "未知错误"))
                data = result.get("data") or {}
                medias = data.get("medias") or []
                info = data.get("info") or {}
                return page, parse_medias(medias), int(info.get("media_count") or len(medias))

            try:
                self.after(0, lambda: (self.sync_bar.set(0), self.log("开始同步收藏夹")))
                _, first_items, total_count = fetch_page(1)
                videos.extend(first_items)
                total_pages = max(1, math.ceil(total_count / page_size)) if first_items else 1
                self.after(0, lambda total=total_count: self.log(f"收藏夹共 {total} 个视频，温和并发拉取中"))
                self.after(0, lambda: self.sync_bar.set(1 / total_pages))
                if total_pages > 1:
                    page_results = {}
                    failed_pages = []
                    workers = min(max_workers, total_pages - 1)
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        future_map = {executor.submit(fetch_page, page): page for page in range(2, total_pages + 1)}
                        done_pages = 1
                        for future in as_completed(future_map):
                            page_no = future_map[future]
                            try:
                                _, items, _ = future.result()
                                page_results[page_no] = items
                            except Exception as page_error:
                                if "疑似触发风控" in str(page_error):
                                    for pending in future_map:
                                        pending.cancel()
                                    self.after(0, lambda err=str(page_error): self.log(err))
                                    return
                                failed_pages.append(page_no)
                            done_pages += 1
                            self.after(0, lambda value=done_pages / total_pages: self.sync_bar.set(value))
                    for page_no in failed_pages:
                        try:
                            _, items, _ = fetch_page(page_no)
                            page_results[page_no] = items
                        except Exception as retry_error:
                            self.after(0, lambda p=page_no, err=str(retry_error): self.log(f"第 {p} 页同步失败：{err}"))
                    for page_no in sorted(page_results):
                        videos.extend(page_results[page_no])
                def _finish():
                    self.fav_videos = videos
                    self.source_seg.set("收藏夹")
                    self.source_mode = "fav"
                    self.current_page = 1
                    self.sync_bar.set(1)
                    self.request_refresh(redraw_stats=True)
                    self.log(f"同步完成：{len(videos)} 个视频")
                self.after(0, _finish)
            except Exception as exc:
                self.after(0, lambda err=str(exc): self.log(f"同步失败：{err}"))
            finally:
                self._fav_sync_running = False
        threading.Thread(target=run, daemon=True).start()

    def open_direct_download(self):
        text = simpledialog.askstring("提取直链", "请输入 B 站视频链接或 BV 号：")
        if not text:
            return
        match = re.search(r"(BV[a-zA-Z0-9]{10})", text) or re.search(r"bvid=(BV\w+)", text)
        if not match:
            messagebox.showerror("错误", "无效链接")
            return
        bvid = match.group(1)
        if any(v["bvid"] == bvid for v in self.manual_videos):
            return
        title = f"Extracted_{bvid}"
        cover = ""
        duration = 0
        try:
            data = self.mgr.session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=15).json()
            if data.get("code") == 0:
                info = data.get("data") or {}
                title = info.get("title") or title
                cover = info.get("pic") or ""
                duration = info.get("duration") or 0
            else:
                _, _, duration = BiliResolver.get_video_stream(bvid, self.mgr.session)
        except Exception:
            _, _, duration = BiliResolver.get_video_stream(bvid, self.mgr.session)
        item = {
            "title": title,
            "bvid": bvid,
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "year": "M",
            "month": "Manual",
            "duration": duration or 0,
            "cover": cover,
        }
        self.manual_videos.insert(0, item)
        self.selected_indices.add(bvid)
        self.source_seg.set("手动")
        self.source_mode = "manual"
        self.current_page = 1
        self.request_refresh()

    # ---------- Account ----------
    def auto_login(self):
        def _task():
            try:
                data = self.mgr.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()
                if data.get("code") == 0:
                    self.mgr.switch_user(data["data"]["mid"])
                    uname = data["data"].get("uname", data["data"]["mid"])
                    self.after(0, lambda: self.u_lbl.configure(text=f"用户：{uname}"))
                    self.after(0, lambda: self.fetch_fav_list(data["data"]["mid"]))
                else:
                    self.mgr.switch_user("guest")
            except Exception:
                pass
        threading.Thread(target=_task, daemon=True).start()

    def fetch_fav_list(self, mid):
        def _task():
            try:
                data = self.mgr.session.get(f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={mid}", timeout=15).json()
                if data.get("code") != 0:
                    return
                fav_data = {item["title"]: item["id"] for item in data["data"]["list"]}
                def _apply():
                    self.fav_data = fav_data
                    self.fav_combo.configure(values=list(self.fav_data.keys()))
                    if self.fav_data:
                        self.fav_combo.set(next(iter(self.fav_data)))
                    self.mgr.load_data()
                    self.request_refresh(redraw_stats=True)
                self.after(0, _apply)
            except Exception:
                pass
        threading.Thread(target=_task, daemon=True).start()

    def perform_logout(self):
        if not messagebox.askyesno("退出", "确定退出登录？"):
            return
        self.mgr.logout()
        self.u_lbl.configure(text="未登录")
        self.fav_combo.configure(values=[])
        self.fav_combo.set("")
        self.fav_data = {}
        self.fav_videos = []
        self.selected_indices.clear()
        self.request_refresh(redraw_stats=True)

    def show_qr(self):
        try:
            res = self.mgr.session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate", timeout=15).json()
            url = res["data"]["url"]
            key = res["data"]["qrcode_key"]
            qr = qrcode.QRCode()
            qr.add_data(url)
            qr.make()
            img = qr.make_image()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            top = ctk.CTkToplevel(self)
            top.title("扫码登录")
            top.geometry("300x320")
            top.attributes("-topmost", True)
            ctk.CTkLabel(top, text="使用哔哩哔哩 App 扫码登录", font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(18, 8))
            qr_img = ctk.CTkImage(Image.open(buf), size=(210, 210))
            top.qr_img = qr_img
            ctk.CTkLabel(top, image=qr_img, text="").pack(pady=4)
            def poll_once():
                if not top.winfo_exists():
                    return
                def _request():
                    try:
                        data = self.mgr.session.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}", timeout=15).json()
                        if data["data"]["code"] == 0:
                            self.after(0, top.destroy)
                            self.after(500, self.auto_login)
                            return
                    except Exception:
                        return
                    self.after(2000, poll_once)
                threading.Thread(target=_request, daemon=True).start()
            self.after(2000, poll_once)
        except Exception as exc:
            messagebox.showerror("错误", f"二维码生成失败：{exc}")

    def import_data_folder(self):
        src = filedialog.askopenfilename(
            title="选择备份数据文件",
            filetypes=[("JSON 备份文件", "*.json"), ("所有文件", "*.*")],
        )
        if not src:
            return
        if self.mgr.import_backup_files(src):
            self.request_refresh(redraw_stats=True)
            messagebox.showinfo("OK", "导入成功")

    # ---------- Download ----------
    def start_download(self):
        targets = [item for item in self.fav_videos + self.manual_videos if item["bvid"] in self.selected_indices]
        unique = []
        seen = set()
        for item in targets:
            if item["bvid"] not in seen:
                unique.append(item)
                seen.add(item["bvid"])
        if not unique:
            messagebox.showwarning("提示", "请先选择视频")
            return
        save_dir = filedialog.askdirectory()
        if not save_dir:
            return
        try:
            speed = int(self.speed_entry.get())
        except Exception:
            speed = 0
        quality = self.quality_combo_dl.get()
        audio_only = self.audio_only_mode.get()
        dl_all = self.dl_all_parts.get()
        try:
            parallelism = max(1, min(8, int(self.parallel_combo_dl.get() or 2)))
        except Exception:
            parallelism = 2
        self.check_env_tools()
        if not self.env_ffmpeg and not audio_only and quality in ["4K", "2K", "1080", "720"]:
            messagebox.showerror("缺少组件", f"无法下载 {quality} 画质：未检测到 FFmpeg。")
            return

        self.btn_start.configure(state="disabled", text="运行中")
        self.btn_pause.configure(state="normal", text="暂停")
        self.btn_cancel.configure(state="normal")
        self.pb_total.set(0)
        self.pb_file.set(0)
        parallel_mode = parallelism > 1 and len(unique) > 1
        self.set_parallel_progress_mode(
            parallel_mode, min(parallelism, len(unique)) if parallel_mode else 0
        )

        _last_switch_text = None
        _logged_parallel_titles = set()

        def progress(
            percent, text, is_switch, current_idx=0, total_cnt=1,
            task_key=None, task_title=None, task_done=False,
        ):
            nonlocal _last_switch_text
            def _apply():
                nonlocal _last_switch_text
                if percent == -1:
                    self.on_finish()
                    return
                if task_key is not None:
                    self.update_parallel_progress(
                        task_key, task_title or str(task_key), percent, text, task_done
                    )
                    return
                if is_switch:
                    completed_count = min(max(int(current_idx), 0), total_cnt)
                    self.lbl_total_progress.configure(
                        text=f"Total: {completed_count}/{total_cnt} | {text}"
                    )
                    self.pb_total.set(min(max(percent, 0), 1))
                    if text.startswith("并行 "):
                        parallel_title = text.partition("|")[2].strip()
                        if parallel_title and parallel_title not in _logged_parallel_titles:
                            _logged_parallel_titles.add(parallel_title)
                            self.log(f"开始：{text}")
                        return
                    if text != _last_switch_text:
                        _last_switch_text = text
                        self.log(text if text.startswith("完成:") else f"开始：{text}")
                else:
                    self.pb_file.set(min(max(percent, 0), 1))
                    self.lbl_status_text.configure(text=text)
            self.after(0, _apply)

        def history_cb(bvid):
            self.mgr.history.add(bvid)
            self.mgr.save_data()

        def fail_cb(item):
            self.after(0, lambda item=item: self.log(f"失败：{item['title']}"))

        def log_proxy(message):
            self.after(0, lambda message=message: self.log(message))

        self.worker = DownloadWorker(unique, save_dir, speed, quality, progress, history_cb, fail_cb, self.mgr.session, self.mgr.get_netscape_cookie_path, log_proxy, audio_only, dl_all, parallelism)
        threading.Thread(target=self.worker.run, daemon=True).start()

    def pause_task(self):
        if self.worker:
            self.worker.is_paused = not self.worker.is_paused
            self.btn_pause.configure(text="继续" if self.worker.is_paused else "暂停")
            self.lbl_status_text.configure(
                text="等待当前活动视频完成后暂停..." if self.worker.is_paused else "继续下载..."
            )

    def cancel_task(self):
        if self.worker:
            self.worker.cancel()
            self.btn_cancel.configure(state="disabled")
            self.lbl_status_text.configure(text="正在取消...")

    def on_finish(self):
        self.pb_total.set(1)
        self.pb_file.set(1)
        self.set_parallel_progress_mode(False)
        self.lbl_total_progress.configure(text="任务完成")
        self.lbl_status_text.configure(text="Ready")
        self.mgr.save_data()
        self.selected_indices.clear()
        self.btn_start.configure(state="normal", text="启动")
        self.btn_pause.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self.request_refresh(redraw_stats=True)
        self.log("所有任务执行完毕")

    # ---------- Batch actions ----------
    def manual_mark_done(self):
        for bvid in self.selected_indices:
            self.mgr.history.add(bvid)
        self.mgr.save_data()
        self.request_refresh(redraw_stats=True)

    def manual_mark_undone(self):
        for bvid in list(self.selected_indices):
            self.mgr.history.discard(bvid)
        self.mgr.save_data()
        self.request_refresh(redraw_stats=True)

    def delete_selected(self):
        if not self.selected_indices:
            return
        if not messagebox.askyesno("删除", "确定移除已选项目？"):
            return
        self.fav_videos = [item for item in self.fav_videos if item["bvid"] not in self.selected_indices]
        self.manual_videos = [item for item in self.manual_videos if item["bvid"] not in self.selected_indices]
        self.selected_indices.clear()
        self.request_refresh(redraw_stats=True)

    # ---------- Shortcuts ----------
    def on_ctrl_a(self, event):
        if self.is_text_input_focused():
            return None
        self.select_all_view()
        return "break"

    def on_ctrl_f(self, event):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def on_ctrl_c(self, event):
        if self.is_text_input_focused():
            return None
        if self.selected_indices:
            self.copy_text("\n".join(sorted(self.selected_indices)))
            self.log(f"已复制 {len(self.selected_indices)} 个 BV 号")
        return "break"

    def on_delete_key(self, event):
        if self.is_text_input_focused():
            return None
        self.delete_selected()
        return "break"

    def on_refresh_key(self, event):
        if self.is_text_input_focused():
            return None
        if self.source_mode == "fav" and self.fav_data:
            self.scrape_fav()
        else:
            self.request_refresh(redraw_stats=True)
        return "break"

    def on_escape_key(self, event):
        if self.is_text_input_focused():
            if self.search_entry.get():
                self.search_entry.delete(0, "end")
                self.perform_search()
                return "break"
            self.focus_set()
            return "break"
        if self.selected_indices:
            self.deselect_all_view()
            return "break"
        return None

    def on_space_key(self, event):
        if self.is_text_input_focused():
            return None
        if self.worker and self.btn_pause.cget("state") != "disabled":
            self.pause_task()
            return "break"
        return None

    def _shortcut_change_page(self, delta):
        if self.is_text_input_focused():
            return None
        self.change_page(delta)
        return "break"

    def _shortcut_first_page(self):
        if self.is_text_input_focused():
            return None
        self.current_page = 1
        self.request_refresh()
        return "break"

    def _shortcut_last_page(self):
        if self.is_text_input_focused():
            return None
        total = math.ceil(len(self.filtered_view) / self.page_size) or 1
        self.current_page = total
        self.request_refresh()
        return "break"

    # ---------- Environment ----------
    def check_env_tools(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.env_ffmpeg = os.path.exists(os.path.join(base_dir, "ffmpeg.exe")) or shutil.which("ffmpeg") is not None
        self.env_aria2 = os.path.exists(os.path.join(base_dir, "aria2c.exe")) or shutil.which("aria2c") is not None

    def update_env_label(self):
        ff = "FFmpeg 就绪" if self.env_ffmpeg else "FFmpeg 未检测"
        ar = "Aria2 就绪" if self.env_aria2 else "Aria2 未检测"
        self.env_lbl.configure(text=f"{ff}  |  {ar}", text_color=self.c("success") if self.env_ffmpeg else self.c("warning"))

    def open_date_picker(self):
        pass
