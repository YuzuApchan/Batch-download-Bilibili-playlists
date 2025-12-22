# worker.py
import threading
import time
import shutil
import os
import random
import re
import yt_dlp
from config import NETSCAPE_TEMP
from utils import BiliResolver

class DownloadWorker:
    def __init__(self, items, save_dir, speed_limit, quality, progress_cb, history_cb, fail_cb, session, cookie_gen, log_cb, is_audio_only):
        self.items = items
        self.save_dir = save_dir
        self.speed_limit = speed_limit
        self.quality = quality
        self.progress_cb = progress_cb
        self.history_cb = history_cb
        self.fail_cb = fail_cb
        self.session = session
        self.cookie_gen = cookie_gen 
        self.log_cb = log_cb
        self.is_audio_only = is_audio_only
        self.is_paused = False
        self.is_cancelled = False
        self.has_aria2 = shutil.which('aria2c') is not None or os.path.exists(os.path.join(os.getcwd(), 'aria2c.exe'))

    def format_speed(self, bytes_per_sec):
        if not bytes_per_sec: return "0 KB/s"
        if bytes_per_sec > 1024 * 1024: return f"{bytes_per_sec/1024/1024:.2f} MB/s"
        return f"{bytes_per_sec/1024:.2f} KB/s"

    def progress_hook(self, d):
        while self.is_paused and not self.is_cancelled: time.sleep(0.5)
        if self.is_cancelled: raise Exception("USER_CANCEL")
        
        if d['status'] == 'downloading':
            try:
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                dl_bytes = d.get('downloaded_bytes', 0)
                p = dl_bytes / total
                eta = d.get('eta')
                
                if self.has_aria2: status_text = "🚀 Aria2 极速下载中..."
                else:
                    eta_str = f"(剩余 {eta}s)" if eta else ""
                    status_text = f"📥 下载进行中 {eta_str}"
                
                self.progress_cb(p, status_text, False)
            except: pass
        elif d['status'] == 'finished':
            msg = "⚙️ 正在转码 MP3..." if self.is_audio_only else "⚙️ 正在合成 MP4..."
            self.progress_cb(1.0, msg, False)
            self.log_cb(f">>> {msg}")

    def run(self):
        total_videos = len(self.items)
        cookie_file = self.cookie_gen() 

        for i, item in enumerate(self.items):
            if self.is_cancelled: break
            self.log_cb(f"[{i+1}/{total_videos}] 开始处理: {item['title']}")
            self.progress_cb(0, f"处理中 ({i+1}/{total_videos}): {item['title'][:15]}...", True, i, total_videos)
            
            if i > 0: time.sleep(0.5)

            rate = self.speed_limit * 1024 if self.speed_limit > 0 else None
            success = False
            pp_args = ['-threads', '0', '-preset', 'veryfast']

            try:
                if self.is_audio_only:
                    format_spec = "bestaudio/best"
                    postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
                    merge_format = None
                else:
                    format_spec = f"bestvideo[height<={self.quality}]+bestaudio/best[height<={self.quality}]/best"
                    postprocessors = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]
                    merge_format = 'mp4'

                opts = {
                    'outtmpl': f'{self.save_dir}/%(title)s.%(ext)s',
                    'ratelimit': rate,
                    'progress_hooks': [self.progress_hook],
                    'playlist_items': '1', 
                    'format': format_spec,
                    'cookiefile': cookie_file,
                    'force_ipv4': True,
                    'extractor_args': {'bilibili': {'player_client': ['android', 'ios', 'web']}}, 
                    'quiet': True,
                    'no_warnings': True,
                    'retries': 20,
                    'fragment_retries': 20,
                    'socket_timeout': 15,
                    'concurrent_fragment_downloads': 32, 
                    'http_chunk_size': 33554432,
                    'postprocessor_args': {'ffmpeg': pp_args},
                }
                
                if merge_format: opts['merge_output_format'] = merge_format
                opts['postprocessors'] = postprocessors

                if self.has_aria2:
                    opts.update({
                        'external_downloader': 'aria2c',
                        'external_downloader_args': {
                            'aria2c': [
                                '-x', '16', '-s', '16', '-j', '16', '-k', '4M', 
                                '--min-split-size=4M', '--file-allocation=falloc', 
                                '--lowest-speed-limit=50K', '--disk-cache=64M',
                            ]
                        }
                    })
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([f"https://www.bilibili.com/video/{item['bvid']}"])
                success = True
                self.log_cb(f"✅ 下载成功: {item['title']}")

            except Exception as e:
                err = str(e)
                if "extract initial state" in err or "festival" in err or "DownloadError" in err:
                    self.log_cb("⚠️ 尝试 API 直链模式...")
                    stream_url, real_title, _ = BiliResolver.get_video_stream(item['bvid'], self.session)
                    if stream_url:
                        try:
                            safe_title = re.sub(r'[\\/*?:"<>|]', "", real_title or item['title'])
                            ext = 'mp3' if self.is_audio_only else '%(ext)s'
                            direct_opts = {
                                'outtmpl': f'{self.save_dir}/{safe_title}.{ext}',
                                'ratelimit': rate,
                                'progress_hooks': [self.progress_hook],
                                'http_headers': {'Referer': 'https://www.bilibili.com/'},
                                'quiet': True,
                                'postprocessors': postprocessors,
                                'postprocessor_args': {'ffmpeg': pp_args}
                            }
                            if self.has_aria2:
                                direct_opts.update({'external_downloader': 'aria2c', 'external_downloader_args': {'aria2c': ['-x','16','-s','16','-k','4M']}})
                            
                            with yt_dlp.YoutubeDL(direct_opts) as ydl:
                                ydl.download([stream_url])
                            success = True
                            self.log_cb(f"✅ 直链下载成功: {item['title']}")
                        except Exception as e2: 
                            self.log_cb(f"❌ 直链失败: {e2}")
            
            if success: self.history_cb(item['bvid'])
            else: self.fail_cb(item)

        self.progress_cb(-1, "DONE", False)
