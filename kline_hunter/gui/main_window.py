from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import QDate, QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..engine import ScanReport, Screener
from ..locator import is_windows
from ..rules import ScreenSpec, humanize_spec
from ..storage import StockStorage
from .settings import SettingsDialog, UpdateDialog, save_config
from .workers import LocateWorker, ScanWorker, UpdateWorker

EXAMPLES = [
    ("放巨量长下影阳线", "找放巨量、长下影的阳线"),
    ("20日线上方缩量回踩10日线收阳", "收盘价站上20日线，缩量回踩10日线附近且收阳"),
    ("涨幅超5%且放量一倍", "涨幅超过5%，成交量比20日均量放大一倍以上"),
]

RESULT_HEADERS = ["代码", "名称", "日期", "收盘", "涨跌幅", "成交量", "成交额", "bar"]


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.storage = StockStorage(config)
        self.screener = Screener(self.storage)
        self._results = []
        self._spec: ScreenSpec | None = None
        self._worker = None
        self._worker_kind = ""

        self.setWindowTitle("K线智能筛选定位器")
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._refresh_counts()
        self._apply_style()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 10)
        root.setSpacing(12)

        title = QLabel("K线智能筛选定位器")
        title.setObjectName("title")
        subtitle = QLabel("大白话描述形态 → AI 生成条件 → 扫描本地全市场 → 双击结果定位通达信")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        root.addWidget(self._build_prompt_box())
        root.addWidget(self._build_range_box())
        root.addWidget(self._build_result_box(), 1)

        self.data_status = QLabel("")
        self.status_label = QLabel("就绪")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.statusBar().addWidget(self.data_status, 1)
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.setCentralWidget(central)

    def _build_prompt_box(self) -> QGroupBox:
        box = QGroupBox("筛选条件")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "例如：找最近一年，成交量是20日均量3倍以上，并且下影线长度超过实体2倍的阳线"
        )
        self.prompt_edit.setFixedHeight(82)
        row.addWidget(self.prompt_edit, 1)

        side = QVBoxLayout()
        self.example_combo = QComboBox()
        self.example_combo.addItem("插入示例条件…", "")
        for label, text in EXAMPLES:
            self.example_combo.addItem(label, text)
        self.example_combo.activated.connect(self._insert_example)
        side.addWidget(self.example_combo)

        self.scan_btn = QPushButton("AI 筛选")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.setMinimumHeight(34)
        self.scan_btn.clicked.connect(self.start_scan)
        side.addWidget(self.scan_btn)

        buttons = QHBoxLayout()
        self.update_btn = QPushButton("更新数据")
        self.update_btn.clicked.connect(self.start_update)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.clicked.connect(self.open_settings)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_current)
        buttons.addWidget(self.update_btn)
        buttons.addWidget(self.settings_btn)
        buttons.addWidget(self.stop_btn)
        side.addLayout(buttons)
        side.addStretch(1)

        row.addLayout(side)
        layout.addLayout(row)

        self.spec_label = QLabel("解析结果：等待筛选")
        self.spec_label.setObjectName("specLabel")
        self.spec_label.setWordWrap(True)
        layout.addWidget(self.spec_label)
        return box

    def _build_range_box(self) -> QGroupBox:
        box = QGroupBox("筛选范围")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.start_edit = QDateEdit(QDate.currentDate().addYears(-1))
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_edit = QDateEdit(QDate.currentDate())
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("yyyy-MM-dd")
        self.all_history = QCheckBox("全部历史")
        self.all_history.toggled.connect(self._toggle_history)

        self.codes_edit = QLineEdit()
        self.codes_edit.setPlaceholderText("留空 = 全部A股；多个代码用逗号分隔，例如 000001,600000")
        self.exclude_st = QCheckBox("排除 ST")
        self.exclude_st.setChecked(True)
        self.min_bars_box = QCheckBox("排除上市不足 60 个交易日")
        self.min_bars_box.setToolTip("用本地已有K线数量近似判断，本地数据不完整时可能误判")

        grid.addWidget(QLabel("时间范围"), 0, 0)
        date_row = QHBoxLayout()
        date_row.addWidget(self.start_edit)
        date_row.addWidget(QLabel("至"))
        date_row.addWidget(self.end_edit)
        date_row.addWidget(self.all_history)
        date_row.addStretch(1)
        grid.addLayout(date_row, 0, 1, 1, 3)

        grid.addWidget(QLabel("股票代码"), 1, 0)
        grid.addWidget(self.codes_edit, 1, 1, 1, 3)

        checks = QHBoxLayout()
        checks.addWidget(self.exclude_st)
        checks.addWidget(self.min_bars_box)
        checks.addStretch(1)
        grid.addLayout(checks, 2, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        return box

    def _build_result_box(self) -> QGroupBox:
        box = QGroupBox("筛选结果")
        layout = QVBoxLayout(box)

        header = QHBoxLayout()
        self.count_label = QLabel("尚未筛选")
        self.count_label.setObjectName("countLabel")
        header.addWidget(self.count_label, 1)
        hint = QLabel("双击行 → 定位通达信")
        hint.setStyleSheet("color:#666;")
        header.addWidget(hint)
        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_results)
        header.addWidget(self.export_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, len(RESULT_HEADERS))
        self.table.setHorizontalHeaderLabels(RESULT_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        header_view = self.table.horizontalHeader()
        for col in range(len(RESULT_HEADERS)):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        return box

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f7fb; color: #1f2937; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #111827; }
            QLabel#subtitle { color: #6b7280; margin-bottom: 2px; }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: #374151;
            }
            QPlainTextEdit, QLineEdit, QDateEdit, QComboBox {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 5px 7px;
                font-weight: 400;
            }
            QPlainTextEdit:focus, QLineEdit:focus, QDateEdit:focus, QComboBox:focus {
                border: 1px solid #3b82f6;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #f3f4f6; }
            QPushButton#primary {
                background: #2563eb;
                border: 1px solid #2563eb;
                color: white;
                font-weight: 600;
            }
            QPushButton#primary:hover { background: #1d4ed8; }
            QPushButton:disabled { color: #9ca3af; background: #f3f4f6; }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                gridline-color: #eef2f7;
                font-weight: 400;
            }
            QHeaderView::section {
                background: #f8fafc;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                padding: 7px;
                font-weight: 600;
                color: #374151;
            }
            QLabel#specLabel, QLabel#countLabel {
                color: #475569;
                font-weight: 400;
            }
            QStatusBar { background: #f5f7fb; color: #64748b; }
            """
        )

    # ---------------------------------------------------------------- helpers
    def _insert_example(self, index: int) -> None:
        text = self.example_combo.itemData(index)
        if text:
            self.prompt_edit.setPlainText(str(text))
        self.example_combo.setCurrentIndex(0)

    def _toggle_history(self, checked: bool) -> None:
        self.start_edit.setEnabled(not checked)
        self.end_edit.setEnabled(not checked)

    def _refresh_counts(self) -> None:
        try:
            counts = self.storage.counts()
        except Exception as exc:
            self.data_status.setText(f"本地数据读取失败: {exc}")
            return
        self.data_status.setText(
            f"本地数据：{counts['with_data']} 只股票有K线 | 目录：{self.config.data_dir}"
        )
        self.data_status.setToolTip(str(self.config.data_dir))

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.update_btn.setEnabled(not busy)
        self.settings_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

    def _start_worker(self, worker, kind: str) -> None:
        self._worker = worker
        self._worker_kind = kind
        worker.finished.connect(self._on_worker_finished)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        worker.start()

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._worker_kind = ""
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        self.status_label.setText(title)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.progress.setFormat(f"{done}/{total}")
        else:
            self.progress.setRange(0, 0)
        if message:
            self.status_label.setText(message)

    # ---------------------------------------------------------------- scan
    def start_scan(self) -> None:
        question = self.prompt_edit.toPlainText().strip()
        if not question:
            QMessageBox.information(self, "请输入条件", "请先用一句话描述要筛选的K线形态。")
            self.prompt_edit.setFocus()
            return
        codes = [x.strip() for x in self.codes_edit.text().split(",") if x.strip()]
        invalid = [c for c in codes if not (c.isdigit() and len(c) == 6)]
        if invalid:
            QMessageBox.warning(
                self, "股票代码错误", f"以下代码不是6位数字:\n{', '.join(invalid)}"
            )
            return

        if self.all_history.isChecked():
            start_date, end_date = None, None
        else:
            start_date = self.start_edit.date().toPyDate()
            end_date = self.end_edit.date().toPyDate()
            if start_date > end_date:
                QMessageBox.warning(self, "日期错误", "开始日期不能晚于结束日期")
                return

        self.spec_label.setText("解析结果：正在调用 DeepSeek…")
        self.spec_label.setToolTip("")
        worker = ScanWorker(
            self.config,
            question,
            start_date=start_date,
            end_date=end_date,
            codes=codes or None,
            exclude_st=self.exclude_st.isChecked(),
            min_bars=60 if self.min_bars_box.isChecked() else 0,
        )
        worker.parsed.connect(self._on_spec_parsed)
        worker.progress.connect(self._on_progress)
        worker.report_ready.connect(self._on_scan_report)
        worker.failed.connect(lambda msg: self._show_error("筛选失败", msg))
        self.status_label.setText("AI 解析中…")
        self._start_worker(worker, "scan")

    def _on_spec_parsed(self, spec: ScreenSpec) -> None:
        self._spec = spec
        text = humanize_spec(spec) or spec.description or "（无条件）"
        self.spec_label.setText(f"解析结果：{spec.description or text}\n条件：{text}")
        self.spec_label.setToolTip(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
        self.progress.setRange(0, 0)
        self.status_label.setText("条件解析完成，开始扫描本地K线…")

    def _on_scan_report(self, report: ScanReport) -> None:
        self._results = report.results
        if report.spec is not None:
            self._spec = report.spec
        self._fill_table(report.results)
        self.export_btn.setEnabled(bool(report.results))
        cancelled = any("取消" in e for e in report.errors)
        prefix = "扫描已停止" if cancelled else "扫描完成"
        self.count_label.setText(
            f"{prefix}：命中 {report.matched_stocks} 只 / {len(report.results)} 条 "
            f"（扫描 {report.scanned} 只，耗时 {report.elapsed_seconds:.2f}s）"
        )
        self.status_label.setText(self.count_label.text())
        if report.errors:
            self.status_label.setToolTip("\n".join(report.errors[:30]))
        if report.results:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

    def _clear_table(self) -> None:
        self.table.setRowCount(0)
        self._results = []
        self.export_btn.setEnabled(False)
        self.count_label.setText("尚未筛选")
        self.status_label.setToolTip("")

    def _fill_table(self, results) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            code_item = QTableWidgetItem(r.code)
            code_item.setData(Qt.ItemDataRole.UserRole, i)
            name_item = QTableWidgetItem(r.name)
            date_item = QTableWidgetItem(r.date_str)
            close_item = _right_item(f"{r.close:.2f}")
            pct_text = "—" if r.pct_chg is None else f"{r.pct_chg:+.2f}%"
            pct_item = _right_item(pct_text)
            if r.pct_chg is not None:
                pct_item.setForeground(QColor("#dc2626" if r.pct_chg >= 0 else "#16a34a"))
            vol_item = _right_item(f"{r.vol:,.0f}")
            amount_item = _right_item(f"{r.amount / 1e8:,.2f}亿" if r.amount else "—")
            bar_item = _right_item(str(r.bar_index))
            for col, item in enumerate(
                [
                    code_item,
                    name_item,
                    date_item,
                    close_item,
                    pct_item,
                    vol_item,
                    amount_item,
                    bar_item,
                ]
            ):
                self.table.setItem(i, col, item)
        self.table.setSortingEnabled(False)
        self.table.resizeRowsToContents()

    def _selected_result(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._results):
            return None
        return self._results[index]

    def _on_row_double_clicked(self, index) -> None:
        self.table.setCurrentCell(index.row(), 0)
        self.locate_selected()

    def _show_context_menu(self, pos: QPoint) -> None:
        if not self._results:
            return
        menu = QMenu(self)
        locate = menu.addAction("定位到通达信")
        copy = menu.addAction("复制股票代码")
        export = menu.addAction("导出 Excel")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == locate:
            self.locate_selected()
        elif action == copy:
            result = self._selected_result()
            if result:
                QApplication.clipboard().setText(result.code)
                self.status_label.setText(f"已复制 {result.code}")
        elif action == export:
            self.export_results()

    # ---------------------------------------------------------------- locate
    def locate_selected(self) -> None:
        result = self._selected_result()
        if result is None:
            self.status_label.setText("请先在结果表中选中一行")
            return
        if not is_windows():
            QMessageBox.information(
                self,
                "仅支持 Windows",
                "通达信联动依赖 Windows 窗口控制，当前系统只能完成筛选和导出。",
            )
            return
        self.status_label.setText(f"正在定位 {result.code} {result.date_str}…")
        worker = LocateWorker(
            self.config,
            result.code,
            result.bar_index,
            result.total_bars,
        )
        worker.progress.connect(self._on_progress)
        worker.done.connect(lambda msg: self.status_label.setText(msg))
        worker.failed.connect(lambda msg: self._show_error("定位失败", msg))
        self._start_worker(worker, "locate")

    # ---------------------------------------------------------------- update
    def start_update(self) -> None:
        dialog = UpdateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        codes = dialog.codes
        refresh = dialog.should_refresh_list
        if not codes and not refresh:
            QMessageBox.warning(
                self, "缺少股票列表", "更新全部A股时必须勾选“刷新股票列表”。"
            )
            return
        if not codes and not self.storage.list_stocks(include_inactive=False):
            QMessageBox.information(
                self, "首次更新", "首次使用会先刷新股票列表，再逐只拉取日线，耗时较长。"
            )
        worker = UpdateWorker(
            self.config,
            codes=codes or None,
            refresh_list=refresh,
            incremental=not dialog.is_full_refresh,
        )
        worker.progress.connect(self._on_progress)
        worker.report_ready.connect(self._on_update_report)
        worker.failed.connect(lambda msg: self._show_error("更新失败", msg))
        self.status_label.setText("准备更新行情…")
        self._start_worker(worker, "update")

    def _on_update_report(self, report) -> None:
        self._refresh_counts()
        text = (
            f"更新完成：成功 {report.updated} 只，跳过 {report.skipped} 只，"
            f"失败 {len(report.failed)} 只，耗时 {report.elapsed_seconds:.1f}s"
        )
        self.status_label.setText(text)
        warnings = "\n".join(getattr(report, "warnings", [])[:3])
        if report.failed or warnings:
            detail = "\n".join(report.failed[:8])
            body = "\n\n".join(part for part in (warnings, detail) if part)
            QMessageBox.warning(self, "更新完成但有异常", f"{text}\n\n{body}")
        else:
            QMessageBox.information(self, "更新完成", text)

    # ---------------------------------------------------------------- settings/export
    def open_settings(self) -> None:
        old_dir = self.config.data_dir
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config = dialog.config
        save_config(self.config)
        self.storage = StockStorage(self.config)
        self.screener = Screener(self.storage)
        if self.config.data_dir != old_dir:
            self._clear_table()
            self.spec_label.setText("解析结果：等待筛选")
        self._refresh_counts()
        self.status_label.setText("设置已保存")

    def export_results(self) -> None:
        if not self._results:
            self.status_label.setText("没有可导出的结果")
            return
        default_name = f"kline_matches_{date.today().strftime('%Y%m%d')}.xlsx"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出筛选结果",
            str(Path.home() / default_name),
            "Excel (*.xlsx);;CSV (*.csv)",
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() not in (".xlsx", ".csv"):
            out = out.with_suffix(".csv" if "csv" in selected_filter.lower() else ".xlsx")
        frame = pd.DataFrame(
            [
                {
                    "代码": r.code,
                    "名称": r.name,
                    "日期": r.date_str,
                    "开盘": r.open,
                    "最高": r.high,
                    "最低": r.low,
                    "收盘": r.close,
                    "涨跌幅%": r.pct_chg,
                    "成交量": r.vol,
                    "成交额": r.amount,
                    "bar_index": r.bar_index,
                    "条件": humanize_spec(self._spec) if self._spec else "",
                }
                for r in self._results
            ]
        )
        try:
            if out.suffix.lower() == ".xlsx":
                frame.to_excel(out, index=False)
            else:
                frame.to_csv(out, index=False, encoding="utf-8-sig")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"{exc}\n\n导出 Excel 需要安装 openpyxl。")
            return
        self.status_label.setText(f"已导出 {len(frame)} 条到 {out}")

    # ---------------------------------------------------------------- stop/close
    def stop_current(self) -> None:
        if self._worker is not None and hasattr(self._worker, "cancel"):
            self._worker.cancel()
            self.stop_btn.setEnabled(False)
            self.status_label.setText("正在停止…")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            answer = QMessageBox.question(
                self,
                "任务运行中",
                "当前任务仍在运行，是否停止并退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if hasattr(self._worker, "cancel"):
                self._worker.cancel()
            if not self._worker.wait(5000):
                QMessageBox.warning(
                    self, "仍在停止", "任务还没有完全停止，请稍后再关闭窗口。"
                )
                event.ignore()
                return
        event.accept()


def _right_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item
