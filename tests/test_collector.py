from __future__ import annotations

import pandas as pd
import pytest

from kline_hunter.collector import TdxCollector
from kline_hunter.config import AppConfig
from kline_hunter.storage import StockStorage


def make_bars(count: int = 25) -> list[dict]:
    dates = pd.bdate_range("2024-01-01", periods=count)
    bars = []
    for i, day in enumerate(dates):
        bars.append(
            {
                "year": day.year,
                "month": day.month,
                "day": day.day,
                "open": 10.0 + i,
                "high": 10.5 + i,
                "low": 9.5 + i,
                "close": 10.2 + i,
                "vol": 1000.0 + i,
                "amount": 1_000_000.0,
            }
        )
    return bars


class FakeApi:
    def __init__(self, bars: list[dict]):
        self.bars = bars
        self.connected = False
        self.calls: list[str] = []

    def connect(self, ip: str, port: int, time_out: float = 5.0) -> bool:
        self.connected = True
        self.calls.append("connect")
        return True

    def disconnect(self) -> None:
        self.connected = False

    def get_security_count(self, market: int) -> int:
        return len(self._lists()[market])

    def get_security_list(self, market: int, start: int):
        items = self._lists()[market]
        return items[start : start + 1000] if start < len(items) else []

    @staticmethod
    def _lists() -> dict[int, list[dict]]:
        return {
            0: [
                {"code": "000001", "name": "平安银行"},
                {"code": "200001", "name": "深B"},
                {"code": "300001", "name": "特锐德"},
            ],
            1: [
                {"code": "600000", "name": "浦发银行"},
                {"code": "900901", "name": "云赛B股"},
                {"code": "688001", "name": "华兴源创"},
                {"code": "600001", "name": "ST测试"},
            ],
        }

    def _page(self, start: int, count: int) -> list[dict]:
        end = max(0, len(self.bars) - start)
        begin = max(0, end - count)
        return self.bars[begin:end]

    def get_security_bars(self, category, market, code, start, count):
        return self._page(start, count)

    def get_index_bars(self, category, market, code, start, count):
        return self._page(start, count)


@pytest.fixture
def collector_config(app_config: AppConfig) -> AppConfig:
    return AppConfig(
        data_dir=app_config.data_dir,
        deepseek_api_key="",
        tdx_hosts=(("fake", 7709),),
        max_bars=25,
        page_size=10,
        incremental_bars=5,
        batch_size=2,
        batch_delay=0,
    )


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi(make_bars(25))


def test_refresh_stock_list_filters_a_shares(collector_config, fake_api):
    storage = StockStorage(collector_config)
    collector = TdxCollector(collector_config, storage, api_factory=lambda: fake_api)
    count = collector.refresh_stock_list()
    assert count == 5
    codes = {s.code for s in storage.list_stocks(include_inactive=False)}
    assert codes == {"000001", "300001", "600000", "688001", "600001"}
    assert storage.get_stock("600001").is_st is True


def test_fetch_daily_pages(collector_config, fake_api):
    storage = StockStorage(collector_config)
    collector = TdxCollector(collector_config, storage, api_factory=lambda: fake_api)
    df = collector.fetch_daily("000001", 0)
    assert len(df) == 25
    assert df["date"].is_monotonic_increasing
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-01")
    assert df["date"].iloc[-1] == pd.Timestamp("2024-02-02")


def test_update_all_saves_stocks_and_index(collector_config, fake_api):
    storage = StockStorage(collector_config)
    collector = TdxCollector(collector_config, storage, api_factory=lambda: fake_api)
    report = collector.update_all(refresh_list=True)
    assert report.updated == 5
    assert report.failed == []
    assert storage.has_daily("600000")
    assert storage.has_daily("399001", kind="index")
    assert len(storage.load_daily("000001")) == 25


def test_update_all_custom_codes(collector_config, fake_api):
    storage = StockStorage(collector_config)
    collector = TdxCollector(collector_config, storage, api_factory=lambda: fake_api)
    collector.refresh_stock_list()
    report = collector.update_all(codes=["000001"], refresh_list=False)
    assert report.updated == 1
    assert report.stock_count == 1
    assert not storage.has_daily("600000")
