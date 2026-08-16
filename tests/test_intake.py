"""文件从哪里来。

这里最要紧的一条：**必须等文件写完才回调**。微信下载、拷贝都是边写边长，
读到半个文件会让长辈看到"这个文件坏了"，然后去让顾客重发一个本来没问题的文件。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from shop_print import paths
from shop_print.config import IntakeConfig
from shop_print.core import intake


@pytest.fixture
def 收件目录(tmp_path, monkeypatch):
    inbox = tmp_path / "待打印"
    inbox.mkdir()
    monkeypatch.setattr(paths, "INBOX_DIR", inbox)
    return inbox


def 造文件(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# ── 哪些文件要理 ────────────────────────────────────────────────
def test_支持的文件才认(tmp_path) -> None:
    assert intake._is_interesting(造文件(tmp_path / "合同.pdf"))  # noqa: SLF001
    assert intake._is_interesting(造文件(tmp_path / "照片.jpg"))  # noqa: SLF001
    assert not intake._is_interesting(造文件(tmp_path / "视频.mp4"))  # noqa: SLF001


@pytest.mark.parametrize(
    "name",
    [
        "还在下载.pdf.tmp",
        "半个文件.crdownload",
        "迅雷.part",
        "微信缓存图.dat",  # 聊天里的图片是加密 dat，走"粘贴图片"那条路
        "缩略图.db",
        "~$打开着的合同.docx",  # Word 临时文件
    ],
)
def test_没写完和不给人看的一律跳过(tmp_path, name: str) -> None:
    assert not intake._is_interesting(造文件(tmp_path / name))  # noqa: SLF001


def test_空文件不算(tmp_path) -> None:
    路径 = tmp_path / "空的.pdf"
    路径.write_bytes(b"")
    assert not intake._is_interesting(路径)  # noqa: SLF001


def test_目录不算(tmp_path) -> None:
    (tmp_path / "一个目录.pdf").mkdir()
    assert not intake._is_interesting(tmp_path / "一个目录.pdf")  # noqa: SLF001


def test_转成SourceFile带上类型和大小(tmp_path) -> None:
    source = intake._to_source(造文件(tmp_path / "照片.png", 2048))  # noqa: SLF001
    assert source is not None
    assert source.kind == "image"
    assert source.is_image is True
    assert source.size == 2048
    assert source.name == "照片.png"


# ── 扫描 ────────────────────────────────────────────────────────
def test_只扫最近几天(tmp_path) -> None:
    """微信目录里可能攒了几年的文件，全列出来长辈根本没法用。"""
    新 = 造文件(tmp_path / "今天收到.pdf")
    旧 = 造文件(tmp_path / "去年的.pdf")
    很久以前 = time.time() - 30 * 86400
    import os

    os.utime(旧, (很久以前, 很久以前))

    names = [s.name for s in intake.scan([tmp_path], recent_days=3)]
    assert 新.name in names
    assert 旧.name not in names


def test_按时间倒序_最新的排最前(tmp_path) -> None:
    import os

    for index, name in enumerate(("第一个.pdf", "第二个.pdf", "第三个.pdf")):
        path = 造文件(tmp_path / name)
        os.utime(path, (time.time() - 100 + index * 10,) * 2)
    assert [s.name for s in intake.scan([tmp_path])] == ["第三个.pdf", "第二个.pdf", "第一个.pdf"]


def test_扫描会递归子目录(tmp_path) -> None:
    造文件(tmp_path / "2026-08" / "合同.pdf")
    assert [s.name for s in intake.scan([tmp_path])] == ["合同.pdf"]


def test_limit限制条数(tmp_path) -> None:
    for index in range(10):
        造文件(tmp_path / f"{index}.pdf")
    assert len(intake.scan([tmp_path], limit=4)) == 4


def test_目录不存在也不报错(tmp_path) -> None:
    assert intake.scan([tmp_path / "根本没有这个目录"]) == []


# ── 监控哪些目录 ────────────────────────────────────────────────
def test_待打印目录排在最前面(收件目录, tmp_path, monkeypatch) -> None:
    """长辈最常用的那个要排第一。"""
    微信 = tmp_path / "微信接收"
    微信.mkdir()
    dirs = intake.watch_dirs(IntakeConfig(watch_wechat=True, wechat_dirs=[str(微信)]))
    assert dirs[0] == 收件目录
    assert 微信 in dirs


def test_配置里手填的微信目录优先于自动探测(收件目录, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(intake, "detect_wechat_dirs", lambda: [tmp_path / "自动探测到的"])
    手填 = tmp_path / "手填的"
    手填.mkdir()
    dirs = intake.watch_dirs(IntakeConfig(wechat_dirs=[str(手填)]))
    assert 手填 in dirs
    assert (tmp_path / "自动探测到的") not in dirs


def test_关掉开关就不监控(收件目录, monkeypatch) -> None:
    monkeypatch.setattr(intake, "detect_wechat_dirs", lambda: [Path("C:/不该出现")])
    assert intake.watch_dirs(IntakeConfig(watch_inbox=False, watch_wechat=False)) == []


def test_重复目录只留一份(收件目录) -> None:
    config = IntakeConfig(wechat_dirs=[str(收件目录), str(收件目录).upper()])
    assert len(intake.watch_dirs(config)) == 1


def test_填了不存在的目录会被忽略(收件目录) -> None:
    dirs = intake.watch_dirs(IntakeConfig(wechat_dirs=["Z:/没有这个盘/微信"]))
    assert dirs == [收件目录]


def test_探测微信两代布局(tmp_path, monkeypatch) -> None:
    """3.x 和 4.x 目录结构不一样，装了哪种用哪种。"""
    documents = tmp_path / "Documents"
    legacy = documents / "WeChat Files" / "wxid_abc" / "FileStorage" / "File"
    modern = documents / "xwechat_files" / "wxid_hash" / "msg" / "file"
    legacy.mkdir(parents=True)
    modern.mkdir(parents=True)
    # 这几个不是聊天记录目录，不能当成接收目录
    (documents / "WeChat Files" / "All Users").mkdir(parents=True)
    (documents / "WeChat Files" / "Applet").mkdir(parents=True)
    monkeypatch.setattr(intake, "_documents_dir", lambda: documents)

    found = intake.detect_wechat_dirs()
    assert legacy in found
    assert modern in found
    assert len(found) == 2


def test_没装微信时探测不出东西(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(intake, "_documents_dir", lambda: tmp_path / "空的Documents")
    assert intake.detect_wechat_dirs() == []


# ── 拖拽 ────────────────────────────────────────────────────────
def test_拖进来能处理的和不认识的分开返回(tmp_path) -> None:
    """不认识的也要返回，否则界面只能说"有文件不支持"，
    长辈没法判断该让顾客重发哪一个。"""
    好的 = 造文件(tmp_path / "合同.pdf")
    坏的 = 造文件(tmp_path / "视频.mp4")
    accepted, rejected = intake.accept_dropped([好的, 坏的])
    assert [s.name for s in accepted] == ["合同.pdf"]
    assert rejected == [坏的]


def test_拖进来一个文件夹会展开里面的文件(tmp_path) -> None:
    folder = tmp_path / "顾客的文件"
    造文件(folder / "第一页.jpg")
    造文件(folder / "第二页.jpg")
    造文件(folder / "无关.mp4")
    accepted, rejected = intake.accept_dropped([folder])
    assert sorted(s.name for s in accepted) == ["第一页.jpg", "第二页.jpg"]
    assert rejected == []


def test_拖进来的旧文件也要收(tmp_path) -> None:
    """扫描首页要按最近几天过滤，但用户明确拖进来的不能因为"太旧"被丢掉。"""
    import os

    folder = tmp_path / "老文件夹"
    old = 造文件(folder / "两年前的合同.pdf")
    很久以前 = time.time() - 700 * 86400
    os.utime(old, (很久以前, 很久以前))
    accepted, _ = intake.accept_dropped([folder])
    assert [s.name for s in accepted] == ["两年前的合同.pdf"]


# ── 等文件写完 ──────────────────────────────────────────────────
def 造监控(directories, calls) -> intake.FileWatcher:
    return intake.FileWatcher(directories, calls.append, poll_interval=0.05)


def test_大小还在变就不回调(tmp_path) -> None:
    calls: list[intake.SourceFile] = []
    watcher = 造监控([tmp_path], calls)
    path = 造文件(tmp_path / "正在下载.pdf", 100)

    watcher._enqueue(path)  # noqa: SLF001
    assert watcher._collect_stable() == []  # noqa: SLF001 —— 第一次只记下大小
    path.write_bytes(b"x" * 5000)  # 又长大了
    assert watcher._collect_stable() == []  # noqa: SLF001
    assert watcher._collect_stable() == []  # noqa: SLF001 —— 连续不变一次还不够
    ready = watcher._collect_stable()  # noqa: SLF001
    assert [p.name for p, _ in ready] == ["正在下载.pdf"]


def test_写完了就只报一次(tmp_path) -> None:
    calls: list[intake.SourceFile] = []
    watcher = 造监控([tmp_path], calls)
    path = 造文件(tmp_path / "合同.pdf")
    watcher._enqueue(path)  # noqa: SLF001
    for _ in range(4):
        watcher._collect_stable()  # noqa: SLF001
    assert watcher._collect_stable() == []  # noqa: SLF001 —— 已经出队，不会重复


def test_中途被删掉不会报错(tmp_path) -> None:
    watcher = 造监控([tmp_path], [])
    path = 造文件(tmp_path / "一闪而过.pdf")
    watcher._enqueue(path)  # noqa: SLF001
    path.unlink()
    assert watcher._collect_stable() == []  # noqa: SLF001


def test_启动时已有的文件不当成新文件(tmp_path) -> None:
    """不然一开程序就弹一堆"新文件"，全是昨天的。"""
    path = 造文件(tmp_path / "昨天的.pdf")
    watcher = 造监控([tmp_path], [])
    watcher.prime(intake.scan([tmp_path]))
    watcher._enqueue(path)  # noqa: SLF001
    assert watcher._collect_stable() == []  # noqa: SLF001


def test_真的监控目录能收到新文件(tmp_path) -> None:
    """跑真的 watchdog + 轮询线程，验证整条线是通的。"""
    calls: list[intake.SourceFile] = []
    watcher = 造监控([tmp_path], calls)
    watcher.start()
    try:
        造文件(tmp_path / "顾客发来的.pdf")
        deadline = time.time() + 8
        while not calls and time.time() < deadline:
            time.sleep(0.05)
    finally:
        watcher.stop()
    assert [s.name for s in calls] == ["顾客发来的.pdf"]


# ── 剪贴板 / 存图 ───────────────────────────────────────────────
def test_粘贴的图片存进待打印目录(收件目录) -> None:
    image = np.full((40, 30, 3), 200, dtype=np.uint8)
    saved = intake.save_incoming_image(image, stem="粘贴的图片")
    assert saved.parent == 收件目录
    assert saved.suffix == ".png"
    assert saved.stat().st_size > 0
    assert "粘贴的图片" in saved.name


def test_剪贴板里没图返回None(monkeypatch) -> None:
    import PIL.ImageGrab

    monkeypatch.setattr(PIL.ImageGrab, "grabclipboard", lambda: None)
    assert intake.clipboard_image() is None


def test_剪贴板读失败也不抛(monkeypatch) -> None:
    import PIL.ImageGrab

    def 炸(*_args, **_kwargs):
        raise OSError("剪贴板被别的程序占着")

    monkeypatch.setattr(PIL.ImageGrab, "grabclipboard", 炸)
    assert intake.clipboard_image() is None
    assert intake.clipboard_files() == []


def test_剪贴板里是文件列表时按文件处理(tmp_path, monkeypatch) -> None:
    import PIL.ImageGrab

    好的 = 造文件(tmp_path / "合同.pdf")
    monkeypatch.setattr(
        PIL.ImageGrab, "grabclipboard", lambda: [str(好的), str(tmp_path / "视频.mp4")]
    )
    assert intake.clipboard_image() is None  # 是文件不是图
    assert [s.name for s in intake.clipboard_files()] == ["合同.pdf"]
