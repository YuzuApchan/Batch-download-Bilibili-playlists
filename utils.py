# utils.py
import time
import hashlib
import urllib.parse
from functools import reduce


class BiliApiError(Exception):
    def __init__(self, message, status_code=None, api_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code

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

class BiliResolver:
    @staticmethod
    def _request_json(sess, url, timeout=15):
        response = sess.get(url, timeout=timeout)
        status_code = getattr(response, 'status_code', 200)
        if status_code in (412, 429):
            raise BiliApiError(f"HTTP {status_code}", status_code=status_code)
        payload = response.json()
        api_code = payload.get('code', 0)
        if api_code != 0:
            message = payload.get('message') or f"API错误 {api_code}"
            raise BiliApiError(message, status_code=status_code, api_code=api_code)
        return payload['data']

    @staticmethod
    def _quality_id(quality_label):
        if quality_label == "4K":
            return 120
        if quality_label == "2K":
            return 116
        if quality_label == "720":
            return 64
        if quality_label == "480":
            return 32
        return 116

    @staticmethod
    def _stream_from_play_data(data, title, duration):
        if 'dash' in data:
            video_streams = data['dash'].get('video') or []
            if not video_streams:
                raise Exception("API未返回可用视频流")
            best_video = sorted(
                video_streams,
                key=lambda x: (x['id'], x.get('bandwidth', 0)),
                reverse=True,
            )[0]

            audio_streams = data['dash'].get('audio') or []
            if not audio_streams:
                raise Exception("API未返回可用音频流")
            best_audio = sorted(
                audio_streams,
                key=lambda x: x.get('bandwidth', 0),
                reverse=True,
            )[0]

            return {
                'type': 'dash',
                'video_url': best_video.get('baseUrl') or best_video.get('base_url'),
                'audio_url': best_audio.get('baseUrl') or best_audio.get('base_url'),
                'title': title,
                'duration': duration,
                'quality_id': best_video['id'],
            }
        if 'durl' in data:
            urls = [row.get('url') for row in data['durl'] if row.get('url')]
            if not urls:
                raise Exception("API未返回可用下载地址")
            return {
                'type': 'durl',
                'url': urls[0],
                'urls': urls,
                'title': title,
                'duration': duration,
            }
        return None

    @staticmethod
    def get_video_pages(bvid, sess, all_parts=False, raise_errors=False):
        """Return lightweight page metadata without resolving expiring play URLs."""
        try:
            view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            view_data = BiliResolver._request_json(sess, view_url)
            title = view_data['title']
            pages = view_data.get('pages') or [{
                'cid': view_data['cid'],
                'page': 1,
                'part': title,
                'duration': view_data.get('duration', 0),
            }]
            if not all_parts:
                pages = pages[:1]

            normalized = []
            for index, page in enumerate(pages, 1):
                normalized.append({
                    'cid': page['cid'],
                    'part_index': page.get('page') or index,
                    'part_title': page.get('part') or f"P{index}",
                    'duration': page.get('duration') or 0,
                })
            return normalized, title
        except Exception as e:
            if raise_errors:
                raise
            print(f"Resolver: {e}")
            return [], None

    @staticmethod
    def get_page_stream(bvid, page, sess, quality_label="1080", video_title=None, raise_errors=False):
        """Resolve a single page immediately before it is downloaded."""
        try:
            target_qn = BiliResolver._quality_id(quality_label)
            cid = page['cid']
            play_url = (
                "https://api.bilibili.com/x/player/playurl"
                f"?bvid={bvid}&cid={cid}&qn={target_qn}&fnval=16&fnver=0&fourk=1"
            )
            play_data = BiliResolver._request_json(sess, play_url)
            stream = BiliResolver._stream_from_play_data(
                play_data, page.get('part_title') or video_title or bvid,
                page.get('duration') or 0,
            )
            if not stream:
                raise BiliApiError("API未返回可用流")
            stream.update({
                'bvid': bvid,
                'video_title': video_title or bvid,
                'part_index': page.get('part_index') or 1,
                'part_title': page.get('part_title') or video_title or bvid,
                'cid': cid,
            })
            return stream
        except Exception as e:
            if raise_errors:
                raise
            print(f"Resolver: {e}")
            return None

    @staticmethod
    def get_video_parts(bvid, sess, quality_label="1080", all_parts=False):
        """Compatibility helper that resolves all requested pages eagerly."""
        pages, title = BiliResolver.get_video_pages(bvid, sess, all_parts)
        if not pages:
            return [], title

        resolved = []
        for page in pages:
            stream = BiliResolver.get_page_stream(
                bvid, page, sess, quality_label, title
            )
            if stream:
                resolved.append(stream)
        return resolved, title

    @staticmethod
    def get_video_stream(bvid, sess, quality_label="1080"):
        parts, title = BiliResolver.get_video_parts(
            bvid, sess, quality_label, all_parts=False
        )
        if not parts:
            return None, None, 0
        stream = parts[0]
        return stream, title, stream.get('duration', 0)
