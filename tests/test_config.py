"""配置读写。

这里所有测试其实都在证明同一件事：**配置坏了不能让程序起不来。**
长辈没法自己修 JSON，读取失败只能退回默认值。
"""

from __future__ import annotations

import json

from shop_print import config as config_mod
from shop_print import paths


def 写配置(raw: str) -> None:
    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


def test_没有配置文件时用默认值() -> None:
    cfg = config_mod.load()
    assert cfg.printing.paper == "A4"
    assert cfg.printing.copies == 1
    assert cfg.enhance.strength == 50
    assert cfg.intake.watch_inbox is True


def test_存了再读回来是同一份() -> None:
    cfg = config_mod.load()
    cfg.printing.printer = "KONICA MINOLTA 225i"
    cfg.printing.copies = 3
    cfg.printing.duplex = True
    cfg.enhance.strength = 72
    cfg.intake.wechat_dirs = ["D:/微信/接收"]
    assert config_mod.save(cfg) is True

    again = config_mod.load()
    assert again.printing.printer == "KONICA MINOLTA 225i"
    assert again.printing.copies == 3
    assert again.printing.duplex is True
    assert again.enhance.strength == 72
    assert again.intake.wechat_dirs == ["D:/微信/接收"]


def test_保存不留下临时文件() -> None:
    """先写 .tmp 再替换。替换完不该还剩着，否则目录里会攒垃圾。"""
    config_mod.save(config_mod.AppConfig())
    assert not paths.config_file().with_suffix(".json.tmp").exists()


def test_坏掉的json退回默认值() -> None:
    写配置("{ 这不是 json ")
    cfg = config_mod.load()
    assert cfg.printing.paper == "A4"


def test_顶层不是对象也退回默认值() -> None:
    写配置("[1, 2, 3]")
    assert config_mod.load().enhance.mode == "auto"


def test_类型不对的字段用默认值_其余照常读() -> None:
    写配置(
        json.dumps(
            {
                "printing": {"copies": "三份", "paper": "A3", "duplex": "是"},
                "enhance": {"strength": 88},
            },
            ensure_ascii=False,
        )
    )
    cfg = config_mod.load()
    assert cfg.printing.copies == 1  # 字符串不当整数用
    assert cfg.printing.duplex is False  # 字符串不当布尔用
    assert cfg.printing.paper == "A3"  # 同一段里正常的字段照样生效
    assert cfg.enhance.strength == 88


def test_布尔不会被当成整数() -> None:
    """Python 里 True == 1，一不小心 copies 就会被写成 True。"""
    写配置(json.dumps({"printing": {"copies": True}}))
    assert config_mod.load().printing.copies == 1


def test_整数可以填进浮点字段() -> None:
    写配置(json.dumps({"printing": {"price_per_page": 1}}))
    assert config_mod.load().printing.price_per_page == 1


def test_多余的键直接忽略() -> None:
    写配置(json.dumps({"printing": {"未来的选项": 1}, "根本没有这一段": {"a": 1}}))
    cfg = config_mod.load()
    assert cfg.printing.copies == 1
    assert not hasattr(cfg.printing, "未来的选项")


def test_缺段落时该段全用默认值() -> None:
    写配置(json.dumps({"printing": {"copies": 2}}))
    cfg = config_mod.load()
    assert cfg.printing.copies == 2
    assert cfg.ocr.cloud_provider == ""  # 整段缺失
    assert cfg.intake.recent_days == 3


def test_保存失败只返回False不抛() -> None:
    """配置目录被占成文件之类的怪事，不能让程序崩在保存上。"""
    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.joinpath("config.json").mkdir(exist_ok=True)  # 用目录顶住文件名
    assert config_mod.save(config_mod.AppConfig()) is False
