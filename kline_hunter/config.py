from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "KLineHunter"
ORG_NAME = "BigA"


def default_data_dir() -> Path:
    """数据目录：环境变量优先，其次可执行文件/项目目录下的 stock_data。"""
    env_dir = os.getenv("KLINE_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "stock_data"
    return Path(__file__).resolve().parent.parent / "stock_data"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


FALLBACK_HOSTS: tuple[tuple[str, int], ...] = (
    ("119.147.212.81", 7709),
    ("119.147.212.85", 7709),
    ("218.75.126.9", 7709),
    ("115.238.56.198", 7709),
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("202.108.253.130", 7709),
    ("202.108.253.131", 7709),
    ("60.191.117.167", 7709),
    ("114.80.63.12", 7709),
)


def _default_hosts() -> tuple[tuple[str, int], ...]:
    """优先使用 pytdx 内置的最新服务器列表，它比写死的 IP 更不容易失效。"""
    try:
        from pytdx.config.hosts import hq_hosts
    except Exception:
        return FALLBACK_HOSTS
    seen: set[tuple[str, int]] = set()
    hosts: list[tuple[str, int]] = []
    for item in hq_hosts:
        try:
            ip, port = str(item[1]).strip(), int(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if not ip or (ip, port) in seen:
            continue
        seen.add((ip, port))
        hosts.append((ip, port))
    return tuple(hosts) or FALLBACK_HOSTS


def _parse_hosts(raw: str | None) -> tuple[tuple[str, int], ...]:
    defaults = _default_hosts()
    if not raw:
        return defaults
    hosts: list[tuple[str, int]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host, sep, port = item.partition(":")
        if not sep:
            port = "7709"
        try:
            hosts.append((host.strip(), int(port)))
        except ValueError:
            continue
    return tuple(hosts) or defaults


@dataclass(slots=True)
class AppConfig:
    data_dir: Path = field(default_factory=default_data_dir)
    deepseek_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    deepseek_model: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    )
    tdx_hosts: tuple[tuple[str, int], ...] = field(
        default_factory=lambda: _parse_hosts(os.getenv("TDX_HOSTS"))
    )
    tdx_window_keyword: str = field(
        default_factory=lambda: os.getenv("TDX_WINDOW_KEYWORD", "通达信")
    )
    tdx_key_interval: float = field(
        default_factory=lambda: _env_float("TDX_KEY_INTERVAL", 0.01)
    )
    max_bars: int = field(default_factory=lambda: _env_int("TDX_MAX_BARS", 8000))
    page_size: int = 800
    batch_size: int = field(default_factory=lambda: _env_int("TDX_BATCH_SIZE", 50))
    batch_delay: float = field(default_factory=lambda: _env_float("TDX_BATCH_DELAY", 0.5))
    incremental_bars: int = field(default_factory=lambda: _env_int("TDX_INCREMENTAL_BARS", 120))
    min_listed_bars: int = 60

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser().resolve()

    def ensure_dirs(self) -> None:
        (self.data_dir / "daily").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "index").mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "stocks.db"

    @property
    def daily_dir(self) -> Path:
        return self.data_dir / "daily"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    def with_overrides(self, **kwargs: object) -> "AppConfig":
        values = {
            "data_dir": self.data_dir,
            "deepseek_api_key": self.deepseek_api_key,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "tdx_hosts": self.tdx_hosts,
            "tdx_window_keyword": self.tdx_window_keyword,
            "tdx_key_interval": self.tdx_key_interval,
            "max_bars": self.max_bars,
            "batch_size": self.batch_size,
            "batch_delay": self.batch_delay,
            "incremental_bars": self.incremental_bars,
            "min_listed_bars": self.min_listed_bars,
        }
        for key, value in kwargs.items():
            if key in values and value is not None:
                values[key] = value
        values["data_dir"] = Path(values["data_dir"]).expanduser().resolve()
        return AppConfig(**values)
