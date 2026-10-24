from __future__ import annotations

import threading
from datetime import date

from PyQt6.QtCore import QThread, pyqtSignal

from ..ai import DeepSeekParser
from ..collector import TdxCollector
from ..config import AppConfig
from ..engine import Screener
from ..locator import TdxLocator
from ..storage import StockStorage


class ScanWorker(QThread):
    parsed = pyqtSignal(object)
    progress = pyqtSignal(int, int, str)
    report_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        question: str,
        *,
        start_date: date | None,
        end_date: date | None,
        codes: list[str] | None,
        exclude_st: bool,
        min_bars: int,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.question = question
        self.start_date = start_date
        self.end_date = end_date
        self.codes = codes
        self.exclude_st = exclude_st
        self.min_bars = min_bars
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(self) -> None:
        try:
            spec = DeepSeekParser(self.config).parse(self.question)
            if self.is_cancelled():
                return
            self.parsed.emit(spec)
            report = Screener(StockStorage(self.config)).scan(
                spec,
                start_date=self.start_date,
                end_date=self.end_date,
                codes=self.codes,
                exclude_st=self.exclude_st,
                min_bars=self.min_bars,
                progress=lambda done, total, msg: self.progress.emit(done, total, msg),
                cancelled=self.is_cancelled,
            )
            self.report_ready.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


class UpdateWorker(QThread):
    progress = pyqtSignal(int, int, str)
    report_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        *,
        codes: list[str] | None,
        refresh_list: bool,
        incremental: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.codes = codes
        self.refresh_list = refresh_list
        self.incremental = incremental
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(self) -> None:
        try:
            report = TdxCollector(self.config, StockStorage(self.config)).update_all(
                codes=self.codes,
                incremental=self.incremental,
                refresh_list=self.refresh_list,
                progress=lambda done, total, msg: self.progress.emit(done, total, msg),
                cancelled=self.is_cancelled,
            )
            self.report_ready.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


class LocateWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        code: str,
        bar_index: int,
        total_bars: int,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.code = code
        self.bar_index = bar_index
        self.total_bars = total_bars
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            plan = TdxLocator(self.config).locate(
                self.code,
                self.bar_index,
                self.total_bars,
                progress=lambda done, total, msg: self.progress.emit(done, total, msg),
                cancelled=lambda: self._cancelled.is_set(),
            )
            self.done.emit(
                f"已定位 {self.code}：{plan.anchor_key.upper()} 起向"
                f"{'右' if plan.direction == 'right' else '左'}移动 {plan.steps} 根"
            )
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)
