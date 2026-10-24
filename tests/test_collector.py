from __future__ import annotations

import pandas as pd
import pytest

from kline_hunter.collector import TdxCollector
from kline_hunter.config import AppConfig
from kline_hunter.models import StockInfo
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


def test_update_all_codes_without_refreshing_list(collector_config, fake_api):
    """首次使用时本地没有元信息，直接指定代码也应该能拉取。"""
    storage = StockStorage(collector_config)
    collector = TdxCollector(collector_config, storage, api_factory=lambda: fake_api)
    report = collector.update_all(codes=["000001"], refresh_list=False)
    assert report.updated == 1
    assert storage.has_daily("000001")
    assert storage.get_stock("000001").market == 0


def test_call_switches_host_when_api_errors(app_config, fake_api):
    """能连上但接口报错的服务器要自动换下一台，而不是一直撞同一台。"""
    config = AppConfig(
        data_dir=app_config.data_dir,
        tdx_hosts=(("bad", 7709), ("good", 7709)),
        max_bars=25,
        page_size=10,
    )
    bad = FakeApi(make_bars(25))

    def boom(market):
        raise RuntimeError("calling function error")

    bad.get_security_count = boom  # type: ignore[method-assign]
    apis = iter([bad, fake_api])
    collector = TdxCollector(config, StockStorage(config), api_factory=lambda: next(apis))
    assert collector.refresh_stock_list() == 5


def test_refresh_stock_list_tolerates_single_page_failure(app_config):
    """个别分页报错时跳过该页继续，不应让整个列表更新失败。"""
    config = AppConfig(data_dir=app_config.data_dir, tdx_hosts=(("fake", 7709),))
    api = PageFailApi()
    collector = TdxCollector(config, StockStorage(config), api_factory=lambda: api)
    count = collector.refresh_stock_list()
    assert count == 4000
    assert "000001" in {s.code for s in collector.storage.list_stocks()}


def test_update_all_falls_back_to_local_list(app_config, fake_api):
    """列表刷新失败但本地已有股票时，继续更新本地列表并给出警告。"""
    config = AppConfig(
        data_dir=app_config.data_dir,
        tdx_hosts=(("fake", 7709),),
        max_bars=25,
        page_size=10,
        batch_delay=0,
    )
    storage = StockStorage(config)
    storage.upsert_stocks([StockInfo(code="000001", name="平安银行", market=0)])

    def boom(market):
        raise RuntimeError("calling function error")

    fake_api.get_security_count = boom  # type: ignore[method-assign]
    collector = TdxCollector(config, storage, api_factory=lambda: fake_api)
    report = collector.update_all(refresh_list=True, incremental=False)
    assert report.updated == 1
    assert report.warnings
    assert storage.has_daily("000001")


class PageFailApi(FakeApi):
    """模拟 3000 只股票、第二页（start=1000）解析失败的服务器。"""

    def __init__(self):
        super().__init__(make_bars(25))

    def get_security_count(self, market: int) -> int:
        return 3000

    def get_security_list(self, market: int, start: int):
        if start == 1000:
            raise RuntimeError("calling function error")
        page = start // 1000
        prefixes = ("600", "601", "603") if market == 1 else ("000", "002", "300")
        prefix = prefixes[page]
        return [{"code": f"{prefix}{i:03d}", "name": f"股{i}"} for i in range(1000)]
