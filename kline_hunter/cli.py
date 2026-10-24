from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .ai import AIError, DeepSeekParser
from .collector import CollectorError, TdxCollector
from .config import AppConfig
from .demo import seed_demo_data
from .engine import ScanError, Screener
from .rules import RuleValidationError, ScreenSpec, humanize_spec
from .storage import StockStorage, StorageError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kline-hunter", description="K线智能筛选定位器")
    parser.add_argument("--data-dir", help="本地数据目录，默认 ./stock_data")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="生成演示数据，便于离线验证")
    demo.add_argument("--count", type=int, default=30)
    demo.add_argument("--bars", type=int, default=520)

    update = sub.add_parser("update", help="从通达信行情服务器更新日线")
    update.add_argument("--codes", help="只更新指定代码，逗号分隔")
    update.add_argument("--limit", type=int, default=0, help="只更新前 N 只，用于测试")
    update.add_argument("--full", action="store_true", help="全量重拉而不是增量更新")
    update.add_argument("--no-refresh-list", action="store_true", help="不刷新股票列表")

    screen = sub.add_parser("screen", help="按条件扫描本地K线")
    group = screen.add_mutually_exclusive_group(required=True)
    group.add_argument("--question", "-q", help="自然语言条件，调用 DeepSeek 解析")
    group.add_argument("--spec", help="直接指定条件 JSON 文件")
    screen.add_argument("--start", help="开始日期 YYYY-MM-DD")
    screen.add_argument("--end", help="结束日期 YYYY-MM-DD")
    screen.add_argument("--codes", help="只扫描指定代码，逗号分隔")
    screen.add_argument("--exclude-st", action="store_true")
    screen.add_argument("--min-bars", type=int, default=0)
    screen.add_argument("--all", action="store_true", help="不限时间范围")
    screen.add_argument("--out", help="结果导出为 CSV/Excel")

    stocks = sub.add_parser("stocks", help="查看本地股票数量")
    stocks.add_argument("--codes", help="指定代码，逗号分隔")

    sub.add_parser("gui", help="启动图形界面")
    return parser


def parse_codes(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _progress(done: int, total: int, message: str) -> None:
    total_text = str(total) if total else "?"
    text = f"\r{done}/{total_text} {message[:40]}"
    print(text.ljust(70), end="", flush=True)
    if total and done >= total:
        print()


def cmd_demo(config: AppConfig, args: argparse.Namespace) -> int:
    count = seed_demo_data(config, count=max(1, args.count), bars=max(120, args.bars))
    print(f"已生成 {count} 只演示股票，数据目录: {config.data_dir}")
    print("试用条件: 找放巨量、长下影的阳线")
    return 0


def cmd_update(config: AppConfig, args: argparse.Namespace) -> int:
    storage = StockStorage(config)
    collector = TdxCollector(config, storage)
    codes = parse_codes(args.codes)
    try:
        refresh_list = not args.no_refresh_list
        if args.limit:
            if refresh_list:
                collector.refresh_stock_list(progress=_progress)
                refresh_list = False
            pool = [s.code for s in storage.list_stocks(codes=codes)]
            codes = pool[: args.limit]
        report = collector.update_all(
            codes=codes,
            incremental=not args.full,
            refresh_list=refresh_list,
            progress=_progress,
        )
    except (CollectorError, StorageError) as exc:
        print(f"\n更新失败: {exc}", file=sys.stderr)
        return 1
    print(f"\n更新完成: {report.updated} 只，跳过 {report.skipped} 只，失败 {len(report.failed)} 只")
    for item in report.warnings:
        print(f"  ! {item}")
    for item in report.failed[:10]:
        print(f"  - {item}")
    if len(report.failed) > 10:
        print(f"  ... 其余 {len(report.failed) - 10} 条失败记录省略")
    return 0


def cmd_screen(config: AppConfig, args: argparse.Namespace) -> int:
    if args.spec:
        try:
            payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
            spec = ScreenSpec.from_dict(payload, source="manual")
        except (OSError, json.JSONDecodeError, RuleValidationError) as exc:
            print(f"条件文件错误: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            spec = DeepSeekParser(config).parse(args.question)
        except AIError as exc:
            print(f"AI 解析失败: {exc}", file=sys.stderr)
            return 1
        print(f"AI 条件: {humanize_spec(spec)}")

    if args.all:
        start, end = None, None
    else:
        end = args.end or date.today().isoformat()
        start = args.start or (date.fromisoformat(end) - timedelta(days=365)).isoformat()

    storage = StockStorage(config)
    screener = Screener(storage)
    try:
        report = screener.scan(
            spec,
            start_date=start,
            end_date=end,
            codes=parse_codes(args.codes),
            exclude_st=args.exclude_st,
            min_bars=args.min_bars,
            progress=_progress,
        )
    except (ScanError, StorageError) as exc:
        print(f"\n扫描失败: {exc}", file=sys.stderr)
        return 1

    print(
        f"\n扫描 {report.scanned} 只股票，命中 {report.matched_stocks} 只，"
        f"共 {len(report.results)} 条，耗时 {report.elapsed_seconds:.2f}s"
    )
    if report.errors:
        for err in report.errors[:5]:
            print(f"  ! {err}")
    if not report.results:
        return 0

    frame = pd.DataFrame(
        [
            {
                "代码": r.code,
                "名称": r.name,
                "日期": r.date_str,
                "收盘": r.close,
                "成交量": r.vol,
                "成交额": r.amount,
                "bar_index": r.bar_index,
            }
            for r in report.results
        ]
    )
    print(frame.head(50).to_string(index=False))
    if len(frame) > 50:
        print(f"... 仅显示前 50 条，共 {len(frame)} 条")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() in (".xlsx", ".xls"):
            frame.to_excel(out, index=False)
        else:
            frame.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"已导出: {out}")
    return 0


def cmd_stocks(config: AppConfig, args: argparse.Namespace) -> int:
    storage = StockStorage(config)
    counts = storage.counts()
    print(f"数据目录: {config.data_dir}")
    print(f"股票元信息 {counts['total']} 条，有效 {counts['active']} 条，已有K线 {counts['with_data']} 条")
    stocks = storage.list_stocks(codes=parse_codes(args.codes), include_inactive=True)
    print(f"当前列出 {len(stocks)} 条")
    for stock in stocks[:20]:
        print(f"  {stock.code} {stock.name}  bars={stock.bar_count} last={stock.last_date}")
    if len(stocks) > 20:
        print("  ...")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig()
    if args.data_dir:
        config = config.with_overrides(data_dir=args.data_dir)

    if args.command == "demo":
        return cmd_demo(config, args)
    if args.command == "update":
        return cmd_update(config, args)
    if args.command == "screen":
        return cmd_screen(config, args)
    if args.command == "stocks":
        return cmd_stocks(config, args)
    if args.command == "gui":
        try:
            from .gui.app import run
        except ImportError as exc:
            print(f"无法启动 GUI: {exc}", file=sys.stderr)
            return 1
        return run(config)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
