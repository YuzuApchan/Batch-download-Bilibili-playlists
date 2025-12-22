# Batch-download-Bilibili-playlists

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)

一个基于 Python 和 CustomTkinter 构建的高性能 Bilibili 视频批量下载工具。它集成了 Aria2 多线程下载与 FFmpeg 自动转码，旨在提供流畅、稳定且美观的下载体验。

## 🚀 功能特性

- **📺 现代化界面**：采用 Fluent Design 风格，支持**深色/浅色模式**一键切换。
- **⚡ 极速下载**：内置 **Aria2** 核心，支持 32 线程并发下载，跑满你的带宽。
- **🔄 批量同步**：一键同步 B 站收藏夹，支持增量更新。
- **🛠 全能解析**：内置 Wbi 签名算法，支持**普通视频、活动页直链**解析。
- **🎵 音频提取**：支持仅下载音频模式，自动转码为 MP3 格式。
- **🔐 账户隔离**：支持多账号扫码登录，数据独立存储，互不干扰。

## 📦 环境依赖

在运行本程序前，请务必确保项目根目录下包含以下外部工具（Windows）：

*   **ffmpeg.exe** (用于视频合成/转码)
*   **aria2c.exe** (用于多线程加速)

> 目录结构示意：
> ```text
> 根目录/
> ├── main.py
> ├── ffmpeg.exe
> ├── ffprobe.exe (可选)
> └── aria2c.exe
> ```

## 🛠️ 安装与使用

1.  **克隆项目**
    ```bash
    git clone https://github.com/xiaokanla/Batch-download-Bilibili-playlists.git
    cd Batch-download-Bilibili-playlists
    ```

2.  **安装依赖**
    ```bash
    pip install -r requirements.txt
    # 或手动安装: pip install customtkinter yt-dlp requests pillow qrcode tkcalendar
    ```

3.  **启动程序**
    ```bash
    python main.py
    ```

## 📝 免责声明

本项目仅供 Python 爱好者学习与技术交流使用。
1.  本工具不提供任何视频内容存储，所有数据均来自 Bilibili 官方接口。
2.  请务必尊重版权，**严禁**将下载内容用于商业用途或非法传播。
3.  使用本工具产生的任何后果由使用者自行承担。

---
**License**: MIT
