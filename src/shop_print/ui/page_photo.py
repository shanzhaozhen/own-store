"""照片变清楚再打印。v1 最核心的功能，界面上要一眼看出效果。

预览走缩略图（长边 1000px）实时出结果，只有真正打印或保存时才跑全分辨率 ——
不然长辈拖滑块的时候界面就卡住了。算法见 docs/03-图片增强算法.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import paths, texts
from ..config import AppConfig
from ..core import enhance as enhance_mod
from .page_base import SubPage
from .widgets.big import ChoiceGroup, PrimaryButton, StrengthSlider
from .widgets.preview import BeforeAfter
from .workers import run_async

logger = logging.getLogger(__name__)

_PREVIEW_DEBOUNCE_MS = 180

_MODE_OPTIONS = [
    (enhance_mod.MODE_AUTO, texts.MODE_AUTO),
    (enhance_mod.MODE_TEXT, texts.MODE_TEXT),
    (enhance_mod.MODE_MIXED, texts.MODE_MIXED),
    (enhance_mod.MODE_PHOTO, texts.MODE_PHOTO),
]
_MODE_HINTS = {
    enhance_mod.MODE_AUTO: texts.MODE_AUTO_HINT,
    enhance_mod.MODE_TEXT: texts.MODE_TEXT_HINT,
    enhance_mod.MODE_MIXED: texts.MODE_MIXED_HINT,
    enhance_mod.MODE_PHOTO: texts.MODE_PHOTO_HINT,
}


class PhotoPage(SubPage):
    printRequested = Signal(list, object)  # (文件列表, EnhanceOptions)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.HOME_CARD_PHOTO_TITLE, parent)
        self._config = config
        self._source: Path | None = None
        self._preview_source: np.ndarray | None = None

        self._compare = BeforeAfter()

        self._modes = ChoiceGroup(_MODE_OPTIONS, config.enhance.mode)
        self._modes.changed.connect(self._on_mode_changed)
        self._mode_hint = QLabel(_MODE_HINTS.get(config.enhance.mode, ""))
        self._mode_hint.setProperty("role", "hint")

        self._strength = StrengthSlider(config.enhance.strength)
        self._strength.changed.connect(lambda _: self._schedule_preview())
        self._strength_hint = QLabel(texts.STRENGTH_HINT)
        self._strength_hint.setProperty("role", "hint")

        self._print_button = PrimaryButton(texts.BTN_START_PRINT)
        self._print_button.setEnabled(False)
        self._print_button.clicked.connect(self._request_print)

        self._save = QPushButton("保存成图片")
        self._save.setEnabled(False)
        self._save.clicked.connect(self._save_full_resolution)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh_preview)

        # 说明文字和小标题挤在同一行：竖着排要多占两行，预览就被压小了 ——
        # 而"看出变清楚了"全靠这块预览，它优先。
        mode_row = QVBoxLayout()
        mode_row.setSpacing(6)
        mode_label = QLabel("这张是什么内容")
        mode_label.setProperty("role", "section")
        mode_head = QHBoxLayout()
        mode_head.setSpacing(14)
        mode_head.addWidget(mode_label)
        mode_head.addWidget(self._mode_hint)
        mode_head.addStretch(1)
        mode_row.addLayout(mode_head)
        mode_row.addWidget(self._modes)

        strength_row = QVBoxLayout()
        strength_row.setSpacing(6)
        strength_label = QLabel(texts.LABEL_STRENGTH)
        strength_label.setProperty("role", "section")
        strength_head = QHBoxLayout()
        strength_head.setSpacing(14)
        strength_head.addWidget(strength_label)
        strength_head.addWidget(self._strength_hint)
        strength_head.addStretch(1)
        strength_row.addLayout(strength_head)
        strength_row.addWidget(self._strength)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addWidget(self._save, stretch=1)
        buttons.addWidget(self._print_button, stretch=3)

        self.body.addWidget(self._compare, stretch=1)
        self.body.addLayout(mode_row)
        self.body.addLayout(strength_row)
        self.body.addLayout(buttons)

    # ── 外部入口 ────────────────────────────────────────────────
    def load(self, path: Path) -> None:
        self._source = Path(path)
        self.set_title(f"{texts.HOME_CARD_PHOTO_TITLE} —— {self._source.name}")
        self._print_button.setEnabled(False)
        self._save.setEnabled(False)
        self.show_busy(texts.BUSY_PROCESSING)
        run_async(
            self._load_preview_source,
            self._source,
            self._config.enhance.preview_max_side,
            on_done=self._on_source_loaded,
            on_failed=self.show_error,
        )

    def current_options(self) -> enhance_mod.EnhanceOptions:
        return enhance_mod.EnhanceOptions(
            mode=self._modes.current(),
            strength=self._strength.value(),
            deskew=self._config.enhance.auto_deskew,
        )

    # ── 预览 ────────────────────────────────────────────────────
    @staticmethod
    def _load_preview_source(path: Path, max_side: int) -> np.ndarray:
        image = enhance_mod.load_image(path)
        return enhance_mod.downscale(image, max_side)

    def _on_source_loaded(self, image: np.ndarray) -> None:
        self._preview_source = image
        self._compare.before.set_array(image)
        self._refresh_preview()

    def _on_mode_changed(self, mode: str) -> None:
        self._mode_hint.setText(_MODE_HINTS.get(mode, ""))
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._debounce.start()

    def _refresh_preview(self) -> None:
        if self._preview_source is None:
            return
        options = self.current_options()
        # 预览用的图已经缩过了，这里不再缩第二次
        run_async(
            enhance_mod.enhance,
            self._preview_source,
            options,
            on_done=self._on_preview_ready,
            on_failed=self.show_error,
        )

    def _on_preview_ready(self, result: enhance_mod.EnhanceResult) -> None:
        self._compare.after.set_array(result.image)
        self._print_button.setEnabled(True)
        self._save.setEnabled(True)
        detail = []
        if result.mode_used != self._modes.current():
            detail.append(f"自动判断为「{dict(_MODE_OPTIONS)[result.mode_used]}」")
        if result.cropped:
            detail.append("已自动裁正")
        elif abs(result.rotated_deg) > 0.05:
            detail.append(f"已自动转正 {abs(result.rotated_deg):.1f} 度")
        if detail:
            self.show_done("　".join(detail))
        else:
            self.clear_status()

    # ── 输出 ────────────────────────────────────────────────────
    def _request_print(self) -> None:
        if self._source is None:
            return
        self.printRequested.emit([self._source], self.current_options())

    def _save_full_resolution(self) -> None:
        if self._source is None:
            return
        self._save.setEnabled(False)
        self.show_busy(texts.BUSY_PROCESSING)
        run_async(
            self._do_save,
            self._source,
            self.current_options(),
            on_done=self._on_saved,
            on_failed=self._on_save_failed,
        )

    @staticmethod
    def _do_save(source: Path, options: enhance_mod.EnhanceOptions) -> Path:
        result = enhance_mod.enhance_file(source, options)
        target_dir = paths.output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}-已处理.png"
        enhance_mod.save_image(result.image, target)
        return target

    def _on_saved(self, target: Path) -> None:
        self._save.setEnabled(True)
        self.show_done(f"{texts.DONE_SAVED}\n{target}")

    def _on_save_failed(self, message: str) -> None:
        self._save.setEnabled(True)
        self.show_error(message)
