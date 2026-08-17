"""脚本文件本身的编码规矩。

这两条是**真的踩过**的坑，而且症状很唬人：`运行.bat` / `打包.bat` 一跑就是
满屏乱码加一堆语法错。原因是 Windows PowerShell 5.1（系统自带的那个）
**把没有 BOM 的 UTF-8 脚本按 GBK 读**，中文全变乱码，有些字节还正好是
反引号或引号，直接把脚本解析坏。开发时用 pwsh 7 测（默认按 UTF-8 读）
一切正常，换成双击 bat 就炸 —— 只靠"我这儿能跑"是发现不了的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
UTF8_BOM = b"\xef\xbb\xbf"


def 脚本(pattern: str) -> list[Path]:
    return sorted(SCRIPTS.glob(pattern))


def test_有脚本可查() -> None:
    assert 脚本("*.ps1"), "scripts 目录里应该有 .ps1"
    assert 脚本("*.bat"), "scripts 目录里应该有 .bat"


@pytest.mark.parametrize("path", 脚本("*.ps1"), ids=lambda p: p.name)
def test_ps1必须带UTF8_BOM(path: Path) -> None:
    """没 BOM 的话 Windows PowerShell 5.1 会按 GBK 读，中文乱码 + 语法错。"""
    assert path.read_bytes().startswith(UTF8_BOM), (
        f"{path.name} 没有 UTF-8 BOM —— 双击 bat 时会乱码并报语法错，"
        "用 utf-8-sig 重存一次"
    )


@pytest.mark.parametrize("path", 脚本("*.bat"), ids=lambda p: p.name)
def test_bat必须是纯ASCII(path: Path) -> None:
    """**连 rem 注释里都不能有中文。**

    cmd 是按当前代码页一边读一边执行 bat 的：一个 UTF-8 中文字被按 GBK 解成
    两个字符，字节数和字符数就错位了，cmd 会从下一行的中间开始执行 ——
    实测报的错是 `'ell' 不是内部或外部命令`、`'cutionPolicy' 不是…`
    （"powershell -NoProfile -ExecutionPolicy" 被从中间切开了）。

    所以 bat 只做一层 ASCII 外壳，所有中文都写在 .ps1 里。
    """
    非ascii = [byte for byte in path.read_bytes() if byte > 127]
    assert not 非ascii, (
        f"{path.name} 里有 {len(非ascii)} 个非 ASCII 字节 —— cmd 解析会错位，"
        "把中文挪到对应的 .ps1 里"
    )


@pytest.mark.parametrize("path", 脚本("*.bat"), ids=lambda p: p.name)
def test_bat优先用pwsh再退回powershell(path: Path) -> None:
    """两个都要能跑：pwsh 7 没装的机器（比如店铺机）要退回系统自带的 5.1。"""
    text = path.read_text(encoding="ascii")
    if ".ps1" not in text:
        pytest.skip(f"{path.name} 不调 PowerShell")
    assert "where /q pwsh" in text
    assert "pwsh -NoProfile" in text
    assert "powershell -NoProfile" in text
