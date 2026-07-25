# manager.py
import os
import json
import shutil
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import BASE_DIR, HISTORY_FILE, COOKIE_FILE, NETSCAPE_TEMP, LAST_LOGIN_COOKIE, BACKUP_FILENAMES

class BiliManager:
    def __init__(self):
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/',
        })
        self.uid = "guest"
        self.init_paths()
        self.history = set()
        self.load_root_cookie_and_identify()

    def init_paths(self):
        self.user_dir = os.path.join(BASE_DIR, str(self.uid))
        if not os.path.exists(self.user_dir): os.makedirs(self.user_dir)
        self.history_file_path = os.path.join(self.user_dir, HISTORY_FILE)
        self.cookie_file_path = os.path.join(self.user_dir, COOKIE_FILE)

    def load_data(self):
        if os.path.exists(self.history_file_path):
            try:
                with open(self.history_file_path, 'r', encoding='utf-8') as f:
                    self.history = set(json.load(f))
            except: self.history = set()
        else: self.history = set()

        if os.path.exists(self.cookie_file_path):
            try:
                with open(self.cookie_file_path, 'r') as f:
                    cookies = json.load(f)
                    self.session.cookies.update(cookies)
            except: pass

    def save_data(self):
        with open(self.history_file_path, 'w', encoding='utf-8') as f:
            json.dump(list(self.history), f)
        with open(self.cookie_file_path, 'w', encoding='utf-8') as f:
            json.dump(self.session.cookies.get_dict(), f)

    def load_root_cookie_and_identify(self):
        if os.path.exists(LAST_LOGIN_COOKIE):
            try:
                with open(LAST_LOGIN_COOKIE, 'r', encoding='utf-8') as f:
                    c = json.load(f)
                    self.session.cookies.update(c)
            except: pass

    def switch_user(self, uid):
        self.uid = str(uid)
        self.init_paths()
        self.load_data()
        with open(LAST_LOGIN_COOKIE, 'w', encoding='utf-8') as f:
            json.dump(self.session.cookies.get_dict(), f)
        self.save_data()

    def get_netscape_cookie_path(self):
        try:
            content = "# Netscape HTTP Cookie File\n\n"
            for cookie in self.session.cookies:
                domain = cookie.domain
                if not domain.startswith('.'): domain = '.' + domain
                content += f"{domain}\tTRUE\t{cookie.path}\t{'TRUE' if cookie.secure else 'FALSE'}\t{int(cookie.expires) if cookie.expires else 0}\t{cookie.name}\t{cookie.value}\n"
            with open(NETSCAPE_TEMP, 'w', encoding='utf-8') as f: f.write(content)
            return NETSCAPE_TEMP
        except: return None

    def logout(self):
        self.session.cookies.clear()
        if os.path.exists(LAST_LOGIN_COOKIE): os.remove(LAST_LOGIN_COOKIE)
        self.uid = "guest"
        self.init_paths()
        self.history = set()

    def import_backup_files(self, src_path):
        """导入备份数据，支持文件路径或目录路径，文件名支持前缀匹配"""
        count = 0
        # 如果传入的是文件，直接根据文件名判断类型并导入
        if os.path.isfile(src_path):
            fname = os.path.basename(src_path).lower()
            for key, prefixes in [("history", ["bili_history", "history"]), ("cookie", ["bili_cookies", "cookies"])]:
                if any(fname.startswith(p) and fname.endswith(".json") for p in prefixes):
                    try:
                        with open(src_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if key == "history":
                            self.history = self.history | set(data)
                        else:
                            self.session.cookies.update(data)
                        self.save_data()
                        count += 1
                        break
                    except Exception:
                        pass
            return count > 0
        # 如果传入的是目录，遍历查找匹配文件
        src_dir = src_path
        files_in_src = os.listdir(src_dir)
        for fname in files_in_src:
            fname_lower = fname.lower()
            for key, prefixes in [("history", ["bili_history", "history"]), ("cookie", ["bili_cookies", "cookies"])]:
                if any(fname_lower.startswith(p) and fname_lower.endswith(".json") for p in prefixes):
                    full_path = os.path.join(src_dir, fname)
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if key == "history":
                            self.history = self.history | set(data)
                        else:
                            self.session.cookies.update(data)
                        self.save_data()
                        count += 1
                    except Exception:
                        pass
                    break
        return count > 0
