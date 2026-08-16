"""子页面的公共骨架。

所有子页面固定三段：**大预览 → 少量大控件 → 一个巨大的主按钮**，
左上角永远有同一个位置的「← 返回」。见 docs/06-界面规范.md。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .widgets.big import BackButton


class SubPage(QWidget):
    back = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._back_button = BackButton()
        self._back_button.clicked.connect(self.back.emit)

        self._title = QLabel(title)
        self._title.setProperty("role", "title")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._back_button)
        header.addSpacing(12)
        header.addWidget(self._title)
        header.addStretch(1)

        self.status = QLabel("")
        self.status.setProperty("role", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.hide()

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(28, 20, 28, 24)
        self._outer.setSpacing(14)
        self._outer.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        self._outer.addLayout(self.body, stretch=1)
        self._outer.addWidget(self.status)
        self._outer.addWidget(self.progress)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    # ── 状态提示：又大又明确，长辈不会漏看 ──────────────────────
    def show_busy(self, message: str) -> None:
        self.status.setProperty("role", "status")
        self.status.setText(message)
        self.status.show()
        self._repolish(self.status)
        self.progress.setRange(0, 0)  # 不确定进度时来回跑
        self.progress.show()

    def show_progress(self, current: int, total: int, message: str) -> None:
        self.status.setProperty("role", "status")
        self.status.setText(message)
        self.status.show()
        self._repolish(self.status)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.progress.show()

    def show_done(self, message: str) -> None:
        self.progress.hide()
        self.status.setProperty("role", "ok")
        self.status.setText(message)
        self.status.show()
        self._repolish(self.status)

    def show_error(self, message: str) -> None:
        self.progress.hide()
        self.status.setProperty("role", "warn")
        self.status.setText(message)
        self.status.show()
        self._repolish(self.status)

    def clear_status(self) -> None:
        self.progress.hide()
        self.status.hide()
        self.status.setText("")

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """property 选择器改了之后要重刷样式，否则颜色不会变。"""
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
