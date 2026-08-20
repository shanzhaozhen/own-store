"""证件印一张纸：身份证正反面、户口本两页拼到一张 A4，按实物尺寸打。

界面上只有四件事：**选类型 → 放两张图 → （看不清就拉一下深浅）→ 开始打印**。
尺寸、方向、排版全自动，长辈不用也不该去调 —— 算法见 core/cards.py。

两条硬规矩：

- **尺寸不保真就必须说出来**。派出所、银行要的是 1:1 的复印件，
  悄悄缩了打出来会被退回重做，比报错更糟。
- **预览要比按钮显眼**。用户反馈过"按钮比预览还大"：这一页控件多
  （类型 6 个 + 两个位置 + 深浅），一不小心就把 A4 预览挤成一小条。
  所以两个位置做成**能缩的图片框**（不是固定高度的按钮行），
  中间那一横排的高度全留给预览。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import texts
from ..config import AppConfig
from ..core import cards, convert, intake
from ..core import enhance as enhance_mod
from .page_base import SubPage
from .widgets.big import ChoiceGroup, PrimaryButton, StrengthSlider
from .widgets.preview import ImagePreview
from .workers import run_async

logger = logging.getLogger(__name__)

_PREVIEW_DPI = 96
# 框里的缩略图缩到这个长边：手机原图 4000px 拿去反复缩放会让窗口一顿一顿的
_THUMB_MAX_SIDE = 700
# 拖滑块时等一下再重画，不然每动一格都排一次版
_TONE_DEBOUNCE_MS = 200
_MONO, _COLOR = "mono", "color"
_COLOR_OPTIONS = [(_MONO, texts.COLOR_MONO), (_COLOR, texts.COLOR_COLOR)]


def _type_options() -> list[tuple[str, str]]:
    """类型按钮上的字。

    用**短名**（"银行卡"而不是"银行卡 / 社保卡"）：六个按钮要排在一行里，
    1024 宽的小屏上长名字会被截成"行卡 / 社保"，反而看不懂。
    全名进 tooltip，出结果时的说明文字仍然用全名。
    """
    options = [(preset.key, preset.name.split(" / ")[0]) for preset in cards.PRESETS]
    options.append((cards.AUTO, texts.CARDS_TYPE_AUTO))
    return options


class CardSlot(QFrame):
    """一个位置：一个**能点、能拖、能预览**的图片框。

    第一版是"名字 + 状态文字 + 三个按钮"一横排，用户反馈两条：
    看不见自己放进去的是哪张图，而且按钮把 A4 预览挤小了。
    现在框里直接显示缩略图，点框就是选图片，放好之后按钮只剩「转一下 / 去掉」。
    """

    changed = Signal()  # 放进来的图变了（换图 / 去掉）：抠图和朝向都得重算
    rotated = Signal()  # 只是点了「转一下」：算好的结果转一下就行，别重算
    pickRequested = Signal()  # 点了框：让页面去开"选图片"对话框
    dropped = Signal(object)  # 拖进来/选中的文件路径，交给页面去异步读图

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: np.ndarray | None = None
        self.source: Path | None = None
        self.rotation = 0  # 人工又转了几个 90°（自动摆正之后再叠上去）
        self.setProperty("role", "cardslot")
        self.setAcceptDrops(True)  # 拖一张图片进这个框就等于选图片
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 框要能跟着窗口缩：高度钉死的话小窗口下预览就没地方了。
        # 宽度的下限管的是"名字 + 两个按钮那一行别挤成一团"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(250, 120)

        self._title = QLabel(label)
        self._title.setProperty("role", "section")
        self._view = ImagePreview(texts.PICK_IMAGE_HINT)
        self._view.setMinimumSize(90, 70)  # 缩略图让位给 A4 预览，给得比它小
        self._view.setProperty("role", "thumb")

        self._paste = QPushButton(texts.CARDS_PASTE)
        self._rotate = QPushButton(texts.CARDS_ROTATE)
        self._rotate.setToolTip("自动摆正偶尔会判错，点一下转 90°")
        self._clear = QPushButton(texts.CARDS_CLEAR)
        for button in (self._paste, self._rotate, self._clear):
            button.setProperty("role", "mini")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paste.clicked.connect(self._on_paste)
        self._rotate.clicked.connect(self._on_rotate)
        self._clear.clicked.connect(self.clear)

        # 按钮和名字挤在**同一行**：单独给按钮一行要多占 52px，那 52px 给缩略图更值。
        # 用户第二轮反馈还是"按钮占的位置太多"，能合的行就得合。
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._paste)
        head.addWidget(self._rotate)
        head.addWidget(self._clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        layout.addLayout(head)
        layout.addWidget(self._view, stretch=1)
        self._sync()

    # ── 状态 ────────────────────────────────────────────────────
    def _sync(self) -> None:
        """按"放了没放"切按钮：空的时候只留「粘贴」，放好了给「转一下 / 去掉」。"""
        filled = self.filled
        self._paste.setVisible(not filled)
        self._rotate.setVisible(filled)
        self._clear.setVisible(filled)
        self.setProperty("filled", filled)
        _repolish(self)

    def set_label(self, label: str) -> None:
        self._title.setText(label)

    @property
    def label(self) -> str:
        return self._title.text()

    @property
    def filled(self) -> bool:
        return self.image is not None

    def set_image(
        self, image: np.ndarray, source: Path | None = None, thumb: np.ndarray | None = None
    ) -> None:
        self.image = image
        self.source = source
        self.rotation = 0
        # 缩略图给的是**放进去的原图**，不是处理后的 —— 长辈先要确认"这张是不是拿对了面"，
        # 处理成什么样右边那张 A4 预览会画出来
        self._view.set_array(
            thumb if thumb is not None else enhance_mod.downscale(image, _THUMB_MAX_SIDE)
        )
        self.setToolTip(str(source) if source else "")
        self._sync()
        self.changed.emit()

    def set_loading(self, source: Path) -> None:
        """图还在后台读，先把状态显示出来 —— 别让人以为点了没反应。"""
        self._view.set_message(f"{texts.CARDS_SLOT_LOADING}\n{source.name}")

    def clear(self) -> None:
        self.image = None
        self.source = None
        self.rotation = 0
        self._view.set_message(texts.PICK_IMAGE_HINT)
        self.setToolTip("")
        self._sync()
        self.changed.emit()

    # ── 交互 ────────────────────────────────────────────────────
    def _on_rotate(self) -> None:
        """人工兜底：转 90°。自动摆正判错时长辈点一下就好，不用重拍。"""
        if not self.filled:
            return
        self.rotation = (self.rotation + 1) % 4
        self.rotated.emit()

    def mousePressEvent(self, event) -> None:
        """点框上任何空地都等于「选图片」——「点这里选图片」就写在框里。

        对话框由页面去开：默认打开哪个文件夹要看设置里的工作区路径，
        那是页面才有的信息（框子不认识配置）。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.pickRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_paste(self) -> None:
        image = intake.clipboard_image()
        if image is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self, texts.APP_TITLE, texts.friendly_error(texts.ErrorKind.NO_IMAGE_IN_CLIPBOARD)
            )
            return
        self.set_image(image)

    # ── 拖进来 ──────────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:
        if _first_image(event.mimeData()) is not None:
            event.acceptProposedAction()
            self.setProperty("dropping", True)
            _repolish(self)

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dropping", False)
        _repolish(self)

    def dropEvent(self, event) -> None:
        path = _first_image(event.mimeData())
        self.setProperty("dropping", False)
        _repolish(self)
        if path is None:
            return
        event.acceptProposedAction()
        self.dropped.emit(path)


def _repolish(widget: QWidget) -> None:
    """property 选择器改了之后要重刷样式，否则边框颜色不会跟着变。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _first_image(mime) -> Path | None:
    """拖进来的东西里第一张能用的图片。不是图片就当没拖。"""
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if path.suffix.lower() in convert.IMAGE_SUFFIXES:
            return path
    return None


class CardsPage(SubPage):
    printRequested = Signal(list)  # [已经拼好的 PDF 路径]

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(texts.CARDS_TITLE, parent)
        self._config = config
        self._merged: Path | None = None
        self._job_seq = 0  # 排版任务的序号，用来丢掉迟到的旧结果
        # 每个位置算好的"怎么处理"（抠在哪、什么证件、转几下）。贵，所以缓存着；
        # 只有换图 / 换类型才作废，拖深浅滑块不作废
        self._plans: list[cards.CardPlan | None] = [None, None]

        default = config.cards.default_type
        self._types = ChoiceGroup(_type_options(), default)
        for preset in cards.PRESETS:  # 按钮上是短名，全名放 tooltip
            self._types.set_tooltip(preset.key, preset.name)
        self._types.set_tooltip(cards.AUTO, texts.CARDS_TYPE_AUTO_HINT)
        self._types.changed.connect(self._on_type_changed)

        front, back = cards.labels_for(default)
        self._slots = (CardSlot(front), CardSlot(back))
        for slot in self._slots:
            slot.changed.connect(lambda s=slot: self._on_slot_changed(s))
            slot.rotated.connect(self._recompose)
            slot.pickRequested.connect(lambda s=slot: self._pick_into(s))
            slot.dropped.connect(lambda path, s=slot: self._load_into(s, path))

        self._preview = ImagePreview("两张都选好就会显示打印出来的样子")
        # A4 预览是这一页的主角（长辈靠它确认位置和大小）。最小宽度按"竖着的 A4
        # 能画到控件区那么高"来给：1024×640 下控件区约 400px 高，纸就要 300px 宽。
        # 给得太小的话，左边一列一挤，预览就缩成一条了。
        self._preview.setMinimumSize(300, 280)

        # 深浅：拍照效果差别很大（逆光、暖光、旧证件），一个定值伺候不了所有照片。
        # 用户反馈"处理得有点过度，字看不清"，就是这个滑块要解决的事。
        self._strength = StrengthSlider(config.cards.strength)
        self._strength.changed.connect(self._on_strength_changed)
        self._tone_debounce = QTimer(self)
        self._tone_debounce.setSingleShot(True)
        self._tone_debounce.setInterval(_TONE_DEBOUNCE_MS)
        self._tone_debounce.timeout.connect(self._recompose)

        # 黑白 / 彩色：店里那台柯美只有黑白，**打印那条路不受影响**（驱动自己转灰），
        # 彩色是给「保存成 PDF / 另存为」用的 —— 红色国徽、蓝色签章要留住
        self._colors = ChoiceGroup(_COLOR_OPTIONS, _COLOR if config.cards.color else _MONO)
        self._colors.changed.connect(self._on_color_changed)

        # 三个按钮**一样高**（56px，规范给主按钮的下限）：一个 44px 的白按钮
        # 挨着一个 68px 的绿按钮，看着就像没做完。绿的仍然更宽 —— 它才是主操作。
        self._save = QPushButton(texts.CARDS_SAVE_PDF)
        self._save.clicked.connect(self._save_pdf)
        self._save_as = QPushButton(texts.CARDS_SAVE_AS)
        self._save_as.clicked.connect(self._save_pdf_as)
        self._print_button = PrimaryButton(texts.BTN_START_PRINT)
        self._print_button.setProperty("compact", True)
        self._print_button.clicked.connect(self._request_print)

        # 类型：小标题单独一行、按钮保持自己的宽度靠左。
        # 按钮不填满整行是刻意的（六个大方块会比预览还抢眼）；小标题放上面一行，
        # 是为了让**这一列的最小宽度只由按钮决定** —— 标题和按钮挤一行会把这一列
        # 撑到 740px，1024 的小屏上预览就只剩最小宽度了。
        type_label = QLabel(texts.CARDS_TYPE_LABEL)
        type_label.setProperty("role", "section")
        type_row = QHBoxLayout()
        type_row.setSpacing(10)
        type_row.addWidget(self._types)
        type_row.addStretch(1)

        # 两个图片框左右并排：正反面本来就是并排看的，长辈一眼能对上"哪张放哪儿"
        slots_row = QHBoxLayout()
        slots_row.setSpacing(12)
        for slot in self._slots:
            slots_row.addWidget(slot, stretch=1)

        # 深浅：说明文字和小标题挤一行，滑块自己占满一行 —— 说明放右边会把滑块
        # 挤到只剩 180px 宽，长辈的手拖不准。「黑白 / 彩色」也挤在这一行的右边
        strength_head = QHBoxLayout()
        strength_head.setSpacing(12)
        strength_label = QLabel(texts.CARDS_STRENGTH_LABEL)
        strength_label.setProperty("role", "section")
        strength_hint = QLabel(texts.CARDS_STRENGTH_HINT)
        strength_hint.setProperty("role", "hint")
        strength_head.addWidget(strength_label)
        strength_head.addWidget(strength_hint)
        strength_head.addStretch(1)
        strength_row = QVBoxLayout()
        strength_row.setSpacing(2)
        strength_row.addLayout(strength_head)
        strength_row.addWidget(self._strength)

        color_row = QHBoxLayout()
        color_row.setSpacing(12)
        color_label = QLabel(texts.LABEL_COLOR_MODE)
        color_label.setProperty("role", "section")
        color_hint = QLabel(texts.COLOR_HINT_PRINT_MONO)
        color_hint.setProperty("role", "hint")
        color_row.addWidget(color_label)
        color_row.addWidget(self._colors)
        color_row.addWidget(color_hint)
        color_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addWidget(self._save, stretch=1)
        buttons.addWidget(self._save_as, stretch=1)
        buttons.addWidget(self._print_button, stretch=3)

        # 控件全在左边一列，右边一列**从上到下全是预览** —— 类型、深浅这些行
        # 省下的高度就都归它了。刻意不再放"两张都选好才能打印"这类说明文字：
        # 框里已经写着「点这里选图片」，少一张就直接在状态栏喊「还差一张：国徽面」。
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(type_label)
        left.addLayout(type_row)
        left.addLayout(slots_row, stretch=1)  # 多出来的高度给图片框
        left.addLayout(strength_row)
        left.addLayout(color_row)

        middle = QHBoxLayout()
        middle.setSpacing(18)
        middle.addLayout(left, stretch=3)
        middle.addWidget(self._preview, stretch=2)

        self.body.addLayout(middle, stretch=1)
        self.body.addLayout(buttons)
        self._sync_buttons()

    # ── 外部入口 ────────────────────────────────────────────────
    def load(self, sources: Path | list[Path] | None = None) -> None:
        """把图片放进空位。从首页/收件页跳进来时用，一次给一张或两张都行。

        位置要**一次分配好**：读图是异步的，边读边看"哪个位置还空着"的话，
        两张图会抢同一个空位（第二张把第一张顶掉）。
        """
        if sources is None:
            return
        items = [sources] if isinstance(sources, str | Path) else list(sources)
        空位 = [slot for slot in self._slots if not slot.filled]
        目标 = 空位 + [slot for slot in self._slots if slot not in 空位]
        for source, slot in zip(items, 目标, strict=False):
            self._load_into(slot, Path(source))

    def _pick_into(self, slot: CardSlot) -> None:
        """开"选图片"对话框，默认打开工作区文件夹（设置里配的那个）。"""
        suffixes = " ".join(f"*{s}" for s in sorted(convert.IMAGE_SUFFIXES))
        start = config_mod.workspace_dir(self._config.intake)
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择{slot.label}的图片", str(start), f"图片 ({suffixes})"
        )
        if path:
            self._load_into(slot, Path(path))

    def _load_into(self, slot: CardSlot, path: Path) -> None:
        """读图放到工作线程：手机拍的图十几兆，在界面线程里解码会卡住窗口。

        缩略图也在线程里缩好一起带回来 —— 4000px 的原图在界面线程里缩一次
        要几十毫秒，两张就够看出一顿。
        """
        slot.set_loading(path)
        run_async(
            self._read_image,
            path,
            on_done=lambda pair: slot.set_image(pair[0], path, thumb=pair[1]),
            on_failed=lambda message: self._on_load_failed(slot, message),
        )

    @staticmethod
    def _read_image(path: Path) -> tuple[np.ndarray, np.ndarray]:
        image = enhance_mod.load_image(path)
        return image, enhance_mod.downscale(image, _THUMB_MAX_SIDE)

    def _on_load_failed(self, slot: CardSlot, message: str) -> None:
        slot.clear()
        self.show_error(message)

    # ── 交互 ────────────────────────────────────────────────────
    def _on_type_changed(self, key: str) -> None:
        front, back = cards.labels_for(key)
        self._slots[0].set_label(front)
        self._slots[1].set_label(back)
        self._config.cards.default_type = key
        self._plans = [None, None]  # 换了类型，抠图和朝向都要重算
        self._recompose()

    def _on_slot_changed(self, slot: CardSlot) -> None:
        """这个位置放的图变了 → 它那份"怎么处理"作废，重新抠一次。

        「转一下」走的是另一条（`rotated`）：那个只要把算好的结果转 90°，
        不用重新抠图 + OCR，省 1–2 秒。
        """
        self._plans[self._slots.index(slot)] = None
        self._sync_buttons()
        self._recompose()

    def _on_strength_changed(self, value: int) -> None:
        """拖深浅滑块：只重画，不重新抠图。滑块停下来 200ms 才真的动手。"""
        self._config.cards.strength = value
        if all(slot.filled for slot in self._slots):
            self._tone_debounce.start()

    def _on_color_changed(self, key: str) -> None:
        """黑白 / 彩色：同样只重画（抠图和朝向跟颜色无关）。"""
        self._config.cards.color = key == _COLOR
        if all(slot.filled for slot in self._slots):
            self._recompose()

    def _sync_buttons(self) -> None:
        ready = self._merged is not None
        self._print_button.setEnabled(ready)
        self._save.setEnabled(ready)
        self._save_as.setEnabled(ready)

    def _missing_label(self) -> str:
        return next((slot.label for slot in self._slots if not slot.filled), "")

    def _recompose(self) -> None:
        self._tone_debounce.stop()
        self._merged = None
        self._sync_buttons()
        missing = self._missing_label()
        if missing:
            self._preview.set_message("两张都选好就会显示打印出来的样子")
            if any(slot.filled for slot in self._slots):
                self.show_error(texts.CARDS_NEED_TWO.format(missing))
            else:
                self.clear_status()
            return

        # 两张都已经抠好过了（只是改了深浅或者转了一下）→ 提示写"重画"，别写"正在处理照片"
        重算 = any(plan is None for plan in self._plans)
        self.show_busy(texts.BUSY_CARDS if 重算 else texts.BUSY_CARDS_TONE)
        jobs = [
            (slot.image, slot.label, slot.rotation, plan)
            for slot, plan in zip(self._slots, self._plans, strict=True)
        ]
        # 每次排一个号：拖滑块时会连着起好几个任务，回来的顺序不一定和发出去的一样，
        # 迟到的旧结果必须丢掉，否则预览和滑块对不上
        self._job_seq += 1
        seq = self._job_seq
        run_async(
            self._compose,
            jobs,
            self._types.current(),
            self._strength.value(),
            self._config.cards.gap_mm,
            self._colors.current() == _COLOR,
            on_done=lambda result, n=seq: self._on_composed(result, n),
            on_failed=lambda message, n=seq: self._on_compose_failed(message, n),
        )

    @staticmethod
    def _compose(
        jobs: list[tuple[np.ndarray, str, int, cards.CardPlan | None]],
        type_key: str,
        strength: int,
        gap_mm: float,
        color: bool = False,
    ) -> tuple[Path, cards.Layout, bytes, list[cards.CardPlan]]:
        """在工作线程里跑：（必要时）抠卡片摆正 → 按深浅重画 → 排版 → PDF → 预览图。

        `plan` 不是 None 就直接拿来用：抠图 + OCR 判朝向要 1–2 秒，
        拖一下滑块重跑一遍的话，界面就等成了"点了没反应"。
        """
        items: list[cards.CardItem] = []
        plans: list[cards.CardPlan] = []
        for image, label, rotation, plan in jobs:
            if plan is None:
                plan = cards.analyze_card(image, type_key)
            plans.append(plan)
            items.append(
                cards.rotate_item(
                    cards.render_card(plan, strength, label=label, color=color), rotation
                )
            )
        pdf_path, layout = cards.merge_to_pdf(items, gap_mm=gap_mm)
        return pdf_path, layout, convert.render_page_png(pdf_path, 0, dpi=_PREVIEW_DPI), plans

    def _on_composed(
        self, result: tuple[Path, cards.Layout, bytes, list[cards.CardPlan]], seq: int
    ) -> None:
        if seq != self._job_seq:
            return  # 过期的结果：用户又动了滑块 / 换了图，别把界面拉回旧状态
        pdf_path, layout, preview, plans = result
        self._merged = pdf_path
        self._plans = list(plans)
        self._preview.set_png(preview)
        self._sync_buttons()
        说明 = cards.describe(layout)
        备注 = "　".join(
            dict.fromkeys(p.item.note for p in layout.placements if p.item.note)
        )  # 去重保序
        # 一行写完（不换行）：状态是 20pt 大字，多一行就吃掉 33px 的预览高度。
        # 真的长了 QLabel 自己会折行 —— 那种情况本来就该占地方
        整句 = f"{说明}　{备注}" if 备注 else 说明
        if layout.exact_size:
            self.show_done(整句)
        else:
            self.show_error(整句)

    def _on_compose_failed(self, message: str, seq: int) -> None:
        if seq == self._job_seq:
            self.show_error(message)

    # ── 输出 ────────────────────────────────────────────────────
    def _request_print(self) -> None:
        if self._merged is not None:
            self.printRequested.emit([self._merged])

    def _save_pdf(self) -> None:
        """一键存到设置里配的那个文件夹（默认 `%LOCALAPPDATA%\\ShopPrint\\output`）。

        想自己挑地方就点旁边的「另存为…」—— 两条路都留着：柜台上连着干活时
        一键存最省事，顾客要求存到 U 盘时才需要挑文件夹。
        """
        if self._merged is None:
            return
        target = config_mod.save_dir(self._config.output) / self._merged.name
        self._copy_to(target)

    def _save_pdf_as(self) -> None:
        """另存为：自己挑文件夹和文件名，选过的文件夹下次还开这里。"""
        if self._merged is None:
            return
        start = config_mod.dialog_dir(self._config.output) / self._merged.name
        chosen, _ = QFileDialog.getSaveFileName(
            self, texts.CARDS_SAVE_AS, str(start), "PDF 文件 (*.pdf)"
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")
        self._config.output.last_save_dir = str(target.parent)
        self._copy_to(target)

    def _copy_to(self, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self._merged.read_bytes())  # type: ignore[union-attr]
        except OSError:
            logger.exception("保存证件 PDF 失败：%s", target)
            self.show_error("存不下这个文件，看看这个文件夹在不在、硬盘满不满")
            return
        self.show_done(f"{texts.DONE_SAVED}\n{target}")
