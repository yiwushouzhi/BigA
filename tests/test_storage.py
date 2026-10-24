from __future__ import annotations

import pandas as pd

from kline_hunter.models import StockInfo
from kline_hunter.storage import StockStorage


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "open": [c - 0.1 for c in closes],
            "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "vol": [1000.0] * len(dates),
            "amount": [1_000_000.0] * len(dates),
        }
    )


def test_save_load_and_bar_index(storage: StockStorage):
    df = _frame(["2024-01-03", "2024-01-01", "2024-01-02"], [3.0, 1.0, 2.0])
    saved = storage.save_daily("000001", df, market=0, name="平安银行")

    assert list(saved["bar_index"]) == [0, 1, 2]
    assert list(saved["date"].dt.strftime("%Y-%m-%d")) == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
    ]
    loaded = storage.load_daily("000001")
    assert loaded.equals(saved)

    meta = storage.get_stock("000001")
    assert meta is not None
    assert meta.name == "平安银行"
    assert meta.bar_count == 3
    assert meta.first_date == "2024-01-01"
    assert meta.last_date == "2024-01-03"


def test_merge_daily_recalculates_indices(storage: StockStorage):
    storage.save_daily(
        "000001", _frame(["2024-01-01", "2024-01-02"], [1.0, 2.0]), name="平安银行"
    )
    merged = storage.merge_daily(
        "000001",
        _frame(["2024-01-03", "2024-01-04"], [3.0, 4.0]),
        name="平安银行",
    )
    assert len(merged) == 4
    assert list(merged["bar_index"]) == [0, 1, 2, 3]
    assert storage.get_stock("000001").bar_count == 4


def test_list_stocks_filters(storage: StockStorage):
    storage.upsert_stocks(
        [
            StockInfo(code="000001", name="平安银行", market=0),
            StockInfo(code="600000", name="ST浦发", market=1, is_st=True),
        ]
    )
    storage.save_daily("000001", _frame(["2024-01-01"] * 1, [1.0]), name="平安银行")
    assert [s.code for s in storage.list_stocks()] == ["000001", "600000"]
    assert [s.code for s in storage.list_stocks(exclude_st=True)] == ["000001"]
    assert [s.code for s in storage.list_stocks(min_bars=1)] == ["000001"]
    assert [s.code for s in storage.list_stocks(codes=["600000"])] == ["600000"]


def test_rejects_invalid_code(storage: StockStorage):
    df = _frame(["2024-01-01"], [1.0])
    try:
        storage.save_daily("../evil", df)
    except Exception as exc:
        assert "非法股票代码" in str(exc)
    else:
        raise AssertionError("应当拒绝非法代码")
