# ui.py

import datetime
import math
import webbrowser
import io
import re
import threading
import time
import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog, Menu
from PIL import Image
import qrcode
from tkcalendar import DateEntry

from config import THEME
from worker import DownloadWorker
from utils import WbiSigner, BiliResolver

class App(ctk.CTk):
    def __init__(self, manager):
        super().__init__()
        self.mgr = manager
        self.title("Bilibili 增强下载 V42.2 (边框修复版)")
        self.geometry("1400x900")
        
        # 默认设置
        ctk.set_appearance_mode("Light")
        ctk.set_default_color_theme("blue")
        
        # 数据模型
        self.fav_videos = []
        self.manual_videos = []
        self.selected_indices = set()
        self.current_page = 1
        self.page_size = 35
        self.filter_month_target = None
        self.checkbox_vars = {}
        self.row_frames = {} 
        self.chart_regions = []
        self.search_keyword = ""
        self.worker = None
        self.search_timer = None
        
        # 控制变量
        self.group_mode = ctk.BooleanVar(value=True)
        self.show_undone_only = ctk.BooleanVar(value=False)
        self.audio_only_mode = ctk.BooleanVar(value=False)
        
        # 绑定快捷键
        self.bind("<Control-a>", self.on_ctrl_a)

        self.setup_ui()
        self.after(500, self.auto_login)

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=260) 
        self.grid_columnconfigure(1, weight=3)             
        self.grid_columnconfigure(2, weight=1, minsize=320) 
        self.grid_rowconfigure(0, weight=1)                
        self.grid_rowconfigure(1, weight=0)                

        # === 1. 左侧边栏 ===
        self.sidebar = ctk.CTkFrame(self, width=260, corner_radius=0, fg_color=THEME["panel_bg"])
        self.sidebar.grid(row=0, column=0, sticky="nsew", rowspan=2)
        
        ctk.CTkLabel(self.sidebar, text="BILI WORKER", font=("Impact", 28), text_color=THEME["btn_primary"]).pack(pady=(30, 5))
        ctk.CTkLabel(self.sidebar, text="V42.2 Stable", font=("Arial", 12), text_color=THEME["text_sub"]).pack(pady=(0, 20))

        # 账户卡片
        self.account_card = ctk.CTkFrame(self.sidebar, fg_color=THEME["card_normal"], corner_radius=12)
        self.account_card.pack(padx=20, fill="x", pady=10)
        self.u_lbl = ctk.CTkLabel(self.account_card, text="未登录", font=("微软雅黑", 14, "bold"), text_color=THEME["text_main"])
        self.u_lbl.pack(pady=12)
        
        acc_btn_frame = ctk.CTkFrame(self.account_card, fg_color="transparent")
        acc_btn_frame.pack(pady=(0, 12), fill="x", padx=10)
        ctk.CTkButton(acc_btn_frame, text="扫码登录", width=80, height=30, fg_color="#00A1D6", hover_color="#008BB8", text_color="white", command=self.show_qr).pack(side="left", padx=2)
        ctk.CTkButton(acc_btn_frame, text="退出", width=60, height=30, fg_color=THEME["neutral"], hover_color="#7F8C8D", text_color="white", command=self.perform_logout).pack(side="right", padx=2)

        # 菜单
        self._create_sidebar_group("数据源")
        self.fav_combo = ctk.CTkComboBox(self.sidebar, values=[], height=32, border_color=THEME["input_border"], button_color=THEME["input_border"], fg_color=THEME["input_bg"], text_color=THEME["text_main"])
        self.fav_combo.pack(padx=20, pady=5, fill="x")
        self._create_sidebar_btn("🔄 同步收藏夹", self.scrape_fav)
        self._create_sidebar_btn("🔗 提取直链", self.open_direct_download)
        
        self.sync_bar = ctk.CTkProgressBar(self.sidebar, height=5, progress_color=THEME["btn_primary"])
        self.sync_bar.set(0); self.sync_bar.pack(padx=25, pady=10, fill="x")

        self._create_sidebar_group("系统")
        self._create_sidebar_btn("📂 导入备份", self.import_data_folder)
        
        # 模式切换
        self.mode_switch = ctk.CTkSwitch(self.sidebar, text="深色模式", command=self.toggle_appearance, font=("微软雅黑", 12), text_color=THEME["text_main"])
        self.mode_switch.pack(padx=20, pady=20, anchor="s", side="bottom")

        # === 2. 中间区域 ===
        self.center_area = ctk.CTkFrame(self, fg_color="transparent")
        self.center_area.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
        self.center_area.grid_columnconfigure(0, weight=1)
        self.center_area.grid_rowconfigure(1, weight=1)

        # 筛选栏
        self.filter_bar = ctk.CTkFrame(self.center_area, height=60, fg_color=THEME["panel_bg"], corner_radius=10, border_width=1, border_color=THEME["border"])
        self.filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        
        self.search_entry = ctk.CTkEntry(self.filter_bar, placeholder_text="🔍 搜索...", width=220, height=34, 
                                         fg_color=THEME["input_bg"], border_width=1, border_color=THEME["input_border"], text_color=THEME["text_main"])
        self.search_entry.pack(side="left", padx=15, pady=12)
        self.search_entry.bind("<KeyRelease>", self.on_search_input)

        self.duration_combo = ctk.CTkComboBox(self.filter_bar, values=["全部时长", "<1分钟", "1-3分钟", "3-5分钟", "5-10分钟", ">10分钟"], width=110, height=34, 
                                              fg_color=THEME["input_bg"], border_color=THEME["input_border"], button_color=THEME["input_border"], text_color=THEME["text_main"],
                                              command=lambda e: self.refresh_list())
        self.duration_combo.set("全部时长"); self.duration_combo.pack(side="left", padx=5)

        ctk.CTkSwitch(self.filter_bar, text="月份分组", variable=self.group_mode, command=self.refresh_list, font=("微软雅黑", 12), text_color=THEME["text_main"]).pack(side="left", padx=15)
        ctk.CTkSwitch(self.filter_bar, text="仅看未下", variable=self.show_undone_only, command=self.refresh_list, font=("微软雅黑", 12), text_color=THEME["text_main"]).pack(side="left", padx=5)
        
        # 列表 Tabs
        self.tab_view = ctk.CTkTabview(self.center_area, fg_color="transparent", corner_radius=10, text_color=THEME["text_main"])
        self.tab_view.grid(row=1, column=0, sticky="nsew")
        self.tab_view.add("📅 收藏夹同步")
        self.tab_view.add("🔗 直链/手动")
        self.tab_view.configure(command=self.on_tab_change)

        self.list_frame_fav = ctk.CTkScrollableFrame(self.tab_view.tab("📅 收藏夹同步"), fg_color=THEME["list_bg"])
        self.list_frame_fav.pack(fill="both", expand=True)
        self.list_frame_manual = ctk.CTkScrollableFrame(self.tab_view.tab("🔗 直链/手动"), fg_color=THEME["list_bg"])
        self.list_frame_manual.pack(fill="both", expand=True)

        # 底部操作栏
        self.action_bar = ctk.CTkFrame(self.center_area, height=50, fg_color=THEME["panel_bg"], corner_radius=10, border_width=1, border_color=THEME["border"])
        self.action_bar.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        
        ctk.CTkButton(self.action_bar, text="<", width=30, height=28, fg_color=THEME["input_bg"], text_color=THEME["text_main"], hover_color=THEME["input_border"], command=lambda: self.change_page(-1)).pack(side="left", padx=(15, 5), pady=10)
        self.p_info = ctk.CTkLabel(self.action_bar, text="1 / 1", font=("Consolas", 12), text_color=THEME["text_sub"])
        self.p_info.pack(side="left", padx=5)
        ctk.CTkButton(self.action_bar, text=">", width=30, height=28, fg_color=THEME["input_bg"], text_color=THEME["text_main"], hover_color=THEME["input_border"], command=lambda: self.change_page(1)).pack(side="left", padx=5)
        
        self._create_action_btn("🗑️", THEME["danger"], self.delete_selected, width=40)
        self._create_action_btn("标为未下", THEME["neutral"], self.manual_mark_undone)
        self._create_action_btn("标为已下", THEME["success"], self.manual_mark_done)
        self._create_action_btn("全不选", THEME["input_border"], self.deselect_all_view)
        self._create_action_btn("全选", THEME["btn_primary"], self.select_all_view)

        # === 3. 右侧面板 ===
        self.right_panel = ctk.CTkFrame(self, fg_color=THEME["panel_bg"], corner_radius=0, width=320)
        self.right_panel.grid(row=0, column=2, sticky="nsew", rowspan=2)
        
        ctk.CTkLabel(self.right_panel, text="数据概览", font=("微软雅黑", 14, "bold"), text_color=THEME["text_main"]).pack(pady=(30, 5))
        self.year_combo = ctk.CTkComboBox(self.right_panel, values=[], width=140, fg_color=THEME["input_bg"], border_color=THEME["input_border"], button_color=THEME["input_border"], text_color=THEME["text_main"], command=lambda e: self.draw_stats())
        self.year_combo.pack(pady=5)
        self.stats_canvas = ctk.CTkCanvas(self.right_panel, bg="#FFFFFF", highlightthickness=0, height=240)
        self.stats_canvas.pack(fill="x", padx=20, pady=15)
        self.stats_canvas.bind("<Button-1>", self.on_chart_click)
        
        ctk.CTkLabel(self.right_panel, text=">_ 系统终端", font=("Consolas", 12, "bold"), text_color=THEME["text_sub"], anchor="w").pack(pady=(15, 5), padx=20, fill="x")
        self.log_box = ctk.CTkTextbox(self.right_panel, font=("Consolas", 10), fg_color=THEME["list_bg"], text_color=THEME["text_logs"], corner_radius=8)
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # === 4. 底部下载栏 ===
        self.bottom_panel = ctk.CTkFrame(self, height=85, fg_color=THEME["list_bg"], corner_radius=0)
        self.bottom_panel.grid(row=1, column=1, sticky="ew", columnspan=2)
        
        self.status_frame = ctk.CTkFrame(self.bottom_panel, fg_color="transparent")
        self.status_frame.place(relx=0.03, rely=0.15, relwidth=0.55, relheight=0.7)
        self.lbl_total_progress = ctk.CTkLabel(self.status_frame, text="等待任务...", font=("微软雅黑", 13, "bold"), anchor="w", text_color=THEME["text_main"])
        self.lbl_total_progress.pack(fill="x")
        self.pb_total = ctk.CTkProgressBar(self.status_frame, height=8, progress_color=THEME["btn_primary"]); self.pb_total.set(0); self.pb_total.pack(fill="x", pady=4)
        self.pb_file = ctk.CTkProgressBar(self.status_frame, height=4, progress_color=THEME["success"]); self.pb_file.set(0); self.pb_file.pack(fill="x")
        self.lbl_status_text = ctk.CTkLabel(self.status_frame, text="Ready", font=("Consolas", 10), text_color=THEME["text_sub"], anchor="w"); self.lbl_status_text.pack(fill="x")

        self.ctrl_frame = ctk.CTkFrame(self.bottom_panel, fg_color="transparent")
        self.ctrl_frame.place(relx=0.6, rely=0.15, relwidth=0.38, relheight=0.7)
        
        ctk.CTkSwitch(self.ctrl_frame, text="🎵 仅音频", variable=self.audio_only_mode, font=("微软雅黑", 12, "bold"), text_color=THEME["text_main"]).pack(side="left", padx=10)
        self.quality_combo_dl = ctk.CTkComboBox(self.ctrl_frame, values=["1080", "720", "480"], width=75, fg_color=THEME["input_bg"], text_color=THEME["text_main"]); self.quality_combo_dl.set("1080"); self.quality_combo_dl.pack(side="left", padx=5)
        self.btn_start = ctk.CTkButton(self.ctrl_frame, text="🚀 启动", width=90, height=36, fg_color=THEME["btn_primary"], hover_color=THEME["btn_primary_hover"], font=("微软雅黑", 13, "bold"), command=self.start_download); self.btn_start.pack(side="left", padx=5)
        self.btn_pause = ctk.CTkButton(self.ctrl_frame, text="⏸", width=36, height=36, fg_color=THEME["border"], text_color=THEME["text_main"], state="disabled", command=self.pause_task); self.btn_pause.pack(side="left", padx=3)
        self.btn_cancel = ctk.CTkButton(self.ctrl_frame, text="⏹", width=36, height=36, fg_color=THEME["danger"], state="disabled", command=self.cancel_task); self.btn_cancel.pack(side="left", padx=3)
        self.speed_entry = ctk.CTkEntry(self.ctrl_frame, width=55, placeholder_text="KB/s", fg_color=THEME["input_bg"], text_color=THEME["text_main"]); self.speed_entry.pack(side="left", padx=5)

        self.toggle_appearance(init=True)

    def _create_sidebar_btn(self, text, cmd):
        ctk.CTkButton(self.sidebar, text=text, height=36, fg_color=THEME["input_bg"], hover_color=THEME["input_border"], 
                      text_color=THEME["text_main"], border_width=0, anchor="w", command=cmd).pack(padx=20, pady=4, fill="x")
    def _create_sidebar_group(self, text):
        ctk.CTkLabel(self.sidebar, text=text, anchor="w", font=("微软雅黑", 11, "bold"), text_color=THEME["text_sub"]).pack(padx=25, pady=(20, 4), fill="x")
    def _create_action_btn(self, text, color, cmd, width=70):
        ctk.CTkButton(self.action_bar, text=text, width=width, height=28, fg_color=color, text_color="white", command=cmd).pack(side="right", padx=5)

    def _adjust_color(self, hex_color): return hex_color

    def refresh_list(self):
        source_data, frame, enable_group = self.get_current_list()
        filtered = []
        for v in source_data:
            if self.search_keyword and self.search_keyword not in v['title'].lower(): continue
            if enable_group and self.filter_month_target and v['month'] != self.filter_month_target: continue
            is_done = v['bvid'] in self.mgr.history
            if self.show_undone_only.get() and is_done: continue
            if not self.check_duration_filter(v.get('duration', 0)): continue
            filtered.append(v)
        
        self.filtered_view = filtered
        for w in frame.winfo_children(): w.destroy()
        self.row_widgets = {} 

        total_p = math.ceil(len(filtered)/self.page_size) or 1
        start = (self.current_page - 1) * self.page_size
        items = filtered[start : start + self.page_size]
        self.p_info.configure(text=f"Page {self.current_page} / {total_p}")

        cur_m = ""
        self.checkbox_vars = {}
        for item in items:
            bvid = item['bvid']; is_done = bvid in self.mgr.history
            if enable_group and self.group_mode.get() and item['month'] != cur_m:
                cur_m = item['month']
                header = ctk.CTkFrame(frame, height=30, fg_color="transparent")
                header.pack(fill="x", pady=(15, 5))
                ctk.CTkLabel(header, text=f"📅 {cur_m}", text_color=THEME["text_sub"], font=("Arial", 14, "bold")).pack(side="left", padx=10)
                ctk.CTkFrame(header, height=2, fg_color=THEME["border"]).pack(side="left", fill="x", expand=True, padx=10)

            is_sel = bvid in self.selected_indices
            
            bg = THEME["card_sel"] if is_sel else (THEME["card_done"] if is_done else THEME["card_normal"])
            strip_c = THEME["strip_selected"] if is_sel else (THEME["strip_done"] if is_done else THEME["strip_normal"])
            
            # [CRASH FIX] 使用 THEME["border_hidden"] 而不是 "transparent"
            border_c = THEME["border_sel"] if is_sel else (THEME["border_done"] if is_done else THEME["border_hidden"])
            bw = 2 if is_sel else (1 if is_done else 0)

            row = ctk.CTkFrame(frame, fg_color=bg, border_width=bw, border_color=border_c, corner_radius=8)
            row.pack(fill="x", pady=4, padx=5)
            self.row_widgets[bvid] = {'row': row, 'strip': None, 'item': item}

            strip = ctk.CTkFrame(row, width=5, height=50, fg_color=strip_c, corner_radius=0)
            strip.pack(side="left", fill="y", padx=(0, 12))
            self.row_widgets[bvid]['strip'] = strip

            var = ctk.BooleanVar(value=is_sel)
            self.checkbox_vars[bvid] = var
            dur_str = self.format_duration(item.get('duration', 0))
            title_text = f"{item['title'][:50]}"
            t_color = THEME["text_done"] if is_done else THEME["text_main"]
            
            cb = ctk.CTkCheckBox(row, text="", width=24, variable=var, border_color=THEME["text_sub"],
                                 command=lambda b=bvid, v=var: self.toggle_row(b, v))
            cb.pack(side="left", padx=(0, 5), pady=10)
            
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=(0, 10))
            lbl_t = ctk.CTkLabel(info, text=title_text, text_color=t_color, font=("微软雅黑", 13, "bold"), anchor="w")
            lbl_t.pack(fill="x", pady=(5, 0))
            lbl_m = ctk.CTkLabel(info, text=f"{item['date']}   |   🕒 {dur_str}", text_color=THEME["text_sub"], font=("Arial", 10), anchor="w")
            lbl_m.pack(fill="x", pady=(0, 5))

            for w in [row, strip, info, lbl_t, lbl_m, cb]:
                w.bind("<Button-3>", lambda e, b=bvid: self.show_context_menu(e, b))
                if w != cb: w.bind("<Button-1>", lambda e, b=bvid, v=var: self.toggle_row(b, v, toggle=True))

    def toggle_row(self, bvid, var, toggle=False):
        if toggle: var.set(not var.get())
        if var.get(): self.selected_indices.add(bvid)
        else: self.selected_indices.discard(bvid)
        
        if bvid in self.row_widgets:
            widgets = self.row_widgets[bvid]
            is_done = bvid in self.mgr.history
            is_sel = var.get()
            
            new_bg = THEME["card_sel"] if is_sel else (THEME["card_done"] if is_done else THEME["card_normal"])
            new_strip = THEME["strip_selected"] if is_sel else (THEME["strip_done"] if is_done else THEME["strip_normal"])
            
            # [CRASH FIX] 局部刷新也需要使用 safe color
            new_border = THEME["border_sel"] if is_sel else (THEME["border_done"] if is_done else THEME["border_hidden"])
            bw = 2 if is_sel else (1 if is_done else 0)
            
            widgets['row'].configure(fg_color=new_bg, border_width=bw, border_color=new_border)
            widgets['strip'].configure(fg_color=new_strip)

    def on_search_input(self, event):
        if self.search_timer: self.after_cancel(self.search_timer)
        self.search_timer = self.after(300, self.perform_search)
    def perform_search(self): self.search_keyword = self.search_entry.get().strip().lower(); self.current_page = 1; self.refresh_list()
    
    def toggle_appearance(self, init=False):
        if not init:
            new_mode = "Dark" if ctk.get_appearance_mode() == "Light" else "Light"
            ctk.set_appearance_mode(new_mode)
            self.mode_switch.configure(text="深色模式" if new_mode == "Light" else "浅色模式")
        self.after(100, self.draw_stats)

    def draw_stats(self):
        self.stats_canvas.delete("all"); self.chart_regions = []
        mode_idx = 0 if ctk.get_appearance_mode() == "Light" else 1
        self.stats_canvas.configure(bg=THEME["panel_bg"][mode_idx])
        
        if not self.fav_videos: return
        years = sorted(list(set(v['year'] for v in self.fav_videos)), reverse=True)
        self.year_combo.configure(values=years)
        ty = self.year_combo.get()
        if ty not in years and years: ty = years[0]; self.year_combo.set(ty)
        
        md = {f"{ty}-{str(m).zfill(2)}": {'total': 0, 'done': 0} for m in range(1, 13)}
        for v in self.fav_videos:
            if v['year'] == ty:
                md[v['month']]['total'] += 1
                if v['bvid'] in self.mgr.history: md[v['month']]['done'] += 1
        vals = [x['total'] for x in md.values()]; mv = max(vals + [1])
        cw, ch = self.stats_canvas.winfo_width(), self.stats_canvas.winfo_height()
        bw = (cw - 40) / 12
        
        for i, k in enumerate(sorted(md.keys())):
            d = md[k]; x0 = 20 + i * bw + 4; x1 = x0 + bw - 8; yb = ch - 30
            h_total = (d['total'] / mv) * (ch - 70); h_done = (d['done'] / mv) * (ch - 70)
            self.chart_regions.append({'x0': x0, 'x1': x1, 'month': k})
            
            self.stats_canvas.create_rectangle(x0, yb - h_total, x1, yb, fill=THEME["btn_primary"][mode_idx], outline="")
            self.stats_canvas.create_rectangle(x0, yb - h_done, x1, yb, fill=THEME["success"][mode_idx], outline="")
            
            hl_c = THEME["btn_primary"][mode_idx] if self.filter_month_target == k else THEME["text_sub"][mode_idx]
            self.stats_canvas.create_text((x0+x1)/2, yb+15, text=k[-2:], fill=hl_c, font=("Arial", 10))
            if d['total'] > 0: self.stats_canvas.create_text((x0+x1)/2, yb - h_total - 10, text=f"{d['done']}/{d['total']}", fill=THEME["text_sub"][mode_idx], font=("Arial", 8))

    # (复制 V37 其余函数)
    def log(self, text): self.log_box.insert("end", f"[{datetime.datetime.now().strftime('%H:%M')}] {text}\n"); self.log_box.see("end")
    def format_duration(self, seconds): 
        if not seconds: return "--:--"
        m, s = divmod(seconds, 60); h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h>0 else f"{m:02d}:{s:02d}"
    def select_all_view(self): 
        for v in self.filtered_view: self.selected_indices.add(v['bvid'])
        self.sync_ui_vars(); self.refresh_list()
    def deselect_all_view(self):
        for v in self.filtered_view: self.selected_indices.discard(v['bvid'])
        self.sync_ui_vars(); self.refresh_list()
    def on_ctrl_a(self, event): 
        if self.focus_get() != self.search_entry: self.select_all_view()
    def check_duration_filter(self, seconds):
        ft = self.duration_combo.get()
        if ft == "全部时长": return True
        if not seconds: return True
        if ft == "<1分钟": return seconds < 60
        if ft == "1-3分钟": return 60 <= seconds < 180
        if ft == "3-5分钟": return 180 <= seconds < 300
        if ft == "5-10分钟": return 300 <= seconds < 600
        return seconds >= 600
    def sync_ui_vars(self):
        for b, v in self.checkbox_vars.items(): v.set(b in self.selected_indices)
    def show_context_menu(self, event, bvid):
        m = Menu(self, tearoff=0)
        m.add_command(label="🌏 浏览器打开", command=lambda: webbrowser.open(f"https://www.bilibili.com/video/{bvid}"))
        m.add_command(label="📋 复制 BV 号", command=lambda: self.clipboard_clear() or self.clipboard_append(bvid))
        m.tk_popup(event.x_root, event.y_root)
    def on_tab_change(self): self.selected_indices.clear(); self.current_page = 1; self.refresh_list()
    def get_current_list(self): return (self.fav_videos, self.list_frame_fav, True) if self.tab_view.get() == "📅 收藏夹同步" else (self.manual_videos, self.list_frame_manual, False)
    def change_page(self, d): self.current_page += d; self.refresh_list()
    def on_chart_click(self, event):
        for r in self.chart_regions:
            if r['x0'] <= event.x <= r['x1']:
                self.filter_month_target = r['month'] if self.filter_month_target != r['month'] else None
                self.tab_view.set("📅 收藏夹同步"); self.refresh_list(); self.draw_stats(); break
    def scrape_fav(self):
        fid = self.fav_data.get(self.fav_combo.get()); 
        if not fid: return
        def run():
            videos = []; page = 1; img_key, sub_key = WbiSigner.get_wbi_keys(self.mgr.session)
            while True:
                try:
                    params = {'media_id': fid, 'pn': page, 'ps': 20, 'keyword': '', 'order': 'mtime', 'type': 0, 'tid': 0, 'platform': 'web'}
                    if img_key and sub_key: params = WbiSigner.enc_wbi(params, img_key, sub_key)
                    res = self.mgr.session.get("https://api.bilibili.com/x/v3/fav/resource/list", params=params).json()
                    if res['code']!=0: self.log(f"API: {res['message']}"); break
                    if not res['data']['medias']: break
                    for m in res['data']['medias']:
                        dt = datetime.datetime.fromtimestamp(m['fav_time']); dur = m.get('duration', 0)
                        videos.append({'title':m['title'], 'bvid':m['bvid'], 'date':dt.strftime("%Y-%m-%d"), 'year':dt.strftime("%Y"), 'month':dt.strftime("%Y-%m"), 'duration': dur})
                    self.after(0, lambda: self.sync_bar.set(len(videos)/res['data']['info']['media_count']))
                    if len(videos) >= res['data']['info']['media_count']: break
                    page += 1; time.sleep(0.5)
                except: break
            self.fav_videos = videos; self.after(0, lambda: (self.refresh_list(), self.draw_stats()))
        threading.Thread(target=run, daemon=True).start()
    def open_direct_download(self):
        url = simpledialog.askstring("提取", "B站链接:"); 
        if not url: return
        match = re.search(r'(BV[a-zA-Z0-9]{10})', url); 
        if not match: match = re.search(r'bvid=(BV\w+)', url)
        if not match: return messagebox.showerror("错误", "无效链接")
        bvid = match.group(1); _, _, duration = BiliResolver.get_video_stream(bvid, self.mgr.session)
        if any(v['bvid'] == bvid for v in self.manual_videos): return
        item = {'title': f"Extracted_{bvid}", 'bvid': bvid, 'date': datetime.datetime.now().strftime("%Y-%m-%d"), 'month': "Manual", 'year': "M", 'duration': duration or 0}
        self.manual_videos.insert(0, item); self.selected_indices.add(bvid); self.tab_view.set("🔗 直链/手动"); self.on_tab_change()
    def auto_login(self):
        def _t():
            try:
                r = self.mgr.session.get("https://api.bilibili.com/x/web-interface/nav").json()
                if r['code'] == 0: self.mgr.switch_user(r['data']['mid']); self.after(0, lambda: self.u_lbl.configure(text=f"用户: {r['data']['uname']}")); self.after(0, self.fetch_fav_list, r['data']['mid'])
                else: self.mgr.switch_user("guest")
            except: pass
        threading.Thread(target=_t, daemon=True).start()
    def fetch_fav_list(self, mid):
        try:
            f = self.mgr.session.get(f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={mid}").json()
            if f['code']==0:
                self.fav_data = {i['title']: i['id'] for i in f['data']['list']}; self.fav_combo.configure(values=list(self.fav_data.keys()))
                if self.fav_data: self.fav_combo.set(list(self.fav_data.keys())[0])
                self.fav_videos = []; self.mgr.load_data(); self.refresh_list()
        except: pass
    def perform_logout(self):
        if messagebox.askyesno("退出", "确定退出?"): self.mgr.logout(); self.u_lbl.configure(text="未登录"); self.fav_combo.configure(values=[]); self.fav_combo.set(""); self.fav_videos=[]; self.refresh_list(); self.draw_stats()
    def import_data_folder(self):
        src = filedialog.askdirectory(); 
        if src and self.mgr.import_backup_files(src): self.refresh_list(); self.draw_stats(); messagebox.showinfo("OK", "导入成功")
    def show_qr(self):
        try:
            res = self.mgr.session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate").json(); url, key = res['data']['url'], res['data']['qrcode_key']
            qr = qrcode.QRCode(); qr.add_data(url); qr.make(); img = qr.make_image(); buf = io.BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
            top = ctk.CTkToplevel(self); top.geometry("300x300"); top.attributes("-topmost", True); ctk.CTkLabel(top, image=ctk.CTkImage(Image.open(buf), size=(200,200)), text="").pack(pady=20)
            def poll():
                while top.winfo_exists():
                    time.sleep(2)
                    try:
                        if self.mgr.session.get(f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}").json()['data']['code'] == 0: top.destroy(); self.after(500, self.auto_login); break
                    except: break
            threading.Thread(target=poll, daemon=True).start()
        except: pass
    def pause_task(self):
        if self.worker:
            self.worker.is_paused = not self.worker.is_paused
            self.btn_pause.configure(text="继续" if self.worker.is_paused else "暂停")
    def cancel_task(self):
        if self.worker: self.worker.is_cancelled = True; self.on_finish()
    def manual_mark_done(self):
        for b in self.selected_indices: self.mgr.history.add(b)
        self.mgr.save_data(); self.refresh_list(); self.draw_stats()
    def manual_mark_undone(self):
        for b in self.selected_indices:
            if b in self.mgr.history: self.mgr.history.remove(b)
        self.mgr.save_data(); self.refresh_list(); self.draw_stats()
    def delete_selected(self):
        if not self.selected_indices: return
        if not messagebox.askyesno("删除", "确定移除?"): return
        self.fav_videos = [v for v in self.fav_videos if v['bvid'] not in self.selected_indices]
        self.manual_videos = [v for v in self.manual_videos if v['bvid'] not in self.selected_indices]
        self.selected_indices.clear(); self.refresh_list(); self.draw_stats()
    def start_download(self):
        targets = []
        for v in self.fav_videos + self.manual_videos:
            if v['bvid'] in self.selected_indices: targets.append(v)
        seen = set(); unique = []
        for v in targets:
            if v['bvid'] not in seen: unique.append(v); seen.add(v['bvid'])
        if not unique: return messagebox.showwarning("提示", "请先选择视频")
        path = filedialog.askdirectory()
        if not path: return
        try: speed = int(self.speed_entry.get())
        except: speed = 0
        q = int(self.quality_combo_dl.get())
        audio_only = self.audio_only_mode.get()
        self.btn_start.configure(state="disabled", text="运行中...")
        self.btn_pause.configure(state="normal", text="暂停"); self.btn_cancel.configure(state="normal")
        self.pb_total.set(0)
        def progress(percent, text, is_switch, current_idx=0, total_cnt=1):
            if percent == -1: self.after(0, self.on_finish); return
            if is_switch:
                disp_idx = min(current_idx + 1, total_cnt)
                self.lbl_total_progress.configure(text=f"Total: {disp_idx}/{total_cnt} | {text}")
                self.pb_total.set(current_idx / total_cnt)
                self.log(f"开始: {text}")
            else:
                self.pb_file.set(percent); self.lbl_status_text.configure(text=text)
        def fail_cb(item): self.log(f"❌ 失败: {item['title']}")
        def log_proxy(msg): self.log(msg)
        WbiSigner.get_wbi_keys(self.mgr.session)
        self.worker = DownloadWorker(unique, path, speed, q, progress, lambda b: self.mgr.history.add(b), fail_cb, self.mgr.session, self.mgr.get_netscape_cookie_path, log_proxy, audio_only)
        threading.Thread(target=self.worker.run, daemon=True).start()
    def on_finish(self):
        self.pb_total.set(1.0); self.pb_file.set(1.0)
        self.lbl_total_progress.configure(text="✅ 任务完成"); self.lbl_status_text.configure(text="Ready")
        self.mgr.save_data(); self.selected_indices.clear()
        self.btn_start.configure(state="normal", text="🚀 启动"); self.btn_pause.configure(state="disabled"); self.btn_cancel.configure(state="disabled")
        self.refresh_list(); self.draw_stats(); self.log("所有任务执行完毕")
    def open_date_picker(self): pass

if __name__ == "__main__":
    from manager import BiliManager
    bili_manager = BiliManager()
    app = App(bili_manager)
    app.mainloop()
