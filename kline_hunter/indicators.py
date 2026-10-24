from __future__ import annotations

import numpy as np
import pandas as pd

MA_PERIODS = (5, 10, 20, 60, 120)
VOL_MA_PERIODS = (5, 10, 20, 60)

DERIVED_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "bar_index",
    "pct_chg",
    "amplitude",
    "body",
    "body_abs",
    "upper_shadow",
    "lower_shadow",
    "is_bullish",
    "is_bearish",
    "prev_close",
    "gap_pct",
    "close_ma5",
    "close_ma10",
    "close_ma20",
    "close_ma60",
    "close_ma120",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "vol_ma5",
    "vol_ma10",
    "vol_ma20",
    "vol_ma60",
    "vol_ratio_20",
    "lower_shadow_body_ratio",
    "upper_shadow_body_ratio",
}

FIELD_ALIASES = {
    "volume": "vol",
    "turnover": "amount",
    "ma_5": "ma5",
    "ma_10": "ma10",
    "ma_20": "ma20",
    "ma_60": "ma60",
    "ma20_vol": "vol_ma20",
    "vol_ma20_": "vol_ma20",
    "volratio": "vol_ratio_20",
    "vol_ratio": "vol_ratio_20",
    "lower_shadow_ratio": "lower_shadow_body_ratio",
    "upper_shadow_ratio": "upper_shadow_body_ratio",
    "is_yang": "is_bullish",
    "is_yin": "is_bearish",
}

FIELD_HELP = """
原始字段:
  open/high/low/close      开高低收
  vol                      成交量（手）
  amount                   成交额
  bar_index                该K线在本地完整历史中的序号（0=最早）
衍生字段:
  pct_chg                  当日涨跌幅（%）
  amplitude                振幅（%）
  body                     实体 = close - open（阳线为正，阴线为负）
  body_abs                 实体绝对值 = abs(close - open)
  upper_shadow             上影线 = high - max(open, close)
  lower_shadow             下影线 = min(open, close) - low
  lower_shadow_body_ratio  下影线 / 实体绝对值（实体为0时为 NaN）
  upper_shadow_body_ratio  上影线 / 实体绝对值
  is_bullish               是否阳线（close > open）
  is_bearish               是否阴线（close < open）
  prev_close               昨收
  gap_pct                  跳空幅度（%）
  ma5/ma10/ma20/ma60/ma120 收盘价均线（含当日）
  vol_ma5/vol_ma10/vol_ma20/vol_ma60  成交量均线（含当日）
  vol_ratio_20             成交量 / vol_ma20
""".strip()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """给原始K线补充筛选所需的衍生字段。"""
    if df.empty:
        return df.copy()
    out = df.copy()
    out = out.sort_values("date").reset_index(drop=True)

    close = out["close"].astype(float)
    open_ = out["open"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    vol = out["vol"].astype(float)
    amount = out["amount"].astype(float)

    prev_close = close.shift(1)
    out["prev_close"] = prev_close
    out["pct_chg"] = (close / prev_close - 1.0) * 100.0
    out["amplitude"] = np.where(prev_close > 0, (high - low) / prev_close * 100.0, np.nan)
    out["gap_pct"] = np.where(prev_close > 0, (open_ / prev_close - 1.0) * 100.0, np.nan)

    out["body"] = close - open_
    out["body_abs"] = (close - open_).abs()
    out["upper_shadow"] = (high - np.maximum(open_, close)).clip(lower=0)
    out["lower_shadow"] = (np.minimum(open_, close) - low).clip(lower=0)
    out["is_bullish"] = close > open_
    out["is_bearish"] = close < open_

    for period in MA_PERIODS:
        ma = close.rolling(period, min_periods=period).mean()
        out[f"ma{period}"] = ma
        out[f"close_ma{period}"] = ma

    for period in VOL_MA_PERIODS:
        out[f"vol_ma{period}"] = vol.rolling(period, min_periods=period).mean()

    out["vol_ratio_20"] = np.where(
        out["vol_ma20"] > 0, vol / out["vol_ma20"], np.nan
    )
    out["lower_shadow_body_ratio"] = np.where(
        out["body_abs"] > 0, out["lower_shadow"] / out["body_abs"], np.nan
    )
    out["upper_shadow_body_ratio"] = np.where(
        out["body_abs"] > 0, out["upper_shadow"] / out["body_abs"], np.nan
    )
    return out
