"""自检（`打印助手.exe --self-check`）。

这是店铺机上排查问题的唯一手段 —— 父母不会看日志、不会用命令行，只会把
自检报告那个文件发回来。所以它**本身不能崩**，也不能因为某一项查不了就整体失败。
"""

from __future__ import annotations

import pytest

from shop_print import paths, self_check


@pytest.fixture
def 跳过慢检查(monkeypatch):
    """打印机和 OCR 两项在这里屏蔽掉：一个依赖本机驱动，一个要跑 2 秒多的模型。
    它们各自有专门的测试，这里只验报告本身的骨架。"""
    monkeypatch.setattr(self_check, "_check_printers", lambda report: report.item("（跳过）"))
    monkeypatch.setattr(self_check, "_check_ocr", lambda report: report.item("（跳过）"))


def test_全部通过时退出码为0(跳过慢检查) -> None:
    assert self_check.run() == 0


def test_报告写进日志目录_而且是utf8(跳过慢检查) -> None:
    """父母要能把这个文件发回来，编码错了就是一堆乱码。"""
    self_check.run()
    report = paths.log_dir() / "自检报告.txt"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "自检报告" in text
    assert "随包资源" in text
    assert "待打印文件夹" in text


def test_模型丢了会报致命错(跳过慢检查, monkeypatch, tmp_path) -> None:
    """`--add-data` 路径写错是最常见的打包事故，必须被这条抓住。"""
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path / "没有模型")
    assert self_check.run() == 1
    assert "没有 OCR 模型" in (paths.log_dir() / "自检报告.txt").read_text(encoding="utf-8")


def test_缺图标只是提醒_不算致命(跳过慢检查, monkeypatch, tmp_path) -> None:
    """图标不影响能不能打印，别为它把整份报告判成失败。"""
    monkeypatch.setattr(paths, "icons_dir", lambda: tmp_path / "没有图标")
    assert self_check.run() == 0


def test_日志目录写不进去也不抛(跳过慢检查, monkeypatch, tmp_path) -> None:
    """用同名文件顶住日志目录。**启动路径上不许抛** —— 长辈双击后什么都没发生
    是最糟的失败方式（那时候日志和错误弹窗都还没装好）。"""
    占位 = tmp_path / "占位"
    占位.write_text("我是个文件，不是目录", encoding="utf-8")
    monkeypatch.setattr(paths, "log_dir", lambda: 占位)
    assert self_check.run() in (0, 1)  # 不抛异常就算过


def test_目录被同名文件占住时启动不崩(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "占位文件")
    (tmp_path / "占位文件").write_text("x", encoding="utf-8")
    assert paths.ensure_runtime_dirs() is False  # 报告失败，但不抛


@pytest.mark.needs_samples
def test_完整跑一遍(capsys) -> None:
    """真的把打印机和 OCR 也查一遍（要模型文件在 assets/models 里）。"""
    code = self_check.run()
    printed = capsys.readouterr().out
    assert "打印机" in printed
    assert "OCR" in printed
    assert code == 0
