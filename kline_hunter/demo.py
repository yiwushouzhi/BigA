from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .config import AppConfig
from .models import StockInfo
from .storage import StockStorage


def make_demo_frame(
    bars: int = 520,
    seed: int = 42,
    *,
    injections: int = 2,
) -> pd.DataFrame:
    """生成可复现的演示日线，并人为注入放量长下影阳线。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=date.today(), periods=bars)
    returns = rng.normal(0.0005, 0.018, bars)
    close = 10.0 * np.cumprod(1.0 + returns)
    open_ = close * (1.0 + rng.normal(0, 0.006, bars))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.018, bars))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.018, bars))
    vol = rng.lognormal(mean=12.0, sigma=0.35, size=bars)
    amount = vol * close * 100.0

    df = pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "vol": vol,
            "amount": amount,
        }
    )

    if injects := max(0, min(injections, bars // 4)):
        candidates = rng.choice(np.arange(80, bars - 10), size=injects, replace=False)
        for i in sorted(int(x) for x in candidates):
            prev_close = float(df.loc[i - 1, "close"])
            prior_mean = float(df.loc[i - 20 : i - 1, "vol"].mean())
            body = prev_close * 0.018
            open_price = prev_close * 1.004
            close_price = open_price + body
            df.loc[i, "open"] = open_price
            df.loc[i, "close"] = close_price
            df.loc[i, "high"] = close_price + body * 0.5
            df.loc[i, "low"] = open_price - body * 3.0
            df.loc[i, "vol"] = prior_mean * 4.0
            df.loc[i, "amount"] = df.loc[i, "vol"] * close_price * 100.0
    return df


def seed_demo_data(config: AppConfig, *, count: int = 30, bars: int = 520, seed: int = 42) -> int:
    storage = StockStorage(config)
    stocks: list[StockInfo] = []
    for n in range(count):
        code = f"900{n + 1:03d}"
        name = f"演示{n + 1:02d}"
        stock = StockInfo(code=code, name=name, market=1, is_active=True)
        stocks.append(stock)
        frame = make_demo_frame(bars=bars, seed=seed + n)
        storage.save_daily(
            code,
            frame,
            market=1,
            name=name,
            is_st=False,
        )
    storage.upsert_stocks(stocks)

    for index, (code, market, name) in enumerate(
        (("999999", 1, "上证指数"), ("399001", 0, "深证成指"), ("399006", 0, "创业板指")), start=1
    ):
        storage.save_daily(
            code,
            make_demo_frame(bars=bars, seed=seed + 100 + index),
            market=market,
            kind="index",
        )
    return count
