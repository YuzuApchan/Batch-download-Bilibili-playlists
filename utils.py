# utils.py
import time
import hashlib
import urllib.parse
from functools import reduce

# === Wbi 签名工具 ===
class WbiSigner:
    @staticmethod
    def get_mixin_key(orig: str):
        mixinKeyEncTab = [
            46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
            33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
            61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
            36, 20, 34, 44, 52
        ]
        return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]

    @staticmethod
    def enc_wbi(params: dict, img_key: str, sub_key: str):
        mixin_key = WbiSigner.get_mixin_key(img_key + sub_key)
        curr_time = round(time.time())
        params['wts'] = curr_time
        params = dict(sorted(params.items()))
        params = {
            k: ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
            for k, v in params.items()
        }
        query = urllib.parse.urlencode(params)
        wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
        params['w_rid'] = wbi_sign
        return params

    @staticmethod
    def get_wbi_keys(sess):
        try:
            for _ in range(3):
                resp = sess.get('https://api.bilibili.com/x/web-interface/nav', timeout=10)
                if resp.status_code == 200:
                    json_content = resp.json()
                    img_url = json_content['data']['wbi_img']['img_url']
                    sub_url = json_content['data']['wbi_img']['sub_url']
                    img_key = img_url.rsplit('/', 1)[1].split('.')[0]
                    sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
                    return img_key, sub_key
                time.sleep(1)
            return None, None
        except: return None, None

# === B站 API 解析器 ===
class BiliResolver:
    @staticmethod
    def get_video_stream(bvid, sess):
        try:
            view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            resp = sess.get(view_url).json()
            if resp['code'] != 0: raise Exception(resp['message'])
            cid = resp['data']['cid']
            title = resp['data']['title']
            duration = resp['data'].get('duration', 0)
            play_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=80&fnval=0&fnver=0&fourk=1"
            play_resp = sess.get(play_url).json()
            if play_resp['code'] != 0: raise Exception(play_resp['message'])
            durl = play_resp['data']['durl']
            if not durl: raise Exception("No stream found")
            return durl[0]['url'], title, duration
        except Exception as e:
            print(f"Resolver Error: {e}")
            return None, None, 0
