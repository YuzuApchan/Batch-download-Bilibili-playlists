import threading
import time
import shutil
import os
import random
import re
import subprocess
import traceback  # 用于捕获详细错误
from concurrent.futures import ThreadPoolExecutor, as_completed
import yt_dlp
from yt_dlp.utils import DownloadError
from config import ERROR_LOG
from utils import BiliApiError, BiliResolver

_PERF_FILE_LOCK = threading.Lock()

class DownloadWorker:
    def __init__(self, items, save_dir, speed_limit, quality, progress_cb, history_cb, fail_cb, session, cookie_gen, log_cb, is_audio_only, dl_all_parts=False, parallelism=1, _shared_control=None, _cleanup_cookie_file=True):
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
        self.dl_all_parts = dl_all_parts
        self.parallelism = max(1, min(8, int(parallelism or 1)))
        self._cleanup_cookie_file = _cleanup_cookie_file

        self.is_paused = False
        self.is_cancelled = False
        self._current_process = None
        self._process_lock = threading.Lock()
        self._children = []
        self._children_lock = threading.Lock()
        self._yt_dlp_blocked_until = 0
        self._yt_dlp_412_count = 0
        self._api_blocked_until = 0
        self._shared_control = _shared_control or {
            'api_lock': threading.Lock(),
            'state_lock': threading.Lock(),
            'api_blocked_until': 0,
            'last_api_request_at': 0,
            'session_id': f"{time.strftime('%Y%m%d-%H%M%S')}-{random.randint(1000, 9999)}",
            'perf_lock': threading.Lock(),
            'perf_totals': {},
        }
        self._shared_control.setdefault(
            'session_id', f"{time.strftime('%Y%m%d-%H%M%S')}-{random.randint(1000, 9999)}"
        )
        self._shared_control.setdefault('perf_lock', threading.Lock())
        self._shared_control.setdefault('perf_totals', {})

        # 进度更新节流
        self._last_progress_time = 0
        self._progress_interval = 0.05  # 最少间隔 50ms，进度条更顺滑
        self._download_hook_started = {}
        self._active_item_bvid = ''

        # 环境检测
        self.aria2_path = None
        self.check_environment()

    def check_environment(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        configured_ffmpeg = os.environ.get("BILI_FFMPEG_PATH", "")
        configured_aria2 = os.environ.get("BILI_ARIA2_PATH", "")
        local_ffmpeg = configured_ffmpeg if configured_ffmpeg and os.path.exists(configured_ffmpeg) else os.path.join(base_dir, 'ffmpeg.exe')
        local_aria2 = configured_aria2 if configured_aria2 and os.path.exists(configured_aria2) else os.path.join(base_dir, 'aria2c.exe')

        if os.path.exists(local_ffmpeg):
            if base_dir not in os.environ["PATH"]:
                os.environ["PATH"] += os.pathsep + base_dir
            self.has_ffmpeg = True
        else:
            self.has_ffmpeg = shutil.which('ffmpeg') is not None

        if os.path.exists(local_aria2):
            if base_dir not in os.environ["PATH"]:
                os.environ["PATH"] += os.pathsep + base_dir
            self.aria2_path = local_aria2 if configured_aria2 else 'aria2c'
            self.has_aria2 = True
        else:
            self.aria2_path = shutil.which('aria2c')
            self.has_aria2 = self.aria2_path is not None

    def _hidden_process_kwargs(self):
        if os.name != 'nt':
            return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {
            'creationflags': subprocess.CREATE_NO_WINDOW,
            'startupinfo': startupinfo,
        }

    def _perf_log(self, event, **fields):
        def render(value):
            if isinstance(value, float):
                return f"{value:.3f}"
            return str(value).replace('\r', ' ').replace('\n', ' ').replace('|', '/')

        payload = {
            'session': self._shared_control.get('session_id', '-'),
            'event': event,
            **fields,
        }
        line = '[PERF] ' + ' | '.join(f"{key}={render(value)}" for key, value in payload.items())
        self.log_cb(line)

        perf_path = self._perf_log_path()
        file_line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(perf_path)), exist_ok=True)
            with _PERF_FILE_LOCK:
                with open(perf_path, 'a', encoding='utf-8') as perf_file:
                    perf_file.write(file_line)
        except Exception:
            pass

        metric = None
        value = 0.0
        if event == 'api_resolve':
            metric, value = 'api_s', fields.get('total_s', 0)
        elif event == 'ffmpeg_done':
            metric, value = 'stream_ffmpeg_s', fields.get('elapsed_s', 0)
        elif event == 'ffprobe':
            metric, value = 'verify_s', fields.get('elapsed_s', 0)
        elif event == 'transfer_done':
            metric, value = 'fallback_transfer_s', fields.get('elapsed_s', 0)
        elif event == 'wait':
            metric, value = 'intentional_wait_s', fields.get('seconds', 0)
        elif event == 'process_done' and str(fields.get('tool', '')).lower().startswith('ffmpeg'):
            metric, value = 'fallback_process_s', fields.get('elapsed_s', 0)
        elif event == 'item_done' and fields.get('mode') == 'fallback':
            metric, value = 'fallback_item_s', fields.get('elapsed_s', 0)
        if metric:
            with self._shared_control['perf_lock']:
                totals = self._shared_control['perf_totals']
                totals[metric] = totals.get(metric, 0.0) + float(value or 0)

    def _perf_log_path(self):
        error_log = os.environ.get("BILI_ERROR_LOG") or ERROR_LOG
        return os.environ.get("BILI_PERF_LOG") or os.path.join(
            os.path.dirname(os.path.abspath(error_log)), 'performance_log.txt'
        )

    def _perf_summary(self, wall_s):
        with self._shared_control['perf_lock']:
            totals = dict(self._shared_control['perf_totals'])
        fallback_transfer = totals.get('fallback_transfer_s', 0)
        fallback_total = totals.get('fallback_item_s', 0)
        categories = {
            'api': totals.get('api_s', 0),
            'intentional_wait': totals.get('intentional_wait_s', 0),
            'stream_ffmpeg': totals.get('stream_ffmpeg_s', 0),
            'fallback_transfer': fallback_transfer,
            'fallback_non_transfer': max(0, fallback_total - fallback_transfer),
            'verify': totals.get('verify_s', 0),
        }
        dominant = max(categories, key=categories.get) if any(categories.values()) else 'unknown'
        self._perf_log(
            'bottleneck_summary', wall_s=wall_s, dominant=dominant,
            fallback_process_s=totals.get('fallback_process_s', 0),
            **{f'{key}_s': value for key, value in categories.items()},
        )

    def _run_hidden(self, args, **kwargs):
        started = time.perf_counter()
        tool = os.path.basename(str(args[0])) if args else 'unknown'
        process_kwargs = self._hidden_process_kwargs()
        process_kwargs.update(kwargs)
        try:
            result = subprocess.run(args, **process_kwargs)
            self._perf_log(
                'process_done', tool=tool, elapsed_s=time.perf_counter() - started,
                returncode=result.returncode,
            )
            return result
        except Exception as exc:
            self._perf_log(
                'process_error', tool=tool, elapsed_s=time.perf_counter() - started,
                error=str(exc),
            )
            raise

    def _popen_hidden(self, args, **kwargs):
        process_kwargs = self._hidden_process_kwargs()
        process_kwargs.update(kwargs)
        return subprocess.Popen(args, **process_kwargs)

    def _set_current_process(self, process):
        with self._process_lock:
            self._current_process = process

    def _clear_current_process(self, process):
        with self._process_lock:
            if self._current_process is process:
                self._current_process = None

    def _terminate_current_process(self):
        with self._process_lock:
            process = self._current_process
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def cancel(self):
        self.is_cancelled = True
        self._terminate_current_process()
        with self._children_lock:
            children = list(self._children)
        for child in children:
            child.cancel()

    def _wait_before_next_stream(self):
        announced = False
        while self.is_paused and not self.is_cancelled:
            if not announced:
                self.progress_cb(0, "⏸️ 当前视频已完成，等待继续...", False)
                announced = True
            time.sleep(0.25)
        return not self.is_cancelled

    def _interruptible_sleep(self, seconds):
        deadline = time.time() + max(0, seconds)
        while time.time() < deadline:
            if self.is_cancelled:
                return False
            time.sleep(min(0.25, max(0, deadline - time.time())))
        return True

    def _is_api_rate_limit(self, exc):
        return isinstance(exc, BiliApiError) and (
            exc.status_code in (412, 429)
            or exc.api_code in (-412, -429, 412, 429)
        )

    def _get_api_blocked_until(self):
        with self._shared_control['state_lock']:
            return self._shared_control['api_blocked_until']

    def _set_api_blocked_until(self, timestamp):
        with self._shared_control['state_lock']:
            self._shared_control['api_blocked_until'] = max(
                self._shared_control['api_blocked_until'], timestamp
            )
            self._api_blocked_until = self._shared_control['api_blocked_until']

    def _resolve_with_backoff(self, callback, label, max_attempts=4):
        resolve_started = time.perf_counter()
        for attempt in range(max_attempts):
            try:
                cooldown = self._get_api_blocked_until() - time.time()
                if cooldown > 0:
                    self.log_cb(f"🧊 API 风控冷却中，等待 {cooldown:.0f} 秒")
                    if not self._interruptible_sleep(cooldown):
                        raise RuntimeError("USER_CANCEL")
                with self._shared_control['api_lock']:
                    cooldown = self._get_api_blocked_until() - time.time()
                    if cooldown > 0:
                        self.log_cb(f"🧊 API 风控冷却中，等待 {cooldown:.0f} 秒")
                        if not self._interruptible_sleep(cooldown):
                            raise RuntimeError("USER_CANCEL")
                    with self._shared_control['state_lock']:
                        since_last = time.time() - self._shared_control['last_api_request_at']
                    spacing = random.uniform(0.35, 0.9)
                    if since_last < spacing:
                        if not self._interruptible_sleep(spacing - since_last):
                            raise RuntimeError("USER_CANCEL")
                    try:
                        request_started = time.perf_counter()
                        result = callback()
                        request_elapsed = time.perf_counter() - request_started
                    finally:
                        with self._shared_control['state_lock']:
                            self._shared_control['last_api_request_at'] = time.time()
                self._perf_log(
                    'api_resolve', label=label, attempt=attempt + 1,
                    request_s=request_elapsed,
                    total_s=time.perf_counter() - resolve_started,
                )
                return result
            except BiliApiError as exc:
                is_limited = self._is_api_rate_limit(exc)
                if not is_limited or attempt + 1 >= max_attempts:
                    if is_limited:
                        self._set_api_blocked_until(time.time() + 90)
                        self.log_cb("🧊 风控响应持续，暂停 API 请求 90 秒")
                    self._perf_log(
                        'api_error', label=label, attempt=attempt + 1,
                        limited=is_limited, error=str(exc),
                        total_s=time.perf_counter() - resolve_started,
                    )
                    raise
                delay = min(60, 3 * (2 ** attempt)) + random.uniform(0.5, 2.5)
                self._perf_log(
                    'api_backoff', label=label, attempt=attempt + 1,
                    delay_s=delay, error=str(exc),
                )
                self.log_cb(
                    f"🧊 {label}触发风控 ({exc})，{delay:.1f} 秒后重试 "
                    f"({attempt + 2}/{max_attempts})"
                )
                if not self._interruptible_sleep(delay):
                    raise RuntimeError("USER_CANCEL")

    def clean_filename(self, name, limit=80):
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = "".join(x for x in name if x.isprintable())
        if len(name) > limit:
            name = name[:limit]
        return name.strip()

    def log_error(self, title, error_obj, context=""):
        """记录详细错误日志到本地文件"""
        tb_str = traceback.format_exc()
        err_msg = (
            f"\n{'='*30}\n"
            f"[时间]: {time.ctime()}\n"
            f"[任务]: {title}\n"
            f"[阶段]: {context}\n"
            f"[错误类型]: {type(error_obj).__name__}\n"
            f"[错误信息]: {str(error_obj)}\n"
            f"[堆栈追踪]:\n{tb_str}\n"
            f"{'='*30}\n"
        )

        # 1. 界面简略提示
        self.log_cb(f"❌ {context} 错误: {str(error_obj)}")
        self.log_cb(f"👉 详情已写入 error_log.txt")

        # 2. 写入文件
        try:
            with open(os.environ.get("BILI_ERROR_LOG") or ERROR_LOG, "a", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception as e:
            print(f"写入日志失败: {e}")

    def _is_yt_dlp_412(self, error_obj):
        text = str(error_obj).lower()
        return "http error 412" in text or "precondition failed" in text

    def _cooldown_yt_dlp(self):
        self._yt_dlp_412_count += 1
        cooldown = min(600, 90 * self._yt_dlp_412_count)
        self._yt_dlp_blocked_until = time.time() + cooldown
        self.log_cb(f"⚠️ yt-dlp metadata 被 B站返回 412，冷却 {cooldown} 秒；后续优先使用 API 直链模式")

    def progress_hook(self, d):
        if self.is_cancelled:
            raise Exception("USER_CANCEL")

        if d['status'] == 'downloading':
            filename = d.get('filename') or d.get('tmpfilename') or 'unknown'
            self._download_hook_started.setdefault(filename, time.perf_counter())
            # 节流：限制更新频率
            current_time = time.time()
            if current_time - self._last_progress_time < self._progress_interval:
                return
            self._last_progress_time = current_time

            try:
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                p = d.get('downloaded_bytes', 0) / total
                info = d.get('info_dict') or {}
                try:
                    part_index = int(info.get('playlist_index') or 1)
                    part_count = int(
                        info.get('n_entries') or info.get('playlist_count') or 1
                    )
                except (TypeError, ValueError):
                    part_index, part_count = 1, 1
                if self.dl_all_parts and part_count > 1:
                    p = (max(1, part_index) - 1 + p) / part_count
                msg = f"🚀 下载中" if self.has_aria2 else f"📥 {int(p*100)}%"
                self.progress_cb(p, msg, False)
            except:
                pass
        elif d['status'] == 'finished':
            filename = d.get('filename') or d.get('tmpfilename') or 'unknown'
            started = self._download_hook_started.pop(filename, None)
            elapsed = time.perf_counter() - started if started else 0
            downloaded = d.get('downloaded_bytes') or d.get('total_bytes') or 0
            self._perf_log(
                'transfer_done', bvid=self._active_item_bvid or '-',
                file=os.path.basename(filename), elapsed_s=elapsed,
                size_mb=downloaded / (1024 * 1024),
                throughput_mbps=(downloaded * 8 / elapsed / 1000000) if elapsed else 0,
            )
            self.progress_cb(1.0, "⚙️ 正在处理...", False)

    class MyLogger:
        def __init__(self, log_callback):
            self.log_cb = log_callback

        def debug(self, msg):
            pass

        def warning(self, msg):
            self.log_cb(f"⚠️ [内核警告]: {msg}")

        def error(self, msg):
            self.log_cb(f"❌ [内核错误]: {msg}")

    def _verify_audio_stream(self, file_path):
        """验证文件是否包含音频流"""
        started = time.perf_counter()
        try:
            result = self._run_hidden(
                [self._find_local_ffprobe(), '-v', 'quiet', '-select_streams', 'a',
                 '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', file_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            valid = 'audio' in result.stdout.lower()
            self._perf_log(
                'ffprobe', file=os.path.basename(file_path), valid=valid,
                elapsed_s=time.perf_counter() - started,
            )
            return valid
        except Exception as exc:
            self._perf_log(
                'ffprobe_error', file=os.path.basename(file_path), error=str(exc),
                elapsed_s=time.perf_counter() - started,
            )
            # 如果 ffprobe 失败，假设文件有效
            return True

    def _find_local_ffmpeg(self):
        """优先使用项目内 ffmpeg，其次使用系统 ffmpeg"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        configured = os.environ.get("BILI_FFMPEG_PATH", "")
        if configured and os.path.exists(configured):
            return configured
        local_ffmpeg = os.path.join(base_dir, 'ffmpeg.exe')
        if os.path.exists(local_ffmpeg):
            return local_ffmpeg
        ffmpeg_cmd = shutil.which('ffmpeg')
        return ffmpeg_cmd if ffmpeg_cmd else 'ffmpeg'

    def _find_local_ffprobe(self):
        """优先使用项目内 ffprobe，其次使用系统 ffprobe"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        configured = os.environ.get("BILI_FFPROBE_PATH", "")
        if configured and os.path.exists(configured):
            return configured
        local_ffprobe = os.path.join(base_dir, 'ffprobe.exe')
        if os.path.exists(local_ffprobe):
            return local_ffprobe
        ffprobe_cmd = shutil.which('ffprobe')
        return ffprobe_cmd if ffprobe_cmd else 'ffprobe'

    def _find_recent_media_file(self, since_ts, exts=('.mp4', '.mkv', '.webm', '.flv', '.m4a', '.mp3')):
        try:
            candidates = []
            for name in os.listdir(self.save_dir):
                path = os.path.join(self.save_dir, name)
                if not os.path.isfile(path):
                    continue
                if not name.lower().endswith(exts):
                    continue
                if os.path.getmtime(path) >= since_ts - 2:
                    candidates.append(path)
            return max(candidates, key=os.path.getmtime) if candidates else None
        except Exception:
            return None

    def _find_output_for_item(self, item, since_ts):
        latest = self._find_recent_media_file(since_ts)
        try:
            title = self.clean_filename(str(item.get('title') or ''), 80).lower()
            bvid = str(item.get('bvid') or '').lower()
            candidates = []
            for name in os.listdir(self.save_dir):
                path = os.path.join(self.save_dir, name)
                if not os.path.isfile(path):
                    continue
                lower = name.lower()
                if not lower.endswith(('.mp4', '.mkv', '.webm', '.flv', '.m4a', '.mp3')):
                    continue
                if os.path.getmtime(path) < since_ts - 10:
                    continue
                score = 0
                if bvid and bvid in lower:
                    score += 3
                if title and (title[:24] in lower or lower.startswith(title[:24])):
                    score += 2
                if score:
                    candidates.append((score, os.path.getmtime(path), path))
            if candidates:
                return max(candidates)[2]
        except Exception:
            pass
        return latest

    def _verify_recent_video_output(self, item, since_ts):
        if self.is_audio_only:
            return True
        latest_file = self._find_recent_media_file(since_ts, exts=('.mp4', '.mkv', '.webm', '.flv'))
        if not latest_file:
            self.log_cb("⚠️ 未定位到最新输出文件，跳过音频验证")
            return True
        if self._verify_audio_stream(latest_file):
            return True
        self.log_cb("⚠️ 输出文件缺少音频，删除后切换 API 直链重试")
        try:
            os.remove(latest_file)
        except Exception:
            pass
        return False

    def _notify_history(self, item, file_path=None):
        bvid = item.get('bvid') if isinstance(item, dict) else item
        try:
            self.history_cb(bvid, item, file_path or "")
        except TypeError:
            self.history_cb(bvid)
        except Exception as exc:
            self.log_error(str(item.get('title') if isinstance(item, dict) else bvid), exc, context="history_cb")

    def _stream_headers(self):
        user_agent = self.session.headers.get('User-Agent', 'Mozilla/5.0')
        cookie = '; '.join(
            f"{cookie.name}={cookie.value}" for cookie in self.session.cookies
        )
        lines = [
            'Referer: https://www.bilibili.com/',
            f'User-Agent: {user_agent}',
        ]
        if cookie:
            lines.append(f'Cookie: {cookie}')
        return '\r\n'.join(lines) + '\r\n'

    def _stream_output_path(self, video_title, stream, total_parts):
        bvid = self.clean_filename(stream.get('bvid') or '', 24)
        identity = f" [{bvid}]" if bvid else ''
        if total_parts > 1:
            part_index = int(stream.get('part_index') or 1)
            part_title = self.clean_filename(stream.get('part_title') or f"P{part_index}", 50)
            name = self.clean_filename(
                f"{video_title}{identity} - P{part_index:02d} {part_title}", 110
            )
        else:
            name = self.clean_filename(f"{video_title}{identity}", 110)
        ext = 'm4a' if self.is_audio_only else 'mp4'
        return os.path.join(self.save_dir, f"{name}.{ext}")

    def _remove_if_exists(self, path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _staging_output_path(self, output_path):
        stem, ext = os.path.splitext(output_path)
        while True:
            candidate = (
                f"{stem}.download-{threading.get_ident()}-"
                f"{random.randint(100000, 999999)}{ext}"
            )
            if not os.path.exists(candidate):
                return candidate

    def _backup_output_path(self, output_path):
        while True:
            candidate = f"{output_path}.backup-{random.randint(100000, 999999)}"
            if not os.path.exists(candidate):
                return candidate

    def _commit_staged_outputs(self, staged_outputs):
        """Atomically replace completed outputs and restore old files on failure."""
        committed = []
        backups = []
        try:
            for staged_path, output_path in staged_outputs:
                backup_path = None
                if os.path.exists(output_path):
                    backup_path = self._backup_output_path(output_path)
                    os.replace(output_path, backup_path)
                try:
                    os.replace(staged_path, output_path)
                except Exception:
                    if backup_path and os.path.exists(backup_path):
                        os.replace(backup_path, output_path)
                    raise
                committed.append(output_path)
                backups.append((output_path, backup_path))
        except Exception:
            for output_path, backup_path in reversed(backups):
                self._remove_if_exists(output_path)
                if backup_path and os.path.exists(backup_path):
                    os.replace(backup_path, output_path)
            raise

        for _, backup_path in backups:
            self._remove_if_exists(backup_path)
        return committed

    def _run_streaming_ffmpeg(self, stream, output_path, transcode_audio=False):
        if self.is_cancelled:
            raise RuntimeError("USER_CANCEL")

        started = time.perf_counter()
        temp_path = output_path.rsplit('.', 1)[0] + '.part.' + output_path.rsplit('.', 1)[1]
        self._remove_if_exists(temp_path)
        headers = self._stream_headers()
        input_opts = [
            '-headers', headers,
            '-rw_timeout', '15000000',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
        ]
        command = [
            self._find_local_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
            '-nostats', '-progress', 'pipe:1',
        ]

        if stream.get('type') == 'dash':
            if self.is_audio_only:
                command += input_opts + ['-i', stream['audio_url']]
                command += ['-map', '0:a:0', '-vn']
                if transcode_audio:
                    command += ['-c:a', 'aac', '-b:a', '192k']
                else:
                    command += ['-c:a', 'copy']
            else:
                command += input_opts + ['-i', stream['video_url']]
                command += input_opts + ['-i', stream['audio_url']]
                command += ['-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy']
                if transcode_audio:
                    command += ['-c:a', 'aac', '-b:a', '192k']
                else:
                    command += ['-c:a', 'copy']
        elif stream.get('type') == 'durl':
            urls = stream.get('urls') or [stream.get('url')]
            urls = [url for url in urls if url]
            if len(urls) != 1:
                raise RuntimeError("多段 DURL 暂不支持直接流式封装")
            command += input_opts + ['-i', urls[0]]
            if self.is_audio_only:
                command += ['-vn']
                if transcode_audio:
                    command += ['-c:a', 'aac', '-b:a', '192k']
                else:
                    command += ['-c:a', 'copy']
            else:
                command += ['-c', 'copy']
        else:
            raise RuntimeError("未知流类型")

        if not self.is_audio_only:
            command += ['-movflags', '+faststart']
        command.append(temp_path)

        duration = float(stream.get('duration') or 0)
        self._perf_log(
            'ffmpeg_start', bvid=stream.get('bvid') or '-',
            part=stream.get('part_index') or 1, stream_type=stream.get('type'),
            transcode_audio=transcode_audio, media_duration_s=duration,
            output=os.path.basename(output_path),
        )
        output_lines = []
        process = None
        try:
            process = self._popen_hidden(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
            )
            self._set_current_process(process)
            for raw_line in process.stdout or []:
                line = raw_line.strip()
                if line:
                    output_lines.append(line)
                    if len(output_lines) > 30:
                        output_lines.pop(0)
                if self.is_cancelled:
                    self._terminate_current_process()
                    raise RuntimeError("USER_CANCEL")
                if duration > 0 and line.startswith(('out_time_ms=', 'out_time_us=')):
                    try:
                        elapsed_us = int(line.split('=', 1)[1])
                        ratio = max(0.0, min(0.99, elapsed_us / (duration * 1000000)))
                        part_no = int(stream.get('_progress_part_no') or 1)
                        part_count = int(stream.get('_progress_part_count') or 1)
                        item_ratio = (part_no - 1 + ratio) / max(1, part_count)
                        self.progress_cb(
                            item_ratio,
                            f"🔄 流式下载并合并 {int(item_ratio * 100)}%",
                            False,
                        )
                    except (TypeError, ValueError):
                        pass
            return_code = process.wait()
            if self.is_cancelled:
                raise RuntimeError("USER_CANCEL")
            if return_code != 0:
                detail = ' | '.join(output_lines[-6:])
                raise RuntimeError(detail or f"FFmpeg退出码 {return_code}")
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
                raise RuntimeError("FFmpeg未生成有效输出文件")
            os.replace(temp_path, output_path)
            elapsed = time.perf_counter() - started
            size_bytes = os.path.getsize(output_path)
            self._perf_log(
                'ffmpeg_done', bvid=stream.get('bvid') or '-',
                part=stream.get('part_index') or 1, elapsed_s=elapsed,
                media_duration_s=duration,
                realtime_x=(duration / elapsed) if duration and elapsed else 0,
                size_mb=size_bytes / (1024 * 1024),
                throughput_mbps=(size_bytes * 8 / elapsed / 1000000) if elapsed else 0,
                transcode_audio=transcode_audio,
            )
            return output_path
        except Exception as exc:
            self._perf_log(
                'ffmpeg_error', bvid=stream.get('bvid') or '-',
                part=stream.get('part_index') or 1,
                elapsed_s=time.perf_counter() - started,
                transcode_audio=transcode_audio, error=str(exc),
            )
            raise
        finally:
            if process is not None:
                self._clear_current_process(process)
            self._remove_if_exists(temp_path)

    def _download_streaming_item(self, item):
        item_started = time.perf_counter()
        staged_outputs = []
        pages, video_title = self._resolve_with_backoff(
            lambda: BiliResolver.get_video_pages(
                item['bvid'], self.session, self.dl_all_parts, raise_errors=True
            ),
            "获取分P信息",
        )
        if not pages:
            raise RuntimeError("API未解析到可用分P")

        total_parts = len(pages)
        self._perf_log(
            'stream_manifest', bvid=item.get('bvid'), parts=total_parts,
            elapsed_s=time.perf_counter() - item_started,
        )
        try:
            for part_no, page in enumerate(pages, 1):
                if self.is_cancelled:
                    raise RuntimeError("USER_CANCEL")
                if part_no > 1:
                    delay = random.uniform(0.8, 2.0)
                    self.log_cb(f"🌿 分P请求间隔 {delay:.1f} 秒")
                    self._perf_log(
                        'wait', bvid=item.get('bvid'), kind='part_gap',
                        part=part_no, seconds=delay,
                    )
                    if not self._interruptible_sleep(delay):
                        raise RuntimeError("USER_CANCEL")

                last_error = None
                for attempt in range(2):
                    if attempt:
                        self.log_cb("🔁 刷新当前分P流地址并重试流式处理...")
                        retry_delay = random.uniform(1.2, 2.4)
                        self._perf_log(
                            'wait', bvid=item.get('bvid'), kind='stream_retry',
                            part=part_no, seconds=retry_delay,
                        )
                        if not self._interruptible_sleep(retry_delay):
                            raise RuntimeError("USER_CANCEL")
                    try:
                        stream = self._resolve_with_backoff(
                            lambda page=page: BiliResolver.get_page_stream(
                                item['bvid'], page, self.session, self.quality,
                                video_title, raise_errors=True,
                            ),
                            f"解析P{part_no}",
                        )
                        output_path = self._stream_output_path(
                            video_title, stream, total_parts
                        )
                        staging_path = self._staging_output_path(output_path)
                        stream['_progress_part_no'] = part_no
                        stream['_progress_part_count'] = total_parts
                        if total_parts > 1:
                            self.log_cb(
                                f"🔄 流式处理 P{part_no}/{total_parts}: "
                                f"{stream.get('part_title') or ''}"
                            )
                        else:
                            self.log_cb("🔄 启动流式下载并合并...")
                        completed_path = self._run_streaming_ffmpeg(
                            stream, staging_path, transcode_audio=bool(attempt)
                        )
                        if not self.is_audio_only and not self._verify_audio_stream(completed_path):
                            self._remove_if_exists(completed_path)
                            raise RuntimeError("流式输出缺少音频轨道")
                        staged_outputs.append((completed_path, output_path))
                        break
                    except Exception as exc:
                        last_error = exc
                        if str(exc) == "USER_CANCEL":
                            raise
                        if self._is_api_rate_limit(exc):
                            raise
                        self.log_cb(f"⚠️ P{part_no} 流式处理失败: {str(exc)[:180]}")
                else:
                    raise last_error or RuntimeError(f"P{part_no} 流式处理失败")
            created_outputs = self._commit_staged_outputs(staged_outputs)
            staged_outputs.clear()
            total_size = sum(
                os.path.getsize(path) for path in created_outputs if os.path.exists(path)
            )
            self._perf_log(
                'stream_item_done', bvid=item.get('bvid'), parts=total_parts,
                elapsed_s=time.perf_counter() - item_started,
                total_size_mb=total_size / (1024 * 1024),
            )
            return created_outputs
        except Exception as exc:
            self._perf_log(
                'stream_item_error', bvid=item.get('bvid'), parts=total_parts,
                elapsed_s=time.perf_counter() - item_started, error=str(exc),
            )
            raise
        finally:
            for staged_path, _ in staged_outputs:
                self._remove_if_exists(staged_path)

    def _run_parallel(self):
        total = len(self.items)
        batch_started = time.perf_counter()
        cookie_file = self.cookie_gen()
        state_lock = threading.Lock()
        callback_lock = threading.Lock()
        history_lock = threading.Lock()
        state = {
            'completed': 0,
            'active': 0,
            'ratios': {index: 0.0 for index in range(total)},
        }
        submitted_at = {}

        def emit_progress(index, title, percent, text, is_switch, current_idx=0, total_cnt=1):
            if percent == -1:
                return
            with state_lock:
                if percent >= 0:
                    state['ratios'][index] = max(
                        state['ratios'][index], max(0.0, min(1.0, percent))
                    )
                aggregate = sum(state['ratios'].values()) / total
                completed = state['completed']
                active = state['active']
            label = f"并行 {active}/{self.parallelism} | {title}"
            with callback_lock:
                self.progress_cb(
                    aggregate, label, True, completed, total
                )
                if not is_switch:
                    self.progress_cb(
                        percent, text, False, index, total, index, title, False
                    )

        def run_one(index, item):
            if self.is_cancelled:
                return
            while self.is_paused and not self.is_cancelled:
                self._interruptible_sleep(0.25)
            if self.is_cancelled:
                return

            item_started = time.perf_counter()
            title = self.clean_filename(item.get('title') or item.get('bvid') or '', 24)
            with callback_lock:
                self.progress_cb(
                    0, "准备下载", False, index, total, index, title, False
                )
            self._perf_log(
                'parallel_item_start', index=index + 1, bvid=item.get('bvid'),
                queue_s=item_started - submitted_at.get(index, batch_started),
            )
            with state_lock:
                state['active'] += 1

            def child_history(*args, **kwargs):
                with history_lock:
                    self.history_cb(*args, **kwargs)

            child = DownloadWorker(
                [item], self.save_dir, self.speed_limit, self.quality,
                lambda p, t, s, ci=0, tc=1: emit_progress(index, title, p, t, s, ci, tc),
                child_history, self.fail_cb, self.session, lambda: cookie_file,
                lambda message: self.log_cb(f"[{title}] {message}"),
                self.is_audio_only, self.dl_all_parts, parallelism=1,
                _shared_control=self._shared_control, _cleanup_cookie_file=False,
            )
            with self._children_lock:
                self._children.append(child)
            if self.is_cancelled:
                child.cancel()
            try:
                child.run()
            finally:
                with self._children_lock:
                    if child in self._children:
                        self._children.remove(child)
                with state_lock:
                    state['active'] = max(0, state['active'] - 1)
                    state['completed'] += 1
                    state['ratios'][index] = 1.0
                    completed = state['completed']
                with callback_lock:
                    self.progress_cb(
                        1.0,
                        "已取消" if self.is_cancelled else "已完成",
                        False,
                        index,
                        total,
                        index,
                        title,
                        True,
                    )
                    self.progress_cb(
                        completed / total,
                        f"并行完成 {completed}/{total}",
                        True,
                        completed,
                        total,
                    )
                self._perf_log(
                    'parallel_item_done', index=index + 1, bvid=item.get('bvid'),
                    elapsed_s=time.perf_counter() - item_started,
                )

        try:
            self.log_cb(f"⚡ 已启用视频级并行：{self.parallelism} 个任务")
            self.log_cb(f"📊 性能日志: {self._perf_log_path()}")
            self._perf_log(
                'batch_start', items=total, parallelism=self.parallelism,
                quality=self.quality, audio_only=self.is_audio_only,
                all_parts=self.dl_all_parts,
            )
            with ThreadPoolExecutor(
                max_workers=self.parallelism, thread_name_prefix='bili-download'
            ) as executor:
                futures = []
                for index, item in enumerate(self.items):
                    submitted_at[index] = time.perf_counter()
                    futures.append(executor.submit(run_one, index, item))
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        self.log_error("并行下载任务", exc, context="并行调度")
        finally:
            elapsed = time.perf_counter() - batch_started
            with state_lock:
                completed = state['completed']
            self._perf_log(
                'batch_done', items=total, completed=completed,
                parallelism=self.parallelism, elapsed_s=elapsed,
                items_per_min=(completed * 60 / elapsed) if elapsed else 0,
                cancelled=self.is_cancelled,
            )
            if self._cleanup_cookie_file:
                self._perf_summary(elapsed)
            if self._cleanup_cookie_file and cookie_file and os.path.exists(cookie_file):
                try:
                    os.remove(cookie_file)
                except Exception:
                    pass
            self.progress_cb(-1, "DONE", False)

    def run(self):
        if self.parallelism > 1 and len(self.items) > 1:
            self._run_parallel()
            return

        batch_started = time.perf_counter()
        total_videos = len(self.items)
        processed_items = 0
        cookie_file = self.cookie_gen()
        current_ua = self.session.headers.get('User-Agent', 'Mozilla/5.0')
        self._perf_log(
            'batch_start', items=total_videos, parallelism=1,
            quality=self.quality, audio_only=self.is_audio_only,
            all_parts=self.dl_all_parts,
        )
        if self._cleanup_cookie_file:
            self.log_cb(f"📊 性能日志: {self._perf_log_path()}")

        if self.speed_limit > 0:
            self.log_cb("ℹ️ 流式模式暂不支持限速；仅兼容回退流程应用限速")

        # 策略判断：4K/2K 默认走 API；普通清晰度只在单个视频失败时 fallback。
        prefer_api_mode = (self.quality in ["4K", "2K"])

        # 画质参数 - 改进格式选择器，确保包含音频
        if self.quality == "4K":
            format_str = "bestvideo[height=2160]+bestaudio/bestvideo[height<=2160]+bestaudio"
        elif self.quality == "2K":
            format_str = "bestvideo[height=1440]+bestaudio/bestvideo[height<=1440]+bestaudio"
        elif self.quality == "1080":
            format_str = "bestvideo[height=1080]+bestaudio/bestvideo[height<=1080]+bestaudio"
        elif self.quality == "720":
            format_str = "bestvideo[height=720]+bestaudio/bestvideo[height<=720]+bestaudio"
        elif self.quality == "480":
            format_str = "bestvideo[height=480]+bestaudio/bestvideo[height<=480]+bestaudio"
        else:
            format_str = "bestvideo+bestaudio/best"

        try:
            for i, item in enumerate(self.items):
                if self.is_cancelled:
                    break

                if not self._wait_before_next_stream():
                    break

                cooldown = self._get_api_blocked_until() - time.time()
                if cooldown > 0:
                    self.log_cb(f"🧊 API 风控冷却中，等待 {cooldown:.0f} 秒")
                    if not self._interruptible_sleep(cooldown):
                        break

                safe_title = self.clean_filename(item['title'], 20)
                self.progress_cb(i / total_videos, safe_title, True, i, total_videos)
                self.log_cb(f"[{i+1}/{total_videos}] 处理: {safe_title}")

                if i > 0:
                    delay = random.uniform(1.0, 2.5)
                    self.log_cb(f"🌿 视频请求间隔 {delay:.1f} 秒")
                    self._perf_log(
                        'wait', bvid=item.get('bvid'), kind='video_gap', seconds=delay
                    )
                    if not self._interruptible_sleep(delay):
                        break

                item_started_at = time.time()
                item_perf_started = time.perf_counter()
                self._perf_log(
                    'item_start', index=i + 1, total=total_videos,
                    bvid=item.get('bvid'), title=safe_title,
                )
                self._active_item_bvid = item.get('bvid') or ''
                output_file = None
                rate = self.speed_limit * 1024 if self.speed_limit > 0 else None
                success = False

                # 主路径：FFmpeg直接读取音视频URL，下载的同时完成封装/合并。
                try:
                    streamed_outputs = self._download_streaming_item(item)
                    output_file = streamed_outputs[0] if streamed_outputs else None
                    success = bool(output_file)
                    if success:
                        self.log_cb("✅ 流式下载并合并完成")
                except Exception as stream_error:
                    if str(stream_error) == "USER_CANCEL" or self.is_cancelled:
                        break
                    if self._is_api_rate_limit(stream_error):
                        self._perf_log(
                            'item_error', bvid=item.get('bvid'), mode='stream',
                            elapsed_s=time.perf_counter() - item_perf_started,
                            error=str(stream_error),
                        )
                        self.log_cb("⚠️ 风控响应持续，本项不进入兼容回退，避免追加请求")
                        self.fail_cb(item)
                        processed_items += 1
                        continue
                    self.log_cb("⚠️ 流式失败，切换兼容模式...")
                    self._perf_log(
                        'fallback_start', bvid=item.get('bvid'),
                        stream_elapsed_s=time.perf_counter() - item_perf_started,
                        error=str(stream_error),
                    )
                    self.log_error(item['title'], stream_error, context="FFmpeg流式模式")

                if success:
                    self._notify_history(item, output_file)
                    size_bytes = (
                        os.path.getsize(output_file)
                        if output_file and os.path.exists(output_file) else 0
                    )
                    self._perf_log(
                        'item_done', bvid=item.get('bvid'), mode='stream',
                        elapsed_s=time.perf_counter() - item_perf_started,
                        size_mb=size_bytes / (1024 * 1024),
                    )
                    processed_items += 1
                    self.progress_cb((i + 1) / total_videos, f"完成: {safe_title}", True, i + 1, total_videos)
                    continue

                # 兼容回退使用yt-dlp内置下载器，避免外部Aria2控制台窗口。
                self.has_aria2 = False
                force_api_mode = prefer_api_mode or (time.time() < self._yt_dlp_blocked_until)
                if force_api_mode and not prefer_api_mode:
                    self.log_cb("🧊 yt-dlp 冷却中，跳过常规模式")

                base_opts = {
                    'ratelimit': rate,
                    'progress_hooks': [self.progress_hook],
                    'logger': self.MyLogger(self.log_cb),
                    'cookiefile': cookie_file,
                    'http_headers': {
                        'Referer': 'https://www.bilibili.com/',
                        'User-Agent': current_ua
                    },
                    'retries': 3,
                    'socket_timeout': 15,
                    'quiet': True,
                    'no_warnings': False,
                    'verbose': False,  # 减少输出噪音
                }

                if not self.dl_all_parts:
                    base_opts['playlist_items'] = '1'

                if self.has_aria2:
                    base_opts.update({
                        'external_downloader': self.aria2_path or 'aria2c',
                        # 保守提速：并发适中，避免触发站点风控
                        'external_downloader_args': {'aria2c': ['-x','8','-s','8','-k','1M','--min-split-size=1M','--max-tries=5','--retry-wait=2','--file-allocation=none']}
                    })

                # === 模式1: 常规 yt-dlp ===
                if not force_api_mode:
                    try:
                        if self.is_audio_only:
                            spec = "bestaudio/best"
                        else:
                            # 添加更多备选方案，确保能获取到音频
                            spec = f"{format_str}/bestvideo+bestaudio/best"

                        opts = base_opts.copy()
                        opts.update({
                            'outtmpl': f'{self.save_dir}/%(title)s [%(id)s].%(ext)s',
                            'format': spec,
                            'trim_file_name': 80,
                            'extractor_args': {'bilibili': {'player_client': ['android', 'web']}},
                            # 温和并发：降低限速/风控概率
                            'concurrent_fragment_downloads': 4,
                            # 关键：尽量要求音视频分离并强制合并，避免单独视频流落盘
                            'final_ext': 'mp4',
                            # 关键：强制指定合并输出格式为 MP4
                            'merge_output_format': 'mp4',
                            'prefer_ffmpeg': True,
                            'keepvideo': False,
                            'postprocessor_args': ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart'],
                            # 确保合并时音频编码为 AAC（兼容性更好）
                            'postprocessors': [
                                {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'}
                            ],
                        })

                        if self.has_ffmpeg:
                            opts['ffmpeg_location'] = os.path.dirname(self._find_local_ffmpeg())

                        self.log_cb("⏳ 尝试常规下载模式...")
                        download_started_at = time.time()
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            ydl.download([f"https://www.bilibili.com/video/{item['bvid']}"])
                        success = True
                        output_file = self._find_output_for_item(item, download_started_at)
                        self.log_cb("✅ 下载完成")

                        # 如果结果文件无音频，交给 API 模式重试
                        if (not self.is_audio_only) and (not force_api_mode) and not self._verify_recent_video_output(item, download_started_at):
                            success = False
                            force_api_mode = True

                    except DownloadError as e:
                        if self._is_yt_dlp_412(e):
                            self._cooldown_yt_dlp()
                            force_api_mode = True
                            success = False
                            if self.has_aria2:
                                self.has_aria2 = False
                                base_opts.pop('external_downloader', None)
                                base_opts.pop('external_downloader_args', None)
                            self.log_cb("⚠️ 跳过 yt-dlp 重试，直接切换 API 直链模式...")
                            time.sleep(random.uniform(1.2, 2.4))
                        # 外部下载器在不同机器上兼容性不稳定；只要本轮用了 aria2，失败后先自动禁用 aria2 重试。
                        elif self.has_aria2:
                            self.log_cb("⚠️ 常规下载失败，自动禁用 aria2 并切换内置下载器重试...")
                            self.has_aria2 = False
                            base_opts.pop('external_downloader', None)
                            base_opts.pop('external_downloader_args', None)
                            retry_opts = base_opts.copy()
                            retry_opts.pop('external_downloader', None)
                            retry_opts.pop('external_downloader_args', None)
                            retry_opts.update({
                                'outtmpl': f'{self.save_dir}/%(title)s [%(id)s].%(ext)s',
                                'format': spec,
                                'trim_file_name': 80,
                                'extractor_args': {'bilibili': {'player_client': ['android', 'web']}},
                                'concurrent_fragment_downloads': 3,
                                'final_ext': 'mp4',
                                'merge_output_format': 'mp4',
                                'prefer_ffmpeg': True,
                                'keepvideo': False,
                                'postprocessor_args': ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart'],
                                'postprocessors': [
                                    {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'}
                                ],
                            })
                            if self.has_ffmpeg:
                                retry_opts['ffmpeg_location'] = os.path.dirname(self._find_local_ffmpeg())

                            try:
                                retry_started_at = time.time()
                                with yt_dlp.YoutubeDL(retry_opts) as ydl:
                                    ydl.download([f"https://www.bilibili.com/video/{item['bvid']}"])
                                success = True
                                output_file = self._find_output_for_item(item, retry_started_at)
                                self.log_cb("✅ 内置下载器重试成功")
                                if (not self.is_audio_only) and not self._verify_recent_video_output(item, retry_started_at):
                                    success = False
                                    force_api_mode = True
                            except Exception as retry_e:
                                if self._is_yt_dlp_412(retry_e):
                                    self._cooldown_yt_dlp()
                                    self.log_cb("⚠️ 内置下载器仍遇到 412，切换 API 直链模式...")
                                else:
                                    self.log_error(item['title'], retry_e, context="yt-dlp内置下载器重试")
                                    self.log_cb("⚠️ 内置下载器重试失败，尝试切换 API 直链模式...")
                                force_api_mode = True
                        else:
                            self.log_error(item['title'], e, context="yt-dlp常规模式")
                            self.log_cb("⚠️ 常规模式失败，尝试切换 API 直链模式...")
                            force_api_mode = True

                    except Exception as e:
                        # 记录常规模式失败，但不打断，继续尝试 API 模式
                        if self._is_yt_dlp_412(e):
                            self._cooldown_yt_dlp()
                            self.log_cb("⚠️ 常规模式遇到 412，切换 API 直链模式...")
                        else:
                            self.log_error(item['title'], e, context="yt-dlp常规模式")
                            self.log_cb("⚠️ 常规模式失败，尝试切换 API 直链模式...")
                        force_api_mode = True

                # === 模式2: API 直链 (Fallback) ===
                if self.is_cancelled:
                    break

                if force_api_mode:
                    try:
                        if not self.has_aria2:
                            base_opts.pop('external_downloader', None)
                            base_opts.pop('external_downloader_args', None)
                        if self.dl_all_parts:
                            self.log_cb("⚠️ 直链模式暂仅支持P1")
                        self.log_cb(f"🔄 启动 API 解析 ({self.quality})...")

                        fallback_pages, r_title = self._resolve_with_backoff(
                            lambda: BiliResolver.get_video_pages(
                                item['bvid'], self.session, all_parts=False,
                                raise_errors=True,
                            ),
                            "兼容模式获取分P信息",
                        )
                        if not fallback_pages:
                            raise RuntimeError("API未解析到可用分P")
                        stream = self._resolve_with_backoff(
                            lambda: BiliResolver.get_page_stream(
                                item['bvid'], fallback_pages[0], self.session,
                                self.quality, r_title, raise_errors=True,
                            ),
                            "兼容模式解析P1",
                        )

                        if stream:
                            s_title = self.clean_filename(r_title)
                            ext = 'm4a' if self.is_audio_only else 'mp4'
                            f_path = os.path.join(
                                self.save_dir, f"{s_title} [{item['bvid']}].{ext}"
                            )

                            # DASH 模式 (音视频分离)
                            if stream['type'] == 'dash' and self.has_ffmpeg and not self.is_audio_only:
                                v_tmp = os.path.join(self.save_dir, f"tmp_v_{int(time.time())}_{random.randint(1000,9999)}.mp4")
                                a_tmp = os.path.join(self.save_dir, f"tmp_a_{int(time.time())}_{random.randint(1000,9999)}.m4a")

                                try:
                                    self.log_cb("📥 下载视频流...")
                                    v_opt = base_opts.copy()
                                    v_opt['outtmpl'] = v_tmp
                                    with yt_dlp.YoutubeDL(v_opt) as ydl:
                                        ydl.download([stream['video_url']])

                                    self.log_cb("📥 下载音频流...")
                                    a_opt = base_opts.copy()
                                    a_opt['outtmpl'] = a_tmp
                                    with yt_dlp.YoutubeDL(a_opt) as ydl:
                                        ydl.download([stream['audio_url']])

                                    self.log_cb("⚙️ 正在合并音视频...")

                                    # 方案1: 尝试流复制（快速）
                                    merge_success = False
                                    try:
                                        result = self._run_hidden(
                                            [self._find_local_ffmpeg(), '-y', '-i', v_tmp, '-i', a_tmp,
                                             '-c:v', 'copy', '-c:a', 'copy',
                                             '-movflags', '+faststart',
                                             '-loglevel', 'warning', f_path],
                                            check=True,
                                            capture_output=True,
                                            text=True,
                                            timeout=120
                                        )
                                        merge_success = True
                                        self.log_cb("✅ 合并完成 (流复制)")
                                    except subprocess.CalledProcessError:
                                        # 方案2: 音频转码（兼容性）
                                        self.log_cb("⚠️ 流复制失败，尝试音频转码...")
                                        try:
                                            self._run_hidden(
                                                [self._find_local_ffmpeg(), '-y', '-i', v_tmp, '-i', a_tmp,
                                                 '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                                                 '-movflags', '+faststart',
                                                 '-loglevel', 'warning', f_path],
                                                check=True,
                                                capture_output=True,
                                                text=True,
                                                timeout=180
                                            )
                                            merge_success = True
                                            self.log_cb("✅ 合并完成 (音频转码)")
                                        except subprocess.CalledProcessError as e2:
                                            self.log_cb(f"❌ FFmpeg错误: {e2.stderr[:200] if e2.stderr else '未知'}")

                                    if merge_success:
                                        # 验证输出文件是否有音频流
                                        if self._verify_audio_stream(f_path):
                                            success = True
                                            output_file = f_path
                                        else:
                                            self.log_cb("⚠️ 输出文件缺少音频，尝试重新编码...")
                                            # 方案3: 完全重新编码
                                            try:
                                                self._run_hidden(
                                                    [self._find_local_ffmpeg(), '-y', '-i', v_tmp, '-i', a_tmp,
                                                     '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                                                     '-c:a', 'aac', '-b:a', '192k',
                                                     '-movflags', '+faststart',
                                                     '-loglevel', 'warning', f_path],
                                                    check=True,
                                                    capture_output=True,
                                                    text=True,
                                                    timeout=300
                                                )
                                                if self._verify_audio_stream(f_path):
                                                    success = True
                                                    output_file = f_path
                                                    self.log_cb("✅ 重新编码成功")
                                            except Exception as e3:
                                                self.log_cb(f"❌ 重新编码失败: {str(e3)[:100]}")
                                    else:
                                        # 清理可能损坏的文件
                                        if os.path.exists(f_path):
                                            try: os.remove(f_path)
                                            except: pass

                                except Exception as inner_e:
                                    raise inner_e
                                finally:
                                    # 清理临时文件
                                    if os.path.exists(v_tmp): os.remove(v_tmp)
                                    if os.path.exists(a_tmp): os.remove(a_tmp)

                            # 单文件模式 (DURL) - 通常已有音频
                            else:
                                url = stream.get('audio_url') if self.is_audio_only else None
                                url = url or stream.get('url') or stream.get('video_url')
                                d_opt = base_opts.copy()
                                d_opt['outtmpl'] = f_path
                                with yt_dlp.YoutubeDL(d_opt) as ydl:
                                    ydl.download([url])

                                # 验证音频
                                if self.is_audio_only or self._verify_audio_stream(f_path):
                                    success = True
                                    output_file = f_path
                                    self.log_cb("✅ 下载完成")
                                else:
                                    self.log_cb("⚠️ 文件可能缺少音频流")
                                    try:
                                        if os.path.exists(f_path):
                                            os.remove(f_path)
                                    except Exception:
                                        pass

                        else:
                            self.log_cb("❌ API返回空数据 (可能需要会员或地区限制)")
                            with open(os.environ.get("BILI_ERROR_LOG") or ERROR_LOG, "a", encoding="utf-8") as f:
                                f.write(f"[{time.ctime()}] API解析为空: {item['title']} - {item['bvid']}\n")

                    except Exception as e:
                        self.log_error(item['title'], e, context="API模式")

                if self.is_cancelled:
                    break
                if success:
                    if not output_file:
                        output_file = self._find_output_for_item(item, item_started_at)
                    self._notify_history(item, output_file)
                    size_bytes = (
                        os.path.getsize(output_file)
                        if output_file and os.path.exists(output_file) else 0
                    )
                    self._perf_log(
                        'item_done', bvid=item.get('bvid'), mode='fallback',
                        elapsed_s=time.perf_counter() - item_perf_started,
                        size_mb=size_bytes / (1024 * 1024),
                    )
                    self.progress_cb((i + 1) / total_videos, f"完成: {safe_title}", True, i + 1, total_videos)
                else:
                    self._perf_log(
                        'item_error', bvid=item.get('bvid'), mode='fallback',
                        elapsed_s=time.perf_counter() - item_perf_started,
                        error='download_failed',
                    )
                    self.fail_cb(item)
                processed_items += 1

        except Exception as global_e:
            self.log_error("全局线程", global_e, context="Worker主循环")

        finally:
            elapsed = time.perf_counter() - batch_started
            self._perf_log(
                'batch_done', items=total_videos, parallelism=1,
                completed=processed_items,
                elapsed_s=elapsed,
                items_per_min=(processed_items * 60 / elapsed) if elapsed else 0,
                cancelled=self.is_cancelled,
            )
            if self._cleanup_cookie_file:
                self._perf_summary(elapsed)
            if self._cleanup_cookie_file and cookie_file and os.path.exists(cookie_file):
                try: os.remove(cookie_file)
                except: pass
            self.progress_cb(-1, "DONE", False)
