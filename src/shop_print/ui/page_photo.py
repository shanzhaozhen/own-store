"""照片变清楚再打印。v1 最核心的功能，界面上要一眼看出效果。

三件事跟着用户反馈改过：

- **先进页面再选图**：首页卡片点进来是一块空的图片框（点 / 拖都行），
  不再一上来就弹文件对话框
- **认出整张 A4/A3 纸就裁正拉平**：顾客拍的合同、证明基本都是标准纸，
  比例已知（√2），透视校正之后残留的误差也能修掉 —— 见 core/enhance._straighten
- **黑白 / 彩色**：店里那台机器只有黑白，彩色是给「保存成图片 / 保存成 PDF」用的
  （红章、蓝签要留住）

预览走缩略图（长边 1000px）实时出结果，只有真正打印或保存时才跑全分辨率 ——
不然长辈拖滑块的时候界面就卡住了。算法见 docs/03-图片增强算法.md。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import texts
from ..config import AppConfig
from ..core import convert
from ..core import enhance as enhance_mod
from .page_base import SubPage
from .widgets.big import ChoiceGroup, PrimaryButton, StrengthSlider
from .widgets.dropzone import DropFrame, ImageDropZone
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
_MONO, _COLOR = "mono", "color"
_COLOR_OPTIONS = [(_MONO, texts.COLOR_MONO), (_COLOR, texts.COLOR_COLOR)]
_CROP_AUTO, _CROP_OFF = "auto", "off"
_CROP_OPTIONS = [(_CROP_AUTO, texts.CROP_AUTO), (_CROP_OFF, texts.CROP_OFF)]
# 「边缘」滑块 0–100 ↔ 裁剪边缘 −5%…+5%，中间 50 = 不多不少
_MARGIN_SPAN = 0.05


def _margin_to_slider(margin: float) -> int:
    return round(50 + margin / _MARGIN_SPAN * 50)


def _slider_to_margin(value: int) -> float:
    return round((value - 50) / 50.0 * _MARGIN_SPAN, 4)


class PhotoPage(SubPage):
    printRequested = Signal(list, object)  # (文件列表, EnhanceOptions)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.HOME_CARD_PHOTO_TITLE, parent)
        self._config = config
        self._source: Path | None = None
        self._preview_source: np.ndarray | None = None
        self._preview_result: np.ndarray | None = None

        # 还没选图时占着预览的位置；选好之后换成左右对比。
        # 对比那一块也是能点、能拖的 —— 点一下就换图，不用退回首页
        self._zone = ImageDropZone()
        self._compare = BeforeAfter()
        self._compare_frame = DropFrame()
        self._compare_frame.setProperty("role", "plain")
        self._compare_frame.setToolTip(texts.PICK_ANOTHER_HINT)
        frame_layout = QVBoxLayout(self._compare_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(self._compare)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._zone)
        self._stack.addWidget(self._compare_frame)
        for zone in (self._zone, self._compare_frame):
            zone.pickRequested.connect(self._pick)
            zone.dropped.connect(self.load)

        self._modes = ChoiceGroup(_MODE_OPTIONS, config.enhance.mode)
        self._modes.changed.connect(self._on_mode_changed)
        self._mode_hint = QLabel(_MODE_HINTS.get(config.enhance.mode, ""))
        self._mode_hint.setProperty("role", "hint")

        self._colors = ChoiceGroup(_COLOR_OPTIONS, _COLOR if config.enhance.color else _MONO)
        self._colors.changed.connect(self._on_color_changed)
        color_hint = QLabel(texts.COLOR_HINT_PRINT_MONO)
        color_hint.setProperty("role", "hint")

        # 裁剪：用户反馈"有时候裁剪得太过了"。所以给两样东西 ——
        # 一个"干脆不裁"的开关，和一个"边缘留多少"的滑块（默认中间 = 不多不少）
        self._crop = ChoiceGroup(
            _CROP_OPTIONS, _CROP_AUTO if config.enhance.auto_deskew else _CROP_OFF
        )
        self._crop.changed.connect(self._on_crop_changed)
        self._crop_margin = StrengthSlider(
            _margin_to_slider(config.enhance.crop_margin),
            low_label=texts.CROP_TIGHT,
            high_label=texts.CROP_LOOSE,
        )
        self._crop_margin.changed.connect(self._on_margin_changed)

        self._strength = StrengthSlider(config.enhance.strength)
        self._strength.changed.connect(lambda _: self._schedule_preview())
        self._strength_hint = QLabel(texts.STRENGTH_HINT)
        self._strength_hint.setProperty("role", "hint")

        self._print_button = PrimaryButton(texts.BTN_START_PRINT)
        self._print_button.setProperty("compact", True)
        self._print_button.clicked.connect(self._request_print)
        self._save_image = QPushButton(texts.PHOTO_SAVE_IMAGE)
        self._save_image.clicked.connect(lambda: self._save(as_pdf=False))
        self._save_pdf = QPushButton(texts.PHOTO_SAVE_PDF)
        self._save_pdf.clicked.connect(lambda: self._save(as_pdf=True))

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh_preview)

        # 说明文字和小标题挤在同一行：竖着排要多占两行，预览就被压小了 ——
        # 而"看出变清楚了"全靠这块预览，它优先。
        mode_row = self._labeled_row("这张是什么内容", self._modes, self._mode_hint)
        color_row = self._labeled_row(texts.LABEL_COLOR_MODE, self._colors, color_hint)

        # 裁剪开关和"边缘"滑块挤一行：这一页控件已经够多了，能合的行就合
        crop_row = QHBoxLayout()
        crop_row.setSpacing(12)
        crop_label = QLabel(texts.LABEL_CROP)
        crop_label.setProperty("role", "section")
        margin_label = QLabel(texts.LABEL_CROP_MARGIN)
        margin_label.setProperty("role", "section")
        crop_row.addWidget(crop_label)
        crop_row.addWidget(self._crop)
        crop_row.addWidget(margin_label)
        crop_row.addWidget(self._crop_margin, stretch=1)

        strength_row = QHBoxLayout()
        strength_row.setSpacing(12)
        strength_label = QLabel(texts.LABEL_STRENGTH)
        strength_label.setProperty("role", "section")
        strength_row.addWidget(strength_label)
        strength_row.addWidget(self._strength, stretch=1)
        strength_row.addWidget(self._strength_hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addWidget(self._save_image, stretch=1)
        buttons.addWidget(self._save_pdf, stretch=1)
        buttons.addWidget(self._print_button, stretch=3)

        self.body.addWidget(self._stack, stretch=1)
        self.body.addLayout(mode_row)
        self.body.addLayout(color_row)
        self.body.addLayout(crop_row)
        self.body.addLayout(strength_row)
        self.body.addLayout(buttons)
        self._sync_crop_enabled()
        self._sync_buttons(False)

    @staticmethod
    def _labeled_row(title: str, group: ChoiceGroup, hint: QLabel) -> QHBoxLayout:
        """小标题 + 按钮组 + 说明，一行摆完。按钮保持自己的宽度，剩下的给说明。"""
        label = QLabel(title)
        label.setProperty("role", "section")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label)
        row.addWidget(group)
        row.addWidget(hint)
        row.addStretch(1)
        return row

    # ── 外部入口 ────────────────────────────────────────────────
    def load(self, path: Path | None = None) -> None:
        """`None` = 还没选图，显示那块"点这里选图片"的空地。

        首页卡片点进来就是这条路：**先进页面，再选图**（用户反馈的第 3 条）。
        """
        if path is None:
            self._source = None
            self._preview_source = None
            self._preview_result = None
            self.set_title(texts.HOME_CARD_PHOTO_TITLE)
            self._stack.setCurrentWidget(self._zone)
            self._sync_buttons(False)
            self.clear_status()
            return

        self._source = Path(path)
        self.set_title(f"{texts.HOME_CARD_PHOTO_TITLE} —— {self._source.name}")
        self._stack.setCurrentWidget(self._compare_frame)
        self._sync_buttons(False)
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
            deskew=self._crop.current() == _CROP_AUTO,
            color=self._colors.current() == _COLOR,
            crop_margin=_slider_to_margin(self._crop_margin.value()),
        )

    def _on_crop_changed(self, key: str) -> None:
        self._config.enhance.auto_deskew = key == _CROP_AUTO
        self._sync_crop_enabled()
        self._schedule_preview()

    def _on_margin_changed(self, value: int) -> None:
        self._config.enhance.crop_margin = _slider_to_margin(value)
        self._schedule_preview()

    def _sync_crop_enabled(self) -> None:
        """不裁的时候"边缘"滑块没有意义，直接置灰 —— 别让长辈拉了没反应。"""
        self._crop_margin.setEnabled(self._crop.current() == _CROP_AUTO)

    # ── 选图 ────────────────────────────────────────────────────
    def _pick(self) -> None:
        """开"选图片"对话框，默认打开工作区文件夹（设置里配的那个）。"""
        suffixes = " ".join(f"*{s}" for s in sorted(convert.IMAGE_SUFFIXES))
        start = config_mod.workspace_dir(self._config.intake)
        path, _ = QFileDialog.getOpenFileName(self, "选择照片", str(start), f"图片 ({suffixes})")
        if path:
            self.load(Path(path))

    def _sync_buttons(self, ready: bool) -> None:
        self._print_button.setEnabled(ready)
        self._save_image.setEnabled(ready)
        self._save_pdf.setEnabled(ready)

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
        self._config.enhance.mode = mode
        self._schedule_preview()

    def _on_color_changed(self, key: str) -> None:
        self._config.enhance.color = key == _COLOR
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._config.enhance.strength = self._strength.value()
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
        self._preview_result = result.image
        self._compare.after.set_array(result.image)
        self._sync_buttons(True)
        detail = []
        if result.mode_used != self._modes.current():
            detail.append(f"自动判断为「{dict(_MODE_OPTIONS)[result.mode_used]}」")
        if result.paper:
            detail.append(texts.PAPER_DETECTED)
        elif result.cropped:
            detail.append("已自动裁正")
        elif abs(result.rotated_deg) > 0.05:
            detail.append(f"已自动转正 {abs(result.rotated_deg):.1f} 度")
        if result.cropped:
            detail.append(texts.CROP_HINT)  # 裁到内容了怎么救，写在旁边
        if detail:
            self.show_done("　".join(detail))
        else:
            self.clear_status()

    # ── 输出 ────────────────────────────────────────────────────
    def _request_print(self) -> None:
        if self._source is None:
            return
        self.printRequested.emit([self._source], self.current_options())

    def _save(self, as_pdf: bool) -> None:
        """存全分辨率的结果。图片和 PDF 两个按钮，都存到设置里配的那个文件夹。"""
        if self._source is None:
            return
        self._sync_buttons(False)
        self.show_busy(texts.BUSY_PROCESSING)
        run_async(
            self._do_save,
            self._source,
            self.current_options(),
            config_mod.save_dir(self._config.output),
            as_pdf,
            on_done=self._on_saved,
            on_failed=self._on_save_failed,
        )

    @staticmethod
    def _do_save(
        source: Path, options: enhance_mod.EnhanceOptions, target_dir: Path, as_pdf: bool
    ) -> Path:
        """全分辨率跑一遍（预览是缩略图，不能拿来存）。"""
        result = enhance_mod.enhance_file(source, options)
        target_dir.mkdir(parents=True, exist_ok=True)
        if as_pdf:
            # 认出是整张 A4/A3 纸也按 A4 出：照片里分不出 A3 和 A4，店里默认 A4
            return convert.image_to_pdf(result.image, target_dir / f"{source.stem}-已处理.pdf")
        target = target_dir / f"{source.stem}-已处理.png"
        enhance_mod.save_image(result.image, target)
        return target

    def _on_saved(self, target: Path) -> None:
        self._sync_buttons(True)
        self.show_done(f"{texts.DONE_SAVED}\n{target}")

    def _on_save_failed(self, message: str) -> None:
        self._sync_buttons(True)
        self.show_error(message)
