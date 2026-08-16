"""主窗口：首页四张大卡片 + 四个子页面。

首页一屏到底，没有菜单栏、没有工具栏、没有设置按钮 —— 设置入口刻意隐藏
（标题连点 5 次），因为长辈误触改坏配置的成本远高于自己改配置的便利。
见 docs/06-界面规范.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import paths, texts
from ..core import convert, history, intake
from ..core.enhance import EnhanceOptions
from ..texts import ErrorKind, friendly_error
from .page_inbox import InboxPage
from .page_ocr import OcrPage
from .page_photo import PhotoPage
from .page_print import PrintPage

logger = logging.getLogger(__name__)

_PAGE_HOME, _PAGE_PRINT, _PAGE_PHOTO, _PAGE_OCR, _PAGE_INBOX = range(5)
_SECRET_CLICKS = 5


def _file_filter() -> str:
    images = " ".join(f"*{s}" for s in sorted(convert.IMAGE_SUFFIXES))
    docs = " ".join(f"*{s}" for s in sorted(convert.OFFICE_SUFFIXES | convert.PDF_SUFFIXES))
    texts_ = " ".join(f"*{s}" for s in sorted(convert.TEXT_SUFFIXES))
    return (
        f"可以打印的文件 ({images} {docs} {texts_});;"
        f"图片 ({images});;办公文档和 PDF ({docs});;所有文件 (*)"
    )


class _WatchBridge(QObject):
    """监控线程 → 主线程。watchdog 的回调不在 Qt 线程里，必须靠信号转过来。"""

    newFile = Signal(object)


class TitleLabel(QLabel):
    """标题。连点 5 次打开设置 —— 藏起来防误触。"""

    secretActivated = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._clicks = 0

    def mousePressEvent(self, event) -> None:
        self._clicks += 1
        if self._clicks >= _SECRET_CLICKS:
            self._clicks = 0
            self.secretActivated.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, config: config_mod.AppConfig) -> None:
        super().__init__()
        self._config = config
        self._watcher: intake.FileWatcher | None = None
        self._new_count = 0

        self.setWindowTitle(texts.APP_TITLE)
        # 最小尺寸按 1366×768（Win10 老机器最常见的分辨率）留够余量：
        # 店铺机分辨率还没现场确认，窗口高度一旦超过可用工作区，
        # 底部那个「开始打印」大绿钮就会被任务栏压住 —— 主操作点不到是致命的。
        self.setMinimumSize(1024, 640)
        self.setAcceptDrops(True)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._home = self._build_home()
        self._print_page = PrintPage(config)
        self._photo_page = PhotoPage(config)
        self._ocr_page = OcrPage(config)
        self._inbox_page = InboxPage(config)

        for page in (
            self._home,
            self._print_page,
            self._photo_page,
            self._ocr_page,
            self._inbox_page,
        ):
            self._stack.addWidget(page)

        for page in (self._print_page, self._photo_page, self._ocr_page, self._inbox_page):
            page.back.connect(self.go_home)

        self._photo_page.printRequested.connect(self._print_with_enhance)
        self._inbox_page.printRequested.connect(self.open_print)
        self._inbox_page.enhanceRequested.connect(self.open_photo)
        self._inbox_page.ocrRequested.connect(self.open_ocr)
        self._inbox_page.pasteRequested.connect(self._paste_image)

        history.init()
        self._start_watching()

    # ── 首页 ────────────────────────────────────────────────────
    def _build_home(self) -> QWidget:
        from .widgets.big import BigCard

        page = QWidget()
        title = TitleLabel(texts.APP_TITLE)
        title.setProperty("role", "title")
        title.secretActivated.connect(self._open_settings)

        self._card_print = BigCard("📄", texts.HOME_CARD_PRINT_TITLE, texts.HOME_CARD_PRINT_HINT)
        self._card_photo = BigCard("🖼️", texts.HOME_CARD_PHOTO_TITLE, texts.HOME_CARD_PHOTO_HINT)
        self._card_ocr = BigCard("🔤", texts.HOME_CARD_OCR_TITLE, texts.HOME_CARD_OCR_HINT)
        self._card_inbox = BigCard("📥", texts.HOME_CARD_INBOX_TITLE, texts.HOME_CARD_INBOX_HINT)

        self._card_print.clicked.connect(self._pick_documents)
        self._card_photo.clicked.connect(lambda: self._pick_image(self.open_photo))
        self._card_ocr.clicked.connect(lambda: self._pick_image(self.open_ocr))
        self._card_inbox.clicked.connect(self.open_inbox)

        grid = QGridLayout()
        grid.setSpacing(22)
        grid.addWidget(self._card_print, 0, 0)
        grid.addWidget(self._card_photo, 0, 1)
        grid.addWidget(self._card_ocr, 1, 0)
        grid.addWidget(self._card_inbox, 1, 1)

        paste = QPushButton(texts.BTN_PASTE_IMAGE)
        paste.clicked.connect(self._paste_image)
        drop_hint = QLabel(texts.HOME_DROP_HINT)
        drop_hint.setProperty("role", "hint")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(paste)
        bottom.addStretch(1)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 28, 40, 30)
        layout.setSpacing(18)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(grid, stretch=1)
        layout.addLayout(bottom)
        layout.addWidget(drop_hint)
        return page

    # ── 页面跳转（公开的导航入口：内部信号和外部脚本都走这几个）──
    def go_home(self) -> None:
        self._stack.setCurrentIndex(_PAGE_HOME)

    # ── 选文件 ──────────────────────────────────────────────────
    def _pick_documents(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要打印的文件", str(self._start_dir()), _file_filter()
        )
        if files:
            self.open_print([Path(f) for f in files])

    def _pick_image(self, handler) -> None:
        images = " ".join(f"*{s}" for s in sorted(convert.IMAGE_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择照片", str(self._start_dir()), f"图片 ({images});;所有文件 (*)"
        )
        if path:
            handler(Path(path))

    def _start_dir(self) -> Path:
        """文件对话框默认打开"待打印"文件夹 —— 长辈平时就往那里存。"""
        if paths.INBOX_DIR.is_dir():
            return paths.INBOX_DIR
        return Path.home() / "Documents"

    # ── 页面跳转 ────────────────────────────────────────────────
    def open_print(self, files: list[Path], enhance: EnhanceOptions | None = None) -> None:
        self._print_page.load([Path(f) for f in files], enhance)
        self._stack.setCurrentIndex(_PAGE_PRINT)

    def _print_with_enhance(self, files: list, options: object) -> None:
        self.open_print([Path(f) for f in files], options)  # type: ignore[arg-type]

    def open_photo(self, path: Path) -> None:
        self._photo_page.load(Path(path))
        self._stack.setCurrentIndex(_PAGE_PHOTO)

    def open_ocr(self, path: Path) -> None:
        self._ocr_page.load(Path(path))
        self._stack.setCurrentIndex(_PAGE_OCR)

    def open_inbox(self) -> None:
        self._inbox_page.reload()
        self._new_count = 0
        self._card_inbox.set_badge(0)
        self._stack.setCurrentIndex(_PAGE_INBOX)

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._config, self)
        if dialog.exec():
            config_mod.save(self._config)
            self._restart_watching()
            self._ocr_page.sync_cloud_button()

    # ── 剪贴板与拖拽 ────────────────────────────────────────────
    def _paste_image(self) -> None:
        image = intake.clipboard_image()
        if image is None:
            files = intake.clipboard_files()
            if files:
                self.open_print([f.path for f in files])
                return
            QMessageBox.information(
                self, texts.APP_TITLE, friendly_error(ErrorKind.NO_IMAGE_IN_CLIPBOARD)
            )
            return
        saved = intake.save_incoming_image(image)
        self.open_photo(saved)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        items = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        accepted, rejected = intake.accept_dropped(items)
        if rejected:
            names = "、".join(p.name for p in rejected[:3])
            QMessageBox.information(
                self,
                texts.APP_TITLE,
                f"这些文件暂时打不了：{names}\n请让顾客发成 PDF 或者图片。",
            )
        if not accepted:
            return
        # 拖进来一张图片时直接进增强页（大概率是拍照文档），其余情况进打印页
        if len(accepted) == 1 and accepted[0].is_image:
            self.open_photo(accepted[0].path)
        else:
            self.open_print([item.path for item in accepted])
        event.acceptProposedAction()

    # ── 目录监控 ────────────────────────────────────────────────
    def _start_watching(self) -> None:
        directories = intake.watch_dirs(self._config.intake)
        if not directories:
            return
        self._bridge = _WatchBridge()
        self._bridge.newFile.connect(self._on_new_file, Qt.ConnectionType.QueuedConnection)
        watcher = intake.FileWatcher(directories, self._bridge.newFile.emit)
        watcher.prime(intake.scan(directories, self._config.intake.recent_days))
        try:
            watcher.start()
        except Exception:
            logger.exception("目录监控启动失败")
            return
        self._watcher = watcher

    def _restart_watching(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._start_watching()

    def _on_new_file(self, source: intake.SourceFile) -> None:
        logger.info("收到新文件：%s", source.path)
        if self._stack.currentIndex() == _PAGE_INBOX:
            self._inbox_page.reload()
        else:
            self._new_count += 1
            self._card_inbox.set_badge(self._new_count)

    def closeEvent(self, event) -> None:
        if self._watcher is not None:
            self._watcher.stop()
        config_mod.save(self._config)
        super().closeEvent(event)
