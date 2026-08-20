"""文件从哪里来。

四条路，**长辈只需要会一条**（见 docs/01-环境与设备.md）：

1. 工作区文件夹（默认 `C:\\打印\\待打印`，设置里能改）—— 微信里"另存为"到这里
2. 微信接收目录 —— 以`文件`形式发来的会自动出现
3. 剪贴板 —— 微信里右键"复制"图片，回来点「粘贴图片」（对长辈最省事）
4. 拖拽 —— 直接把文件拖进窗口（UI 层处理）

注意聊天里的**图片**和**文件**不是一回事：以`文件`发来的落在 File 目录、
可以直接监控；聊天里直接发的图片在 Image 目录且是加密的 `.dat`，不做解密，
走上面第 3 条路。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import config as config_mod
from .. import paths
from ..config import IntakeConfig
from . import convert

logger = logging.getLogger(__name__)

# 明显是"还没写完"或"不是给人看的"文件，一律跳过
_IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload", ".download", ".!ut", ".dat", ".db"}
_IGNORED_PREFIXES = ("~$", ".~")

# 新文件要连续两次检查大小不变才算写完，避免读到半个文件
_STABLE_CHECKS = 2
_POLL_INTERVAL_SEC = 0.6


@dataclass
class SourceFile:
    path: Path
    kind: str  # pdf | image | office | text
    size: int
    modified: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


def _is_interesting(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(_IGNORED_PREFIXES):
        return False
    if path.suffix.lower() in _IGNORED_SUFFIXES:
        return False
    if not convert.is_supported(path):
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _to_source(path: Path) -> SourceFile | None:
    if not _is_interesting(path):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return SourceFile(
        path=path, kind=convert.classify(path), size=stat.st_size, modified=stat.st_mtime
    )


def _documents_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Documents"


def detect_wechat_dirs() -> list[Path]:
    """自动探测微信接收文件的目录。两代布局不一样，存在哪种用哪种。

    - 3.x：`Documents\\WeChat Files\\<wxid>\\FileStorage\\File\\<yyyy-MM>\\`
    - 4.x：`Documents\\xwechat_files\\<wxid_hash>\\msg\\file\\<yyyy-MM>\\`

    返回的是要**递归监控**的父目录（`File` / `file`），新月份文件夹会自己出现。
    店铺机上到底是哪种要现场确认，所以路径也允许在设置里改。
    """
    found: list[Path] = []
    documents = _documents_dir()

    legacy_root = documents / "WeChat Files"
    if legacy_root.is_dir():
        for account in legacy_root.iterdir():
            if not account.is_dir() or account.name in {"All Users", "Applet", "WMPF"}:
                continue
            candidate = account / "FileStorage" / "File"
            if candidate.is_dir():
                found.append(candidate)

    modern_root = documents / "xwechat_files"
    if modern_root.is_dir():
        for account in modern_root.iterdir():
            if not account.is_dir():
                continue
            candidate = account / "msg" / "file"
            if candidate.is_dir():
                found.append(candidate)

    return found


def watch_dirs(config: IntakeConfig) -> list[Path]:
    """最终要监控的目录列表。工作区排最前面，长辈最常用。"""
    dirs: list[Path] = []
    if config.watch_workspace:
        workspace = config_mod.workspace_dir(config)
        if workspace.is_dir():
            dirs.append(workspace)
    if config.watch_wechat:
        if config.wechat_dirs:
            dirs.extend(Path(d) for d in config.wechat_dirs if Path(d).is_dir())
        else:
            dirs.extend(detect_wechat_dirs())
    # 去重但保持顺序：工作区要排在最前面，长辈最常用
    seen: set[str] = set()
    unique: list[Path] = []
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def scan(directories: Iterable[Path], recent_days: int = 3, limit: int = 60) -> list[SourceFile]:
    """扫出最近的文件，按时间倒序。

    只看最近几天：微信目录里可能攒了几年的文件，全列出来长辈根本没法用。
    """
    cutoff = time.time() - recent_days * 86400
    result: list[SourceFile] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            for path in directory.rglob("*"):
                source = _to_source(path)
                if source is not None and source.modified >= cutoff:
                    result.append(source)
        except OSError:
            logger.warning("扫描目录失败：%s", directory, exc_info=True)
    result.sort(key=lambda s: s.modified, reverse=True)
    return result[:limit]


class FileWatcher:
    """监控目录，有新文件就回调。

    **必须等文件写完才回调。**微信下载、拷贝都是边写边长，读到半个文件会
    让长辈看到"这个文件坏了"。做法是记下候选文件，轮询到大小连续两次不变
    才算写完。watchdog 只负责发现变化，判断稳定靠自己的轮询线程。
    """

    def __init__(
        self,
        directories: Iterable[Path],
        on_new: Callable[[SourceFile], None],
        poll_interval: float = _POLL_INTERVAL_SEC,
    ) -> None:
        self._directories = [Path(d) for d in directories]
        self._on_new = on_new
        self._poll_interval = poll_interval
        self._observer = None
        self._pending: dict[Path, tuple[int, int]] = {}  # 路径 -> (上次大小, 连续不变次数)
        self._seen: set[Path] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None

    def prime(self, known: Iterable[SourceFile]) -> None:
        """把启动时已经扫到的文件标成"见过"，免得一启动就弹一堆"新文件"。"""
        with self._lock:
            self._seen.update(item.path for item in known)

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event) -> None:
                if not event.is_directory:
                    watcher._enqueue(Path(event.src_path))

            def on_modified(self, event) -> None:
                if not event.is_directory:
                    watcher._enqueue(Path(event.src_path))

            def on_moved(self, event) -> None:
                # 很多下载器是"先写 .tmp 再改名"，改名后的才是真文件
                if not event.is_directory:
                    watcher._enqueue(Path(event.dest_path))

        self._observer = Observer()
        handler = _Handler()
        for directory in self._directories:
            if directory.is_dir():
                self._observer.schedule(handler, str(directory), recursive=True)
                logger.info("开始监控：%s", directory)
        self._observer.start()

        self._stop.clear()
        self._poller = threading.Thread(target=self._run_poll, name="intake-poll", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
        if self._poller is not None:
            self._poller.join(timeout=3)
            self._poller = None

    def _enqueue(self, path: Path) -> None:
        with self._lock:
            if path in self._seen or not _is_interesting(path):
                return
            self._pending.setdefault(path, (-1, 0))

    def _run_poll(self) -> None:
        while not self._stop.wait(self._poll_interval):
            for path, source in self._collect_stable():
                self._seen.add(path)
                try:
                    self._on_new(source)
                except Exception:
                    logger.exception("处理新文件的回调出错：%s", path)

    def _collect_stable(self) -> list[tuple[Path, SourceFile]]:
        ready: list[tuple[Path, SourceFile]] = []
        with self._lock:
            for path in list(self._pending):
                try:
                    size = path.stat().st_size
                except OSError:
                    self._pending.pop(path, None)  # 又被删掉了
                    continue
                previous, stable_count = self._pending[path]
                if size == previous and size > 0:
                    stable_count += 1
                else:
                    stable_count = 0
                if stable_count >= _STABLE_CHECKS:
                    self._pending.pop(path, None)
                    source = _to_source(path)
                    if source is not None:
                        ready.append((path, source))
                else:
                    self._pending[path] = (size, stable_count)
        return ready


def clipboard_image() -> np.ndarray | None:
    """从剪贴板取图，返回 BGR；没有图就返回 None。

    这是给长辈准备的最短路径：在微信里右键图片→复制，回到工具点「粘贴图片」。
    比"另存为再去文件夹里找"少好几步，也绕开了微信图片缓存是加密 .dat 的问题。
    """
    import cv2
    from PIL import ImageGrab

    try:
        grabbed = ImageGrab.grabclipboard()
    except Exception:
        logger.warning("读剪贴板失败", exc_info=True)
        return None
    if grabbed is None or isinstance(grabbed, list):
        return None
    rgb = np.asarray(grabbed.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def clipboard_files() -> list[SourceFile]:
    """从剪贴板取文件（在资源管理器里 Ctrl+C 复制的文件）。"""
    from PIL import ImageGrab

    try:
        grabbed = ImageGrab.grabclipboard()
    except Exception:
        return []
    if not isinstance(grabbed, list):
        return []
    result = [_to_source(Path(item)) for item in grabbed]
    return [item for item in result if item is not None]


def accept_dropped(items: Iterable[str | Path]) -> tuple[list[SourceFile], list[Path]]:
    """处理拖进来的东西。返回 (能处理的, 不认识的)。

    不认识的也要返回，好让界面说清楚是哪个文件不行 —— 只说"有文件不支持"
    长辈没法判断该让顾客重发哪一个。
    """
    accepted: list[SourceFile] = []
    rejected: list[Path] = []
    for item in items:
        path = Path(item)
        if path.is_dir():
            accepted.extend(scan([path], recent_days=36500, limit=200))
            continue
        source = _to_source(path)
        if source is None:
            rejected.append(path)
        else:
            accepted.append(source)
    return accepted, rejected


def copy_into_workspace(sources: Iterable[Path], workspace: Path) -> list[Path]:
    """把拖进来 / 粘贴过来的文件拷进工作区，返回拷好的路径（顺序不变）。

    为什么要拷：工作区页面列的是"工作区里有什么"，文件还躺在桌面或者 U 盘上的话，
    列表里根本看不到它，长辈就以为拖丢了。拷过来之后这些文件也留在工作区里，
    第二天还能找到（这就是"历史工作区"）。

    同名不覆盖：后面加 `-2`、`-3`。已经在工作区里的文件原地不动，不复制一份。
    """
    workspace = Path(workspace)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("工作区建不出来，文件留在原处：%s", workspace, exc_info=True)
        return [Path(s) for s in sources]

    copied: list[Path] = []
    for raw in sources:
        source = Path(raw)
        if not source.is_file():
            continue
        if _same_dir(source.parent, workspace):
            copied.append(source)  # 本来就在工作区里
            continue
        target = _free_name(workspace / source.name)
        try:
            shutil.copy2(source, target)
        except OSError:
            logger.exception("拷进工作区失败：%s", source)
            copied.append(source)  # 拷不过去就用原路径，别把文件丢了
            continue
        copied.append(target)
    return copied


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def _free_name(target: Path) -> Path:
    """同名文件已经在了就加 -2、-3…… 不覆盖顾客的文件。"""
    if not target.exists():
        return target
    for index in range(2, 100):
        candidate = target.with_name(f"{target.stem}-{index}{target.suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{target.stem}-{time.strftime('%H%M%S')}{target.suffix}")


def save_incoming_image(
    image: np.ndarray, stem: str = "粘贴的图片", target_dir: Path | None = None
) -> Path:
    """把剪贴板/拖进来的图片存到工作区，后续流程和普通文件一样。

    没给目录就用默认工作区；连它也建不出来（C 盘没权限之类）就退到输出目录 ——
    图片先落盘比"报错让长辈重新复制一次"要好。
    """
    from .enhance import save_image

    if target_dir is None:
        target_dir = paths.WORKSPACE_DIR if paths.ensure_workspace_dir() else paths.output_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        target_dir = paths.output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = target_dir / f"{stem}-{stamp}.png"
    save_image(image, path)
    return path
