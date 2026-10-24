from __future__ import annotations

import pandas as pd
import pytest

from kline_hunter.engine import ScanError, Screener
from kline_hunter.rules import ScreenSpec
from kline_hunter.storage import StockStorage


def _spec() -> ScreenSpec:
    return ScreenSpec.from_dict(
        {
            "description": "放量长下影阳线",
            "conditions": [
                {"field": "is_bullish", "operator": "==", "value": True},
                {"field": "vol_ratio_20", "operator": ">", "value": 2.5},
                {"field": "lower_shadow_body_ratio", "operator": ">", "value": 2},
            ],
        }
    )


def test_scan_returns_bar_index_and_date(storage: StockStorage, pattern_frame: pd.DataFrame):
    storage.save_daily(
        "000001", pattern_frame, market=0, name="测试股", is_st=False
    )
    report = Screener(storage).scan(
        _spec(),
        start_date="2024-02-01",
        end_date="2024-02-29",
    )
    assert report.scanned == 1
    assert report.matched_stocks == 1
    assert len(report.results) == 1
    result = report.results[0]
    assert result.code == "000001"
    assert result.name == "测试股"
    assert result.date_str == "2024-02-02"
    assert result.bar_index == 24
    assert result.total_bars == 25


def test_scan_date_range_excludes(storage: StockStorage, pattern_frame: pd.DataFrame):
    storage.save_daily("000001", pattern_frame, name="测试股")
    report = Screener(storage).scan(
        _spec(), start_date="2023-01-01", end_date="2023-12-31"
    )
    assert report.results == []


def test_scan_custom_codes_and_st_filter(storage: StockStorage, pattern_frame: pd.DataFrame):
    storage.save_daily("000001", pattern_frame, name="测试股")
    storage.save_daily("600000", pattern_frame, name="ST测试", is_st=True)
    screener = Screener(storage)
    report = screener.scan(_spec(), codes=["600000"], exclude_st=True)
    assert report.results == []
    report = screener.scan(_spec(), codes=["600000"])
    assert len(report.results) == 1

    with pytest.raises(ScanError):
        screener.scan(_spec(), start_date="2024-02-01", end_date="2024-01-01")


def test_scan_without_data_raises(storage: StockStorage):
    with pytest.raises(ScanError):
        Screener(storage).scan(_spec())
