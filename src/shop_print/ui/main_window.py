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
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import texts
from ..core import history, intake
from ..core.enhance import EnhanceOptions
from ..core.errors import ShopPrintError
from ..texts import ErrorKind
from .page_cards import CardsPage
from .page_ocr import OcrPage
from .page_photo import PhotoPage
from .page_print import PrintPage
from .page_workspace import WorkspacePage
from .workers import run_async

logger = logging.getLogger(__name__)

_PAGE_HOME, _PAGE_PRINT, _PAGE_PHOTO, _PAGE_OCR, _PAGE_WORKSPACE, _PAGE_CARDS = range(6)
_SECRET_CLICKS = 5


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
        self._workspace_page = WorkspacePage(config)
        self._cards_page = CardsPage(config)

        for page in (
            self._home,
            self._print_page,
            self._photo_page,
            self._ocr_page,
            self._workspace_page,
            self._cards_page,
        ):
            self._stack.addWidget(page)

        for page in (
            self._print_page,
            self._photo_page,
            self._ocr_page,
            self._workspace_page,
            self._cards_page,
        ):
            page.back.connect(self.go_home)

        self._photo_page.printRequested.connect(self._print_with_enhance)
        # 证件那条路必须按实物尺寸打：缩了复印件就作废了
        self._cards_page.printRequested.connect(
            lambda files: self.open_print(files, actual_size=True)
        )
        self._workspace_page.printRequested.connect(self.open_print)
        self._workspace_page.enhanceRequested.connect(self.open_photo)
        self._workspace_page.ocrRequested.connect(self.open_ocr)
        self._workspace_page.cardRequested.connect(self.open_cards)
        self._workspace_page.pasteRequested.connect(self._paste_image)

        history.init()
        self._start_watching()

    # ── 首页 ────────────────────────────────────────────────────
    def _build_home(self) -> QWidget:
        from .widgets.big import BigCard

        page = QWidget()
        title = TitleLabel(texts.APP_TITLE)
        title.setProperty("role", "title")
        title.secretActivated.connect(self._open_settings)

        self._card_photo = BigCard("🖼️", texts.HOME_CARD_PHOTO_TITLE, texts.HOME_CARD_PHOTO_HINT)
        self._card_ocr = BigCard("🔤", texts.HOME_CARD_OCR_TITLE, texts.HOME_CARD_OCR_HINT)
        self._card_cards = BigCard("🪪", texts.HOME_CARD_CARDS_TITLE, texts.HOME_CARD_CARDS_HINT)
        self._card_workspace = BigCard(
            "📁", texts.HOME_CARD_WORKSPACE_TITLE, texts.HOME_CARD_WORKSPACE_HINT
        )
        self._card_paste = BigCard("📋", texts.HOME_CARD_PASTE_TITLE, texts.HOME_CARD_PASTE_HINT)

        # 「照片变清楚」「照片转文字」点进去**先到页面**，在页面上再选图片
        # （用户反馈：不要一点卡片就弹文件对话框）
        self._card_photo.clicked.connect(lambda: self.open_photo(None))
        self._card_ocr.clicked.connect(lambda: self.open_ocr(None))
        self._card_cards.clicked.connect(lambda: self.open_cards(None))
        self._card_workspace.clicked.connect(lambda: self.open_workspace())
        self._card_paste.clicked.connect(self._paste_image)

        # 没有「打印文档」这一项：原件是 Word/Excel/PDF 的时候直接在 Office 里
        # 改好再打就行，工具在这上面帮不了忙（还多一道转换）。
        # 需要打印的入口都在具体的活儿里：照片变清楚 / 证件 / 工作区里的文件。
        grid = QGridLayout()
        grid.setSpacing(18)
        for index, card in enumerate(
            (
                self._card_photo,
                self._card_ocr,
                self._card_cards,
                self._card_workspace,
                self._card_paste,
            )
        ):
            grid.addWidget(card, index // 3, index % 3)

        drop_hint = QLabel(texts.HOME_DROP_HINT)
        drop_hint.setProperty("role", "hint")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 22, 32, 22)
        layout.setSpacing(14)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(grid, stretch=1)
        layout.addWidget(drop_hint)
        return page

    # ── 页面跳转（公开的导航入口：内部信号和外部脚本都走这几个）──
    def go_home(self) -> None:
        self._stack.setCurrentIndex(_PAGE_HOME)

    # ── 页面跳转 ────────────────────────────────────────────────
    def open_print(
        self,
        files: list[Path],
        enhance: EnhanceOptions | None = None,
        actual_size: bool = False,
    ) -> None:
        self._print_page.load([Path(f) for f in files], enhance, actual_size)
        self._stack.setCurrentIndex(_PAGE_PRINT)

    def _print_with_enhance(self, files: list, options: object) -> None:
        self.open_print([Path(f) for f in files], options)  # type: ignore[arg-type]

    def open_photo(self, path: Path | None = None) -> None:
        """不带路径时进空页面，让长辈在页面上点那块框选图片。"""
        self._photo_page.load(Path(path) if path is not None else None)
        self._stack.setCurrentIndex(_PAGE_PHOTO)

    def open_ocr(self, path: Path | None = None) -> None:
        self._ocr_page.load(Path(path) if path is not None else None)
        self._stack.setCurrentIndex(_PAGE_OCR)

    def open_workspace(self, note: str = "") -> None:
        """打开工作区页。`note` 是扫完文件之后要显示的一句话（比如刚拖进来了什么）。"""
        self._workspace_page.reload(note)
        self._new_count = 0
        self._card_workspace.set_badge(0)
        self._stack.setCurrentIndex(_PAGE_WORKSPACE)

    def open_cards(self, sources: Path | list[Path] | None = None) -> None:
        """证件二合一。带路径时把图片放进空位（一张或两张都行）。"""
        self._cards_page.load(sources)
        self._stack.setCurrentIndex(_PAGE_CARDS)

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self._config, self)
        if dialog.exec():
            config_mod.save(self._config)
            self._restart_watching()
            self._ocr_page.sync_cloud_button()

    # ── 剪贴板与拖拽：都落到工作区 ──────────────────────────────
    def _paste_image(self) -> None:
        """粘贴剪贴板里的图片 / 文件，落进工作区。

        **不直接跳到某个功能页**（用户反馈的第 4 条）：粘的东西是图片还是文件、
        要拿它做什么（打印 / 变清楚 / 拼证件 / 转文字），只有长辈自己知道。
        所以统一存进工作区，再由他在工作区那一页点对应的按钮。

        取图和存盘都丢到后台线程：手机拍的图有十几兆，在界面线程里编码 PNG
        会让窗口卡住几秒 —— Windows 这时候画的是一张定格的旧画面，看起来
        就像"按钮变形卡死"。
        """
        self.statusBar().showMessage(texts.BUSY_PROCESSING, 3000)
        run_async(
            self._grab_clipboard,
            config_mod.workspace_dir(self._config.intake),
            on_done=self._on_landed,
            on_failed=lambda message: QMessageBox.information(self, texts.APP_TITLE, message),
        )

    @staticmethod
    def _grab_clipboard(workspace: Path) -> list[Path]:
        """剪贴板 → 工作区里的文件列表。在工作线程里跑，所以不碰任何界面对象。"""
        image = intake.clipboard_image()
        if image is not None:
            return [intake.save_incoming_image(image, target_dir=workspace)]
        files = intake.clipboard_files()
        if files:
            return intake.copy_into_workspace([f.path for f in files], workspace)
        raise ShopPrintError(ErrorKind.NO_IMAGE_IN_CLIPBOARD, "剪贴板里没有图片也没有文件")

    def _on_landed(self, files: list[Path]) -> None:
        """文件已经进工作区了：打开工作区页，新文件排在最前面。"""
        note = ""
        if files:
            names = "、".join(p.name for p in files[:3])
            note = f"已经放进工作区：{names}\n点文件上的按钮选要做什么"
        self.open_workspace(note)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """拖进来的文件也**先进工作区**，让长辈在那里挑要做什么。"""
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
        event.acceptProposedAction()
        self.statusBar().showMessage(texts.BUSY_PROCESSING, 3000)
        run_async(
            intake.copy_into_workspace,
            [item.path for item in accepted],
            config_mod.workspace_dir(self._config.intake),
            on_done=self._on_landed,
            on_failed=lambda message: QMessageBox.information(self, texts.APP_TITLE, message),
        )

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
        if self._stack.currentIndex() == _PAGE_WORKSPACE:
            self._workspace_page.reload()
        else:
            self._new_count += 1
            self._card_workspace.set_badge(self._new_count)

    def closeEvent(self, event) -> None:
        if self._watcher is not None:
            self._watcher.stop()
        config_mod.save(self._config)
        super().closeEvent(event)
