"""大尺寸控件。尺寸和字号对应 docs/06-界面规范.md 的硬性要求。"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ... import texts


def _passive(label: QLabel) -> QLabel:
    """让标签不吃鼠标事件，否则放在按钮里会把点击挡掉。"""
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class BigCard(QPushButton):
    """首页的大卡片。图标 + 一行大字 + 一行小字说明。

    文字用"要做的事"描述，不用功能名 —— 「照片变清楚再打印」而不是「图像增强」。
    """

    def __init__(self, icon: str, title: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        icon_label = _passive(QLabel(icon))
        icon_label.setStyleSheet("font-size: 46pt;")
        title_label = _passive(QLabel(title))
        title_label.setStyleSheet("font-size: 22pt; font-weight: bold;")
        title_label.setWordWrap(True)
        hint_label = _passive(QLabel(hint))
        hint_label.setProperty("role", "hint")
        hint_label.setWordWrap(True)

        layout.addWidget(icon_label)
        layout.addWidget(title_label)
        layout.addWidget(hint_label)

        self._badge = _passive(QLabel(""))
        self._badge.setStyleSheet(
            "font-size: 16pt; font-weight: bold; color: #ffffff;"
            "background: #e5442f; border-radius: 16px; padding: 2px 12px;"
        )
        self._badge.hide()
        layout.addWidget(self._badge, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_badge(self, count: int) -> None:
        if count > 0:
            self._badge.setText(f"{count} 个新文件")
            self._badge.show()
        else:
            self._badge.hide()


class PrimaryButton(QPushButton):
    """每个页面那个巨大的绿色主按钮。位置和颜色在所有页面保持一致。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class BackButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(texts.BTN_BACK, parent)
        self.setProperty("role", "back")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class NumberStepper(QWidget):
    """「− 3 +」大加减按钮。刻意不用输入框和微调框：长辈点不准小箭头，
    也容易误输入字母。"""

    valueChanged = Signal(int)

    def __init__(
        self,
        minimum: int = 1,
        maximum: int = 99,
        value: int = 1,
        parent: QWidget | None = None,
        step: int = 1,
        suffix: str = "",
    ) -> None:
        super().__init__(parent)
        self._min, self._max = minimum, maximum
        self._step = max(1, step)
        self._suffix = suffix
        self._value = max(minimum, min(value, maximum))

        self._minus = QPushButton("−")
        self._plus = QPushButton("+")
        for button in (self._minus, self._plus):
            button.setProperty("role", "stepper")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._display = QLabel(self._text())
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setStyleSheet("font-size: 26pt; font-weight: bold; min-width: 72px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._minus)
        layout.addWidget(self._display)
        layout.addWidget(self._plus)

        self._minus.clicked.connect(lambda: self.set_value(self._value - self._step))
        self._plus.clicked.connect(lambda: self.set_value(self._value + self._step))
        self._sync()

    def _text(self) -> str:
        return f"{self._value}{self._suffix}"

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        value = max(self._min, min(value, self._max))
        if value == self._value:
            return
        self._value = value
        self._display.setText(self._text())
        self._sync()
        self.valueChanged.emit(value)

    def _sync(self) -> None:
        self._minus.setEnabled(self._value > self._min)
        self._plus.setEnabled(self._value < self._max)


class ChoiceGroup(QWidget):
    """多选一的大按钮组，代替下拉框（下拉框要点两次，还会遮住内容）。

    `vertical=True` 时一行一个：打印机名字长（"KONICA MINOLTA 225i PCL6"），
    横着排三台就挤成一团、字还被截掉，竖着排反而看得清。
    """

    changed = Signal(str)

    def __init__(
        self,
        options: Iterable[tuple[str, str]],
        current: str = "",
        parent: QWidget | None = None,
        vertical: bool = False,
    ) -> None:
        super().__init__(parent)
        self._layout: QVBoxLayout | QHBoxLayout = (
            QVBoxLayout(self) if vertical else QHBoxLayout(self)
        )
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._current = ""
        self.set_options(options, current)

    def set_options(self, options: Iterable[tuple[str, str]], current: str = "") -> None:
        """换掉全部选项。打印机列表会变（插拔、改设置），所以要能重建。

        `setParent(None)` 那一步不能省：只 `deleteLater()` 的话旧按钮在真正被
        删掉之前还留在屏幕上，新旧按钮叠在一起，看着就是"按钮变形/残影"。
        """
        for button in self._buttons.values():
            self._group.removeButton(button)
            self._layout.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()

        for key, label in options:
            button = QPushButton(label)
            button.setProperty("role", "choice")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self._on_pick(k))
            self._group.addButton(button)
            self._buttons[key] = button
            self._layout.addWidget(button)

        if current in self._buttons:
            self._buttons[current].setChecked(True)
            self._current = current
        else:
            first = next(iter(self._buttons), "")
            if first:
                self._buttons[first].setChecked(True)
            self._current = first

    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        if key in self._buttons and key != self._current:
            self._buttons[key].setChecked(True)
            self._current = key

    def set_enabled_option(self, key: str, enabled: bool, reason: str = "") -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setEnabled(enabled)
            button.setToolTip(reason)

    def set_tooltip(self, key: str, text: str) -> None:
        """按钮上的字截短了（打印机名字很长），全名放这里。"""
        button = self._buttons.get(key)
        if button is not None:
            button.setToolTip(text)

    def _on_pick(self, key: str) -> None:
        if key != self._current:
            self._current = key
            self.changed.emit(key)


class StrengthSlider(QWidget):
    """两端写字、中间一条滑块，不显示数值。默认是「淡 ←→ 浓」。

    别的地方也用它（比如证件页的深浅、照片页的"裁剪边缘 紧 ←→ 松"），
    两端的字换一下就行 —— 长辈看得懂"往哪边拉"，看不懂百分数。
    """

    changed = Signal(int)

    def __init__(
        self,
        value: int = 50,
        parent: QWidget | None = None,
        low_label: str = texts.STRENGTH_LIGHT,
        high_label: str = texts.STRENGTH_HEAVY,
    ) -> None:
        super().__init__(parent)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(value)
        self._slider.setSingleStep(5)
        self._slider.setPageStep(10)
        self._slider.valueChanged.connect(self.changed.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        low = QLabel(low_label)
        high = QLabel(high_label)
        for label in (low, high):
            label.setStyleSheet("font-size: 18pt;")
        layout.addWidget(low)
        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(high)

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, value: int) -> None:
        self._slider.setValue(value)
