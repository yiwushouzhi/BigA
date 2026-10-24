from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable

import pandas as pd

from .indicators import add_indicators
from .models import MatchResult
from .rules import ScreenSpec, evaluate_mask
from .storage import StockStorage, StorageError

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class ScanError(RuntimeError):
    pass


@dataclass(slots=True)
class ScanReport:
    results: list[MatchResult] = field(default_factory=list)
    scanned: int = 0
    matched_stocks: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    spec: ScreenSpec | None = None
    start_date: str | None = None
    end_date: str | None = None


def _as_timestamp(value: date | datetime | str | None, *, end_of_day: bool = False) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize() + (pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1) if end_of_day else pd.Timedelta(0))


class Screener:
    def __init__(self, storage: StockStorage):
        self.storage = storage

    def scan(
        self,
        spec: ScreenSpec,
        *,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        codes: Iterable[str] | None = None,
        exclude_st: bool = False,
        min_bars: int = 0,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> ScanReport:
        started = time.perf_counter()
        start_ts = _as_timestamp(start_date)
        end_ts = _as_timestamp(end_date, end_of_day=True)
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ScanError("开始日期不能晚于结束日期")

        if not self.storage.list_stocks():
            raise ScanError("没有可扫描的股票，请先更新行情数据")

        code_list = [str(c).strip() for c in codes or [] if str(c).strip()]
        stocks = self.storage.list_stocks(
            codes=code_list or None,
            exclude_st=exclude_st,
            min_bars=min_bars,
        )
        report = ScanReport(
            spec=spec,
            start_date=start_ts.strftime("%Y-%m-%d") if start_ts is not None else None,
            end_date=end_ts.strftime("%Y-%m-%d") if end_ts is not None else None,
        )
        if not stocks:
            report.elapsed_seconds = time.perf_counter() - started
            return report
        total = len(stocks)
        matched_codes: set[str] = set()

        for index, stock in enumerate(stocks, start=1):
            if cancelled and cancelled():
                report.errors.append("扫描已取消")
                break
            if progress:
                progress(index - 1, total, stock.code)
            try:
                df = self.storage.load_daily(stock.code)
            except StorageError as exc:
                report.skipped += 1
                report.errors.append(f"{stock.code}: {exc}")
                continue
            if df.empty:
                report.skipped += 1
                continue

            enriched = add_indicators(df)
            mask = evaluate_mask(enriched, spec)
            if start_ts is not None:
                mask &= enriched["date"] >= start_ts
            if end_ts is not None:
                mask &= enriched["date"] <= end_ts

            matched = enriched.loc[mask]
            if not matched.empty:
                matched_codes.add(stock.code)
                for row in matched.itertuples(index=False):
                    report.results.append(
                        MatchResult(
                            code=stock.code,
                            name=stock.name or stock.code,
                            market=stock.market,
                            date=pd.Timestamp(row.date).to_pydatetime(),
                            bar_index=int(row.bar_index),
                            total_bars=len(enriched),
                            open=float(row.open),
                            high=float(row.high),
                            low=float(row.low),
                            close=float(row.close),
                            vol=float(row.vol),
                            amount=float(row.amount),
                            pct_chg=_optional_float(getattr(row, "pct_chg", None)),
                        )
                    )
            report.scanned += 1

        if progress:
            progress(total, total, "")
        report.results.sort(key=lambda r: (r.date, r.code), reverse=True)
        report.matched_stocks = len(matched_codes)
        report.elapsed_seconds = time.perf_counter() - started
        return report


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None
