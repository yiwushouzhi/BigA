from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .config import AppConfig
from .models import StockInfo, is_st_name, market_for_code

KLINE_COLUMNS = ["date", "open", "high", "low", "close", "vol", "amount", "bar_index"]


class StorageError(RuntimeError):
    pass


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    """统一日期、数值类型，并重算 bar_index（0=最早一根）。"""
    required = {"date", "open", "high", "low", "close", "vol"}
    missing = required - set(df.columns)
    if missing:
        raise StorageError(f"K线数据缺少字段: {', '.join(sorted(missing))}")

    out = df.copy()
    if "amount" not in out.columns:
        out["amount"] = 0.0

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if getattr(out["date"].dt, "tz", None) is not None:
        out["date"] = out["date"].dt.tz_localize(None)
    out["date"] = out["date"].dt.normalize()

    for col in ("open", "high", "low", "close", "vol", "amount"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["date", "open", "high", "low", "close", "vol"])
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    out = out.reset_index(drop=True)
    out["bar_index"] = range(len(out))
    return out[KLINE_COLUMNS]


class StockStorage:
    """SQLite 存元信息，Parquet 存每只股票的 K 线。"""

    def __init__(self, config: AppConfig):
        self.config = config
        config.ensure_dirs()
        self.db_path = config.db_path
        self._init_db()

    # ------------------------------------------------------------------ SQLite
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    market INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'daily',
                    is_st INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    industry TEXT NOT NULL DEFAULT '',
                    listed_date TEXT,
                    first_date TEXT,
                    last_date TEXT,
                    bar_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_kind ON stocks(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(name)")

    def upsert_stocks(self, stocks: Iterable[StockInfo]) -> int:
        rows = [
            (
                s.code,
                s.name,
                s.market,
                "daily",
                int(s.is_st),
                int(s.is_active),
                s.industry or "",
                s.listed_date,
                s.first_date,
                s.last_date,
                s.bar_count,
                s.updated_at or datetime.now().isoformat(timespec="seconds"),
            )
            for s in stocks
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO stocks (
                    code, name, market, kind, is_st, is_active, industry,
                    listed_date, first_date, last_date, bar_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    market = excluded.market,
                    is_st = excluded.is_st,
                    is_active = excluded.is_active,
                    industry = CASE
                        WHEN excluded.industry <> '' THEN excluded.industry
                        ELSE stocks.industry
                    END,
                    listed_date = COALESCE(excluded.listed_date, stocks.listed_date),
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def touch_all_inactive(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE stocks SET is_active = 0 WHERE kind = 'daily'")

    def get_stock(self, code: str) -> StockInfo | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM stocks WHERE code = ?", (str(code),)).fetchone()
        return self._row_to_stock(row) if row else None

    def list_stocks(
        self,
        *,
        codes: Sequence[str] | None = None,
        exclude_st: bool = False,
        min_bars: int = 0,
        include_inactive: bool = False,
    ) -> list[StockInfo]:
        sql = "SELECT * FROM stocks WHERE kind = 'daily'"
        params: list[object] = []
        if not include_inactive:
            sql += " AND is_active = 1"
        if exclude_st:
            sql += " AND is_st = 0"
        if min_bars > 0:
            sql += " AND bar_count >= ?"
            params.append(int(min_bars))
        if codes:
            placeholders = ",".join("?" for _ in codes)
            sql += f" AND code IN ({placeholders})"
            params.extend(str(c) for c in codes)
        sql += " ORDER BY code"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_stock(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN bar_count > 0 THEN 1 ELSE 0 END) AS with_data
                FROM stocks WHERE kind = 'daily'
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "with_data": int(row["with_data"] or 0),
        }

    @staticmethod
    def _row_to_stock(row: sqlite3.Row) -> StockInfo:
        return StockInfo(
            code=row["code"],
            name=row["name"] or "",
            market=int(row["market"]),
            is_st=bool(row["is_st"]),
            is_active=bool(row["is_active"]),
            industry=row["industry"] or "",
            listed_date=row["listed_date"],
            first_date=row["first_date"],
            last_date=row["last_date"],
            bar_count=int(row["bar_count"] or 0),
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------ Parquet
    def _path_for(self, code: str, kind: str = "daily") -> Path:
        code = str(code).strip()
        if not code.isdigit() or len(code) != 6:
            raise StorageError(f"非法股票代码: {code!r}")
        folder = self.config.index_dir if kind == "index" else self.config.daily_dir
        return folder / f"{code}.parquet"

    def has_daily(self, code: str, kind: str = "daily") -> bool:
        return self._path_for(code, kind).exists()

    def load_daily(self, code: str, kind: str = "daily") -> pd.DataFrame:
        path = self._path_for(code, kind)
        if not path.exists():
            raise StorageError(f"{code} 本地没有K线数据")
        try:
            df = pd.read_parquet(path)
        except Exception as exc:  # pragma: no cover - 依赖异常时才触发
            raise StorageError(f"读取 {path.name} 失败: {exc}") from exc
        return normalize_kline(df)

    def save_daily(
        self,
        code: str,
        df: pd.DataFrame,
        *,
        market: int | None = None,
        name: str | None = None,
        is_st: bool | None = None,
        listed_date: str | None = None,
        kind: str = "daily",
    ) -> pd.DataFrame:
        normalized = normalize_kline(df)
        if normalized.empty:
            return normalized
        path = self._path_for(code, kind)
        tmp_name = ""
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{code}-", suffix=".parquet", dir=str(path.parent)
            )
            os.close(fd)
            normalized.to_parquet(tmp_name, index=False, compression="zstd")
            os.replace(tmp_name, path)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)

        if kind == "daily":
            self._update_stats(
                code=code,
                name=name,
                market=market,
                is_st=is_st,
                listed_date=listed_date,
                df=normalized,
            )
        return normalized

    def merge_daily(
        self,
        code: str,
        new_df: pd.DataFrame,
        *,
        market: int | None = None,
        name: str | None = None,
        is_st: bool | None = None,
        kind: str = "daily",
        full_refresh: bool = False,
    ) -> pd.DataFrame:
        incoming = normalize_kline(new_df)
        if incoming.empty:
            return self.load_daily(code, kind) if self.has_daily(code, kind) else incoming
        if full_refresh or not self.has_daily(code, kind):
            merged = incoming
        else:
            old = self.load_daily(code, kind)
            merged = pd.concat([old, incoming], ignore_index=True)
            merged = normalize_kline(merged)
        return self.save_daily(
            code,
            merged,
            market=market,
            name=name,
            is_st=is_st,
            kind=kind,
        )

    def latest_date(self, code: str, kind: str = "daily") -> pd.Timestamp | None:
        if not self.has_daily(code, kind):
            return None
        df = self.load_daily(code, kind)
        return None if df.empty else pd.Timestamp(df["date"].iloc[-1])

    def _update_stats(
        self,
        *,
        code: str,
        df: pd.DataFrame,
        name: str | None,
        market: int | None,
        is_st: bool | None,
        listed_date: str | None,
    ) -> None:
        first = pd.Timestamp(df["date"].iloc[0]).strftime("%Y-%m-%d")
        last = pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")
        resolved_name = name if name is not None else (self.get_stock(code).name if self.get_stock(code) else "")
        resolved_market = market if market is not None else market_for_code(code)
        resolved_st = is_st if is_st is not None else is_st_name(resolved_name)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stocks (
                    code, name, market, kind, is_st, is_active, industry,
                    listed_date, first_date, last_date, bar_count, updated_at
                ) VALUES (?, ?, ?, 'daily', ?, 1, '', ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE stocks.name END,
                    market = excluded.market,
                    is_st = excluded.is_st,
                    listed_date = COALESCE(excluded.listed_date, stocks.listed_date),
                    first_date = excluded.first_date,
                    last_date = excluded.last_date,
                    bar_count = excluded.bar_count,
                    updated_at = excluded.updated_at
                """,
                (
                    code,
                    resolved_name,
                    resolved_market,
                    int(resolved_st),
                    listed_date,
                    first,
                    last,
                    len(df),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )


