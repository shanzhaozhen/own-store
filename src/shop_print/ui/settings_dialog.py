"""设置对话框。**这是给店主/维护者看的，不是给长辈看的。**

入口刻意藏在标题连点 5 次里，所以这里可以用常规控件密度，
不必守首页那套大按钮规矩。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import paths
from ..config import AppConfig
from ..core import intake, ocr_cloud, printing
from .page_ocr import open_in_explorer

logger = logging.getLogger(__name__)


class FolderRow(QWidget):
    """一行"文件夹路径 + 选择… + 打开"。留空 = 用默认值（提示里写着默认是哪儿）。"""

    def __init__(self, value: str, default: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._default = default
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText(f"留空 = {default}")
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick)
        show = QPushButton("打开")
        show.clicked.connect(lambda: open_in_explorer(Path(self.value() or str(default))))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._edit, stretch=1)
        row.addWidget(pick)
        row.addWidget(show)

    def value(self) -> str:
        return self._edit.text().strip()

    def _pick(self) -> None:
        start = self.value() or str(self._default)
        chosen = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if chosen:
            self._edit.setText(chosen)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置（维护用）")
        self.setMinimumWidth(680)
        self._config = config

        self._printer = QComboBox()
        self._printer.addItem("（用系统默认打印机）", "")
        try:
            for info in printing.list_printers():
                label = info.name + ("　【离线】" if info.offline else "")
                self._printer.addItem(label, info.name)
        except Exception:
            logger.warning("列打印机失败", exc_info=True)
        self._select_printer(config.printing.printer)

        self._dpi = QSpinBox()
        self._dpi.setRange(150, 600)
        self._dpi.setSingleStep(50)
        self._dpi.setValue(config.printing.dpi)

        self._backend = QComboBox()
        self._backend.addItem("GDI 直接打印（默认）", "gdi")
        self._backend.addItem("SumatraPDF 备选", "sumatra")
        self._backend.setCurrentIndex(0 if config.printing.backend != "sumatra" else 1)

        self._price = QDoubleSpinBox()
        self._price.setRange(0.0, 99.0)
        self._price.setDecimals(2)
        self._price.setSingleStep(0.1)
        self._price.setValue(config.printing.price_per_page)

        self._workspace = FolderRow(config.intake.workspace_dir, paths.WORKSPACE_DIR)
        self._watch_workspace = QCheckBox("盯着工作区文件夹，有新文件就提示")
        self._watch_workspace.setChecked(config.intake.watch_workspace)
        self._watch_wechat = QCheckBox("监控微信接收目录")
        self._watch_wechat.setChecked(config.intake.watch_wechat)

        self._save_dir = FolderRow(config.output.dir, paths.output_dir())

        self._wechat_dirs = QPlainTextEdit("\n".join(config.intake.wechat_dirs))
        self._wechat_dirs.setPlaceholderText("一行一个目录；留空则自动探测")
        self._wechat_dirs.setFixedHeight(90)
        detected = QLabel(
            "自动探测到："
            + ("；".join(str(d) for d in intake.detect_wechat_dirs()) or "（没找到）")
        )
        detected.setWordWrap(True)

        self._recent_days = QSpinBox()
        self._recent_days.setRange(1, 60)
        self._recent_days.setValue(config.intake.recent_days)

        self._cloud_provider = QComboBox()
        self._cloud_provider.addItem("（不用云端）", "")
        for name in ocr_cloud.available():
            self._cloud_provider.addItem(name, name)
        index = self._cloud_provider.findData(config.ocr.cloud_provider)
        self._cloud_provider.setCurrentIndex(max(0, index))
        self._cloud_key = QLineEdit(config.ocr.cloud_api_key)
        self._cloud_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._cloud_endpoint = QLineEdit(config.ocr.cloud_endpoint)

        form = QFormLayout()
        form.addRow("打印机", self._printer)
        form.addRow("打印分辨率 (dpi)", self._dpi)
        form.addRow("打印后端", self._backend)
        form.addRow("每张收费（元）", self._price)
        form.addRow("工作区文件夹", self._workspace)
        form.addRow("保存到哪个文件夹", self._save_dir)
        form.addRow(self._watch_workspace)
        form.addRow(self._watch_wechat)
        form.addRow("微信目录（手填优先）", self._wechat_dirs)
        form.addRow("", detected)
        form.addRow("只显示最近几天", self._recent_days)
        form.addRow("云端识别 provider", self._cloud_provider)
        form.addRow("云端 API Key", self._cloud_key)
        form.addRow("云端 Endpoint", self._cloud_endpoint)

        open_logs = QPushButton("打开日志文件夹")
        open_logs.clicked.connect(lambda: open_in_explorer(paths.log_dir()))
        open_data = QPushButton("打开数据文件夹")
        open_data.clicked.connect(lambda: open_in_explorer(paths.data_dir()))
        tools = QHBoxLayout()
        tools.addWidget(open_logs)
        tools.addWidget(open_data)
        tools.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(tools)
        layout.addWidget(buttons)

    def _select_printer(self, name: str) -> None:
        index = self._printer.findData(name)
        self._printer.setCurrentIndex(max(0, index))

    def _accept(self) -> None:
        self._config.printing.printer = self._printer.currentData() or ""
        self._config.printing.dpi = self._dpi.value()
        self._config.printing.backend = self._backend.currentData() or "gdi"
        self._config.printing.price_per_page = float(self._price.value())
        self._config.intake.workspace_dir = self._workspace.value()
        self._config.intake.watch_workspace = self._watch_workspace.isChecked()
        self._config.intake.watch_wechat = self._watch_wechat.isChecked()
        self._config.intake.wechat_dirs = [
            line.strip() for line in self._wechat_dirs.toPlainText().splitlines() if line.strip()
        ]
        self._config.intake.recent_days = self._recent_days.value()
        self._config.output.dir = self._save_dir.value()
        self._config.ocr.cloud_provider = self._cloud_provider.currentData() or ""
        self._config.ocr.cloud_api_key = self._cloud_key.text().strip()
        self._config.ocr.cloud_endpoint = self._cloud_endpoint.text().strip()
        # 路径改了就先把目录建出来，别等到保存文件时才发现建不了
        config_mod.workspace_dir(self._config.intake)
        config_mod.save_dir(self._config.output)
        self.accept()
