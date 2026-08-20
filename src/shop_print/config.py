"""配置读写。落在 %LOCALAPPDATA%\\ShopPrint\\config.json。

原则：**配置坏了绝不能让程序起不来。**长辈没法自己修 JSON，
所以读取失败一律退回默认值并写日志，不抛异常。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
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
    # 出彩色。默认黑白 —— 店里那台柯美只有黑白，彩色是给「保存成图片 / PDF」用的
    color: bool = False
    # 裁剪边缘：正数往外多留一圈（怕裁到内容），负数往里收。界面上是「边缘 紧←→松」
    crop_margin: float = 0.0


@dataclass
class PrintConfig:
    printer: str = ""  # 空 = 用系统默认打印机；店铺机上应填柯美 225i 的准确名称
    copies: int = 1
    duplex: bool = False
    paper: str = "A4"
    backend: str = "gdi"  # gdi | sumatra，见 docs/decisions/ADR-003-打印后端选择.md
    dpi: int = 300  # 打印光栅化分辨率。600dpi 下 A4 灰度约 70MB/页，弱机器吃不消
    price_per_page: float = 0.0  # 打印记录里的金额，第二阶段收费会用
    zoom: int = 100  # 等比缩放百分比。100 = 刚好铺满可打印区（不会超边）


@dataclass
class OcrConfig:
    cloud_provider: str = ""  # 空 = 云端高精度未配置，界面上按钮置灰
    cloud_api_key: str = ""
    cloud_endpoint: str = ""


@dataclass
class OutputConfig:
    """存出来的东西放哪儿：证件 PDF、转好的 Word、处理过的图片。

    `dir` 空 = 用 `%LOCALAPPDATA%\\ShopPrint\\output`。那个路径长辈根本找不到，
    所以设置里能改成桌面或者 D 盘上的一个文件夹 —— 店里习惯把当天的活儿
    都堆在一个文件夹里，改这里比让长辈记路径实在。
    """

    dir: str = ""
    last_save_dir: str = ""  # 上次「另存为」选的文件夹，下次对话框还开这里


@dataclass
class IntakeConfig:
    """文件从哪来。工作区 = 店里放待打印文件的那个文件夹（默认 C:\\打印\\待打印）。"""

    workspace_dir: str = ""  # 空 = 用 paths.WORKSPACE_DIR
    watch_workspace: bool = True  # 监控工作区文件夹
    watch_wechat: bool = True  # 监控微信接收目录
    wechat_dirs: list[str] = field(default_factory=list)  # 空 = 自动探测
    recent_days: int = 3  # 首页只显示最近几天的文件，否则列表会长到没法用


@dataclass
class CardsConfig:
    """证件二合一（身份证正反面、户口本两页拼一张纸）。见 docs/10-证件二合一.md。"""

    default_type: str = "id"  # 默认证件类型，见 core/cards.PRESETS 的 key；auto = 自动认
    gap_mm: float = 10.0  # 两张之间的间隔，留一点好剪
    # 深浅（界面上的「淡 ←→ 浓」滑块）。默认偏淡：身份证满版防伪底纹，
    # 太浓底纹和字一样黑，字就看不清了（用户反馈过）。见 core/cards._enhance_card
    strength: int = 30
    # 出彩色。默认黑白（店里只有黑白机），彩色是给「保存成 PDF / 另存为」用的
    color: bool = False


@dataclass
class AppConfig:
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    printing: PrintConfig = field(default_factory=PrintConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    cards: CardsConfig = field(default_factory=CardsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


_SECTIONS = {f.name: f.type for f in fields(AppConfig)}


# ── 目录：配了就用配的，配的用不了就退回默认 ──────────────────────
def _usable(raw: str, fallback: Path) -> Path:
    """把配置里的路径变成一个**真能写**的目录。

    建不出来就退回默认目录而不是报错：店主可能把路径填成 U 盘上的目录，
    盘一拔工具就该继续能用，而不是每次保存都弹错。
    """
    for candidate in (Path(raw.strip()) if raw.strip() else None, fallback):
        if candidate is None:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            logger.warning("目录用不了：%s", candidate)
    return fallback


def save_dir(cfg: OutputConfig) -> Path:
    """「保存成 PDF / 保存成图片」默认存到哪儿。设置里能改。"""
    return _usable(cfg.dir, paths.output_dir())


def dialog_dir(cfg: OutputConfig) -> Path:
    """「另存为」对话框该打开哪个文件夹：上次去过的地方优先，没有就用默认保存位置。"""
    last = cfg.last_save_dir.strip()
    if last and Path(last).is_dir():
        return Path(last)
    return save_dir(cfg)


def workspace_dir(cfg: IntakeConfig) -> Path:
    """工作区文件夹：店里放待打印文件的地方，也是文件对话框默认打开的地方。"""
    return _usable(cfg.workspace_dir, paths.WORKSPACE_DIR)


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
        cards=_build_section(CardsConfig, raw.get("cards")),
        output=_build_section(OutputConfig, raw.get("output")),
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
