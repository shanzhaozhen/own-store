"""所有路径集中在这里定义，不要在别处硬编码。

运行时数据放 %LOCALAPPDATA%\\ShopPrint\\，不放程序目录 ——
程序目录在升级时会被整体覆盖，配置和打印记录不能跟着丢。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "ShopPrint"

# 工作区：店里放待打印文件的那个文件夹。微信里"另存为"到这里，工具首页立刻出现。
# 安装脚本会创建它并在桌面放快捷方式。用中文路径是有意的 —— 长辈要能看懂。
# **这只是默认值**，设置里能改（见 config.workspace_dir）。
WORKSPACE_DIR = Path("C:/打印/待打印")


def _local_app_data() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def data_dir() -> Path:
    """运行时数据根目录（配置、日志、缓存、输出、打印记录）。"""
    return _local_app_data() / APP_DIR_NAME


def config_file() -> Path:
    return data_dir() / "config.json"


def history_db() -> Path:
    return data_dir() / "history.db"


def cache_dir() -> Path:
    """转换出来的 PDF，按源文件内容 hash 命名。可安全清空。"""
    return data_dir() / "cache"


def log_dir() -> Path:
    """技术细节只进这里，界面上不显示。"""
    return data_dir() / "logs"


def output_dir() -> Path:
    """给用户的产出的**默认**位置：OCR 生成的 docx/txt、另存的增强图片、证件 PDF。

    设置里能改到别处（见 `config.save_dir`）—— 这个路径长辈自己找不到。
    """
    return data_dir() / "output"


def bundle_root() -> Path:
    """资源根目录。PyInstaller 打包后指向解包目录，开发时指向包目录。"""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent


def assets_dir() -> Path:
    return bundle_root() / "assets"


def models_dir() -> Path:
    """随包分发的 OCR 模型，运行时不联网下载。"""
    return assets_dir() / "models"


def fonts_dir() -> Path:
    """内嵌中文字体，txt 转 PDF 时用，不依赖系统装了什么字体。"""
    return assets_dir() / "fonts"


def icons_dir() -> Path:
    return assets_dir() / "icons"


def style_file() -> Path:
    """全局样式表。开发时在包目录下，打包后在解包目录下，两边都靠 bundle_root() 拿。"""
    return bundle_root() / "ui" / "style.qss"


def ensure_runtime_dirs() -> bool:
    """启动时调用一次。缺目录会让后面每个写操作都失败，所以一次性建好。

    **建不出来也不抛。**启动路径上抛异常等于长辈双击后什么都没发生（这时候
    日志和错误弹窗都还没装好）。同名的文件占住位置、C 盘没权限这类怪事，
    宁可让后面每处写操作各自降级（`config.save()` 返回 False、日志静默关掉），
    也不能让程序起不来。返回是否全部就绪，给自检用。
    """
    ok = True
    for path in (data_dir(), cache_dir(), log_dir(), output_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            ok = False
    return ok


def ensure_workspace_dir() -> bool:
    """建默认的工作区文件夹。失败不该拦住启动（比如 C 盘没权限），所以返回布尔而不抛。

    设置里改过路径的话，实际用的目录由 `config.workspace_dir()` 负责创建。
    """
    try:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True
