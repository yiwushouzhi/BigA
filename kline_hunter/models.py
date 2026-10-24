from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime


MARKET_SZ = 0
MARKET_SH = 1


def market_for_code(code: str) -> int:
    """根据 A 股代码判断市场：0=深圳，1=上海。"""
    code = str(code).strip()
    if code.startswith(("6", "9")):
        return MARKET_SH
    return MARKET_SZ


def market_name(market: int) -> str:
    return {MARKET_SH: "上海", MARKET_SZ: "深圳"}.get(market, "未知")


def is_st_name(name: str) -> bool:
    upper = (name or "").upper().replace(" ", "")
    return "ST" in upper


@dataclass(slots=True)
class StockInfo:
    code: str
    name: str
    market: int
    is_st: bool = False
    is_active: bool = True
    industry: str = ""
    listed_date: str | None = None
    first_date: str | None = None
    last_date: str | None = None
    bar_count: int = 0
    updated_at: str | None = None


@dataclass(slots=True)
class MatchResult:
    code: str
    name: str
    market: int
    date: datetime
    bar_index: int
    total_bars: int
    open: float
    high: float
    low: float
    close: float
    vol: float
    amount: float
    pct_chg: float | None = None

    @property
    def date_str(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["date"] = self.date_str
        data["pct_chg"] = self.pct_chg
        return data
