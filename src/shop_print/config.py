"""配置读写。落在 %LOCALAPPDATA%\\ShopPrint\\config.json。

原则：**配置坏了绝不能让程序起不来。**长辈没法自己修 JSON，
所以读取失败一律退回默认值并写日志，不抛异常。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import paths

logger = logging.getLogger(__name__)


@dataclass
class EnhanceConfig:
    """照片去底增强的默认参数，见 docs/03-图片增强算法.md。"""

    mode: str = "auto"  # auto | text | mixed | photo
    strength: int = 50  # 0–100，界面上的"淡 ←→ 浓"滑块
    auto_deskew: bool = True  # 自动裁正/旋正；判断不确信时算法自己会跳过
    preview_max_side: int = 1000  # 预览缩略图长边，保证滑块实时响应


@dataclass
class PrintConfig:
    printer: str = ""  # 空 = 用系统默认打印机；店铺机上应填柯美 225i 的准确名称
    copies: int = 1
    duplex: bool = False
    paper: str = "A4"
    backend: str = "gdi"  # gdi | sumatra，见 docs/decisions/ADR-003-打印后端选择.md
    dpi: int = 300  # 打印光栅化分辨率。600dpi 下 A4 灰度约 70MB/页，弱机器吃不消
    price_per_page: float = 0.0  # 打印记录里的金额，第二阶段收费会用


@dataclass
class OcrConfig:
    cloud_provider: str = ""  # 空 = 云端高精度未配置，界面上按钮置灰
    cloud_api_key: str = ""
    cloud_endpoint: str = ""


@dataclass
class IntakeConfig:
    watch_inbox: bool = True  # 监控 C:\打印\待打印
    watch_wechat: bool = True  # 监控微信接收目录
    wechat_dirs: list[str] = field(default_factory=list)  # 空 = 自动探测
    recent_days: int = 3  # 首页只显示最近几天的文件，否则列表会长到没法用


@dataclass
class AppConfig:
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    printing: PrintConfig = field(default_factory=PrintConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    intake: IntakeConfig = field(default_factory=IntakeConfig)


_SECTIONS = {f.name: f.type for f in fields(AppConfig)}


def _build_section(cls: type, raw: Any) -> Any:
    """只认识的键才用，多余的忽略，缺的用默认值，类型不对的也用默认值。

    长辈不会手改配置，但我们自己会；改错一个字段不该让整个配置作废。
    """
    section = cls()
    if not isinstance(raw, dict):
        return section
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        default = getattr(section, f.name)
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        if isinstance(default, int) and not isinstance(default, bool):
            if not isinstance(value, int) or isinstance(value, bool):
                continue
        elif (
            (isinstance(default, float) and not isinstance(value, int | float))
            or (isinstance(default, str) and not isinstance(value, str))
            or (isinstance(default, list) and not isinstance(value, list))
        ):
            continue
        setattr(section, f.name, value)
    return section


def load() -> AppConfig:
    path = paths.config_file()
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        logger.exception("配置文件读不了，退回默认值：%s", path)
        return AppConfig()
    if not isinstance(raw, dict):
        logger.warning("配置文件格式不对（顶层不是对象），退回默认值：%s", path)
        return AppConfig()
    return AppConfig(
        enhance=_build_section(EnhanceConfig, raw.get("enhance")),
        printing=_build_section(PrintConfig, raw.get("printing")),
        ocr=_build_section(OcrConfig, raw.get("ocr")),
        intake=_build_section(IntakeConfig, raw.get("intake")),
    )


def save(cfg: AppConfig) -> bool:
    """先写临时文件再替换，避免写一半断电留下坏配置。"""
    path = paths.config_file()
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        logger.exception("配置保存失败：%s", path)
        return False
    return True
