from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kline_hunter.config import AppConfig
from kline_hunter.storage import StockStorage


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path / "stock_data",
        deepseek_api_key="",
        tdx_hosts=(("127.0.0.1", 1),),
    )


@pytest.fixture
def storage(app_config: AppConfig) -> StockStorage:
    return StockStorage(app_config)


@pytest.fixture
def pattern_frame() -> pd.DataFrame:
    """25 根K线：最后一根是放巨量长下影阳线。"""
    dates = pd.bdate_range("2024-01-01", periods=25)
    rows = []
    for i, day in enumerate(dates):
        if i == 24:
            rows.append(
                {
                    "date": day,
                    "open": 10.0,
                    "high": 11.6,
                    "low": 7.0,
                    "close": 11.0,
                    "vol": 300.0,
                    "amount": 3_000_000.0,
                }
            )
        else:
            rows.append(
                {
                    "date": day,
                    "open": 10.2,
                    "high": 10.3,
                    "low": 9.8,
                    "close": 10.0,
                    "vol": 100.0,
                    "amount": 1_000_000.0,
                }
            )
    return pd.DataFrame(rows)
