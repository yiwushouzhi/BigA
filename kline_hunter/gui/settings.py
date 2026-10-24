from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_NAME, ORG_NAME, AppConfig


def _saved_str(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)
    if value is None or str(value).strip() == "":
        return default
    return str(value)


def load_saved_config(base: AppConfig | None = None) -> AppConfig:
    base = base or AppConfig()
    settings = QSettings(ORG_NAME, APP_NAME)
    data_dir = _saved_str(settings, "data_dir", str(base.data_dir))
    try:
        key_interval = float(settings.value("tdx_key_interval", base.tdx_key_interval))
    except (TypeError, ValueError):
        key_interval = base.tdx_key_interval
    return base.with_overrides(
        data_dir=data_dir,
        deepseek_api_key=_saved_str(settings, "deepseek_api_key", base.deepseek_api_key),
        deepseek_base_url=_saved_str(settings, "deepseek_base_url", base.deepseek_base_url),
        deepseek_model=_saved_str(settings, "deepseek_model", base.deepseek_model),
        tdx_window_keyword=_saved_str(settings, "tdx_window_keyword", base.tdx_window_keyword),
        tdx_key_interval=key_interval,
    )


def save_config(config: AppConfig) -> None:
    settings = QSettings(ORG_NAME, APP_NAME)
    settings.setValue("data_dir", str(config.data_dir))
    settings.setValue("deepseek_api_key", config.deepseek_api_key)
    settings.setValue("deepseek_base_url", config.deepseek_base_url)
    settings.setValue("deepseek_model", config.deepseek_model)
    settings.setValue("tdx_window_keyword", config.tdx_window_keyword)
    settings.setValue("tdx_key_interval", config.tdx_key_interval)
    settings.sync()


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self._config = config

        self.data_dir_edit = QLineEdit(str(config.data_dir))
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.addWidget(self.data_dir_edit, 1)
        dir_row.addWidget(browse)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)

        self.key_edit = QLineEdit(config.deepseek_api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...（也可用环境变量 DEEPSEEK_API_KEY）")
        self.base_url_edit = QLineEdit(config.deepseek_base_url)
        self.model_edit = QLineEdit(config.deepseek_model)
        self.tdx_title_edit = QLineEdit(config.tdx_window_keyword)
        self.key_interval = QDoubleSpinBox()
        self.key_interval.setRange(0.002, 0.2)
        self.key_interval.setSingleStep(0.002)
        self.key_interval.setDecimals(3)
        self.key_interval.setValue(config.tdx_key_interval)
        self.key_interval.setSuffix(" 秒")

        form = QFormLayout()
        form.addRow("数据目录", dir_widget)
        form.addRow("DeepSeek API Key", self.key_edit)
        form.addRow("DeepSeek Base URL", self.base_url_edit)
        form.addRow("DeepSeek 模型", self.model_edit)
        form.addRow("通达信窗口标题关键字", self.tdx_title_edit)
        form.addRow("方向键间隔", self.key_interval)

        note = QLabel(
            "提示：通达信联动仅支持 Windows；API Key 保存在本机 QSettings 中，"
            "不会上传到除 DeepSeek 之外的服务。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        start = self.data_dir_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择数据目录", start)
        if selected:
            self.data_dir_edit.setText(selected)

    def accept(self) -> None:
        raw_dir = self.data_dir_edit.text().strip()
        if not raw_dir:
            QMessageBox.warning(self, "数据目录不能为空", "请选择本地数据目录。")
            return
        data_dir = Path(raw_dir).expanduser()
        try:
            (data_dir / "daily").mkdir(parents=True, exist_ok=True)
            (data_dir / "index").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "目录不可用", f"无法创建数据目录:\n{exc}")
            return
        self._config = self._config.with_overrides(
            data_dir=data_dir,
            deepseek_api_key=self.key_edit.text().strip(),
            deepseek_base_url=self.base_url_edit.text().strip(),
            deepseek_model=self.model_edit.text().strip(),
            tdx_window_keyword=self.tdx_title_edit.text().strip() or "通达信",
            tdx_key_interval=self.key_interval.value(),
        )
        super().accept()

    @property
    def config(self) -> AppConfig:
        return self._config


class UpdateDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("更新行情数据")
        self.setMinimumWidth(520)

        self.codes_edit = QLineEdit()
        self.codes_edit.setPlaceholderText("留空 = 全部 A 股；多个代码用逗号分隔")
        self.refresh_list = QCheckBox("刷新股票列表（首次使用必须勾选）")
        self.refresh_list.setChecked(True)
        self.full_refresh = QCheckBox("全量重拉（很慢，只在本地数据异常时使用）")

        note = QLabel(
            "默认使用增量更新：每只股票只拉最近 "
            "120 根 K 线并合并。首次更新会拉取较长的历史数据。\n"
            "行情服务器可能限流，更新过程中请耐心等待。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始更新")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("股票代码"))
        layout.addWidget(self.codes_edit)
        layout.addWidget(self.refresh_list)
        layout.addWidget(self.full_refresh)
        layout.addWidget(note)
        layout.addWidget(buttons)

    @property
    def codes(self) -> list[str]:
        return [x.strip() for x in self.codes_edit.text().split(",") if x.strip()]

    @property
    def should_refresh_list(self) -> bool:
        return self.refresh_list.isChecked()

    @property
    def is_full_refresh(self) -> bool:
        return self.full_refresh.isChecked()
