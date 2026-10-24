from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

import pandas as pd

from .config import AppConfig
from .models import StockInfo, is_st_name, market_for_code
from .storage import StockStorage, StorageError

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]

SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZ_PREFIXES = ("000", "001", "002", "003", "300", "301")

INDEXES: tuple[tuple[str, int, str], ...] = (
    ("999999", 1, "上证指数"),
    ("399001", 0, "深证成指"),
    ("399006", 0, "创业板指"),
)


class CollectorError(RuntimeError):
    pass


@dataclass(slots=True)
class UpdateReport:
    updated: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    stock_count: int = 0


def _describe_error(exc: BaseException | None) -> str:
    """pytdx 会把手底下的真实异常包成 calling function error，这里展开原始原因。"""
    if exc is None:
        return "未知错误"
    text = f"{type(exc).__name__}: {exc}"
    original = getattr(exc, "original_exception", None)
    if original is not None:
        text += f"（原始错误 {type(original).__name__}: {original}）"
    return text


def _is_a_share(code: str, market: int) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    if market == 1:
        return code.startswith(SH_PREFIXES)
    return code.startswith(SZ_PREFIXES)


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("gbk", errors="ignore")
    return str(value or "")


class TdxCollector:
    """pytdx 采集器：全市场列表、日线、指数和增量更新。"""

    MAX_CONNECT_ATTEMPTS = 10

    def __init__(self, config: AppConfig, storage: StockStorage, api_factory: Callable[[], Any] | None = None):
        self.config = config
        self.storage = storage
        self._api_factory = api_factory
        self._api: Any | None = None
        self._host: tuple[str, int] | None = None
        self._host_pos = 0

    # ------------------------------------------------------------- connection
    def _new_api(self) -> Any:
        if self._api_factory is not None:
            return self._api_factory()
        try:
            from pytdx.hq import TdxHq_API
        except ImportError as exc:
            raise CollectorError("缺少 pytdx 依赖，请执行 pip install pytdx") from exc
        return TdxHq_API(raise_exception=True, auto_retry=False)

    def connect(self) -> Any:
        if self._api is not None:
            return self._api
        hosts = list(self.config.tdx_hosts)
        if not hosts:
            raise CollectorError("没有配置任何行情服务器")
        last_error: Exception | None = None
        attempts = min(len(hosts), self.MAX_CONNECT_ATTEMPTS)
        for offset in range(attempts):
            pos = (self._host_pos + offset) % len(hosts)
            host, port = hosts[pos]
            api = self._new_api()
            try:
                ok = api.connect(host, port, time_out=5.0)
                if not ok:
                    raise CollectorError(f"连接 {host}:{port} 返回 False")
                self._api = api
                self._host = (host, port)
                self._host_pos = pos
                return api
            except Exception as exc:
                last_error = exc
                try:
                    api.disconnect()
                except Exception:
                    pass
        raise CollectorError(
            f"无法连接通达信行情服务器（已尝试 {attempts} 个）：{_describe_error(last_error)}"
        )

    def disconnect(self) -> None:
        if self._api is not None:
            try:
                self._api.disconnect()
            except Exception:
                pass
        self._api = None
        self._host = None

    def _advance_host(self) -> None:
        """当前服务器能连上但接口报错时，换下一台再试。"""
        if self.config.tdx_hosts:
            self._host_pos = (self._host_pos + 1) % len(self.config.tdx_hosts)

    def _reconnect(self) -> Any:
        self._advance_host()
        self.disconnect()
        return self.connect()

    def _call(self, method: str, *args: Any, attempts: int = 2) -> Any:
        """调用行情接口；失败时自动切换服务器重试。"""
        last_error: Exception | None = None
        used_host: tuple[str, int] | None = None
        for _ in range(max(1, attempts)):
            api = self.connect()
            used_host = self._host
            try:
                return getattr(api, method)(*args)
            except Exception as exc:
                last_error = exc
                self._advance_host()
                self.disconnect()
        host_text = f"{used_host[0]}:{used_host[1]}" if used_host else "未知服务器"
        raise CollectorError(
            f"行情接口 {method} 调用失败（{host_text}，已换服务器重试）：{_describe_error(last_error)}"
        )

    def __enter__(self) -> "TdxCollector":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()

    # ------------------------------------------------------------- stock list
    def refresh_stock_list(
        self,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> int:
        stocks: list[StockInfo] = []
        for market in (0, 1):
            count = self._call("get_security_count", market)
            if not count:
                raise CollectorError(f"无法获取市场 {market} 的股票数量（服务器返回 {count}）")
            chunk = 1000
            pages = min((int(count) + chunk - 1) // chunk, 100)
            market_stocks = 0
            consecutive_failures = 0
            for page in range(pages):
                if cancelled and cancelled():
                    raise CollectorError("更新已取消")
                try:
                    items = self._call("get_security_list", market, page * chunk) or []
                except CollectorError as exc:
                    # 个别分页失败（服务器缺数据/解析异常）不应让整个列表更新失败。
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        raise CollectorError(
                            f"市场 {market} 的股票列表连续 {consecutive_failures} 页读取失败，"
                            f"该服务器可能不支持此接口。请在设置里更换行情服务器，"
                            f"或用“指定代码”方式更新。{exc}"
                        ) from exc
                    continue
                consecutive_failures = 0
                if not items:
                    break
                market_stocks += len(items)
                for item in items:
                    code = _decode(item.get("code")).strip()
                    name = _decode(item.get("name")).strip()
                    if not _is_a_share(code, market):
                        continue
                    stocks.append(
                        StockInfo(
                            code=code,
                            name=name,
                            market=market,
                            is_st=is_st_name(name),
                            is_active=True,
                        )
                    )
                if progress:
                    progress(len(stocks), 0, f"读取股票列表 {market}/{page * chunk}")
            if market_stocks == 0:
                raise CollectorError(f"市场 {market} 的股票列表为空，行情服务器可能异常")

        if not stocks:
            raise CollectorError("股票列表为空，可能是行情服务器异常")
        # 两个市场都拉取成功后再标记旧数据，避免一次失败把本地股票全部置为失效。
        self.storage.touch_all_inactive()
        unique = {s.code: s for s in stocks}
        self.storage.upsert_stocks(unique.values())
        return len(unique)

    # ------------------------------------------------------------- K line
    def fetch_daily(
        self,
        code: str,
        market: int,
        *,
        max_bars: int | None = None,
        page_size: int | None = None,
    ) -> pd.DataFrame:
        limit = max_bars or self.config.max_bars
        size = page_size or self.config.page_size
        bars: list[dict[str, Any]] = []
        start = 0
        while len(bars) < limit:
            count = min(size, limit - len(bars))
            chunk = self._call("get_security_bars", 9, market, code, start, count) or []
            if not chunk:
                break
            bars.extend(chunk)
            if len(chunk) < count:
                break
            start += len(chunk)
        return self._bars_to_frame(bars)

    def fetch_index(self, code: str, market: int, *, max_bars: int | None = None) -> pd.DataFrame:
        limit = max_bars or self.config.max_bars
        size = self.config.page_size
        bars: list[dict[str, Any]] = []
        start = 0
        while len(bars) < limit:
            count = min(size, limit - len(bars))
            chunk = self._call("get_index_bars", 9, market, code, start, count) or []
            if not chunk:
                break
            bars.extend(chunk)
            if len(chunk) < count:
                break
            start += len(chunk)
        return self._bars_to_frame(bars)

    @staticmethod
    def _bars_to_frame(bars: Iterable[dict[str, Any]]) -> pd.DataFrame:
        rows = []
        for bar in bars:
            raw_date = bar.get("datetime")
            if raw_date:
                dt = pd.to_datetime(raw_date, errors="coerce")
            else:
                year, month, day = bar.get("year"), bar.get("month"), bar.get("day")
                if not year or not month or not day:
                    continue
                dt = pd.Timestamp(year=int(year), month=int(month), day=int(day))
            if pd.isna(dt):
                continue
            rows.append(
                {
                    "date": dt,
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "vol": bar.get("vol"),
                    "amount": bar.get("amount", 0),
                }
            )
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "vol", "amount"])
        return pd.DataFrame(rows).sort_values("date").drop_duplicates("date").reset_index(drop=True)

    def update_stock(self, stock: StockInfo, *, incremental: bool) -> int:
        if incremental and self.storage.has_daily(stock.code):
            count = min(self.config.incremental_bars, self.config.max_bars)
            bars = self._call("get_security_bars", 9, stock.market, stock.code, 0, count) or []
            new_df = self._bars_to_frame(bars)
            if new_df.empty:
                return 0
            merged = self.storage.merge_daily(
                stock.code,
                new_df,
                market=stock.market,
                name=stock.name,
                is_st=stock.is_st,
            )
            return len(merged)

        new_df = self.fetch_daily(stock.code, stock.market)
        if new_df.empty:
            return 0
        merged = self.storage.merge_daily(
            stock.code,
            new_df,
            market=stock.market,
            name=stock.name,
            is_st=stock.is_st,
            full_refresh=True,
        )
        return len(merged)

    def update_indexes(self) -> int:
        updated = 0
        for code, market, _name in INDEXES:
            df = self.fetch_index(code, market)
            if df.empty:
                continue
            self.storage.save_daily(code, df, market=market, kind="index")
            updated += 1
        return updated

    def update_all(
        self,
        *,
        codes: Iterable[str] | None = None,
        incremental: bool = True,
        refresh_list: bool = True,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> UpdateReport:
        started = time.perf_counter()
        report = UpdateReport()
        try:
            if refresh_list:
                try:
                    self.refresh_stock_list(progress=progress, cancelled=cancelled)
                except CollectorError as exc:
                    existing = self.storage.list_stocks(include_inactive=False)
                    if not existing:
                        raise
                    # 列表刷新失败但本地已有股票：继续按本地列表更新，别让用户彻底卡住。
                    message = (
                        f"股票列表刷新失败，已改用本地 {len(existing)} 只股票继续更新：{exc}"
                    )
                    report.warnings.append(message)
                    if progress:
                        progress(0, 0, message)
            code_list = [str(c).strip() for c in codes or [] if str(c).strip()]
            stocks = self.storage.list_stocks(codes=code_list or None, include_inactive=False)
            if code_list:
                # 本地还没有元信息的代码也允许直接拉取，否则“指定代码更新”在首次使用时不可用。
                known = {stock.code for stock in stocks}
                for code in code_list:
                    if code in known or len(code) != 6 or not code.isdigit():
                        continue
                    stocks.append(
                        StockInfo(
                            code=code,
                            name=code,
                            market=market_for_code(code),
                            is_st=False,
                            is_active=True,
                        )
                    )
            report.stock_count = len(stocks)
            if not stocks:
                raise CollectorError("没有可更新的股票")

            total = len(stocks)
            for index, stock in enumerate(stocks, start=1):
                if cancelled and cancelled():
                    report.skipped += total - index + 1
                    break
                if progress:
                    progress(index - 1, total, f"{stock.code} {stock.name}")
                try:
                    self.update_stock(stock, incremental=incremental)
                    report.updated += 1
                except Exception as exc:
                    report.failed.append(f"{stock.code} {stock.name}: {exc}")
                    try:
                        self._reconnect()
                    except CollectorError:
                        pass
                if index % self.config.batch_size == 0:
                    time.sleep(self.config.batch_delay)
            if progress:
                progress(total, total, "日线更新完成")
            self.update_indexes()
        finally:
            self.disconnect()
        report.elapsed_seconds = time.perf_counter() - started
        return report
