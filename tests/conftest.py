"""公共 fixture。

最重要的一件事：**把 %LOCALAPPDATA% 指到临时目录**。
不然测试会往开发机真实的 `%LOCALAPPDATA%\\ShopPrint\\` 里写配置、打印记录和缓存，
既污染自己平时用的那份数据，也会让"读默认配置"这类测试受上一次运行影响。
"""

from __future__ import annotations

import faulthandler

import pytest


@pytest.fixture(autouse=True)
def 隔离运行时目录(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    return tmp_path


@pytest.fixture
def 屏蔽驱动噪音():
    """临时关掉 faulthandler。

    「Microsoft Print to PDF」的驱动在 `CreateDC` / `DeviceCapabilities` 里会抛一个
    它自己接住的 Windows 结构化异常（开发机上是 0x80040155），打印其实是成功的。
    但 pytest 默认开着 faulthandler，会把它打印成一大片"Windows fatal exception"
    堆栈 —— 看起来像崩了，下次跑测试的人会白白去查。
    程序自己跑的时候 faulthandler 是关的，用户不会看到这些。
    """
    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        yield
    finally:
        if enabled:
            faulthandler.enable()
