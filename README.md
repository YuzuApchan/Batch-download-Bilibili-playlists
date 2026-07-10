# BiliDownloader Studio

BiliDownloader Studio 是一个面向 B 站收藏夹批量下载、历史去重、Eagle 视频导入和视频套图封面生成的本地 Web 工具。

## 主要功能

- 扫码登录 B 站账号并同步收藏夹。
- 按收藏夹、月份、时长、下载状态筛选视频。
- 批量下载选中视频，支持暂停、取消、限速、音频下载和分 P 下载。
- 导入/导出历史下载记录，换设备后避免重复下载。
- 将已下载视频导入 Eagle 指定文件夹。
- 为 Eagle 视频生成套图缩略图：优先使用 B 站封面 + 弹幕峰值帧，匹配不到 BV 时自动退回本地抽帧。
- 自定义路径：数据目录、下载目录、FFmpeg、FFprobe、Aria2、Eagle 缓存导出目录、错误日志。

## 快速启动

1. 安装 Python 3.10 或更高版本。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 确保本机可用 FFmpeg。可以把 `ffmpeg.exe` / `ffprobe.exe` 放在项目根目录，也可以在 Web UI 的“路径设置”中指定。
4. 启动：

```bash
python web_app.py
```

5. 浏览器打开：

```text
http://127.0.0.1:8765
```

## 文档

- [用户指南](docs/USER_GUIDE.md)
- [开发者指南](docs/DEVELOPER_GUIDE.md)

## 风控原则

本项目默认采用保守策略：缓存数据、低频请求、优先使用本地记录，不通过高并发请求提高速度。请不要把并发、请求频率或重试策略改得过于激进。

## 开源提醒

不要提交以下个人文件：

- `last_login_cookie.json`
- `bili_netscape_temp.txt`
- `userdata/`
- Eagle 库目录
- 下载视频目录
- 任何 Cookie、账号、私人收藏夹缓存
