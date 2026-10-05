#!/usr/bin/env python3
"""Sequoia-X(增强版) -> daily_stock_analysis (DSA) 桥接脚本.

适配 D:\\sequoia-x-20261005 的「个人增强版」, 与官方版的差异:
  * 8 个策略(新增 PlatformBreakout 平台突破)
  * 配置项外置(Settings 新增策略参数, 均有默认值)
  * 新增 signal_history 表, 支持多策略共振统计

职责:
  1. 判断今天是否 A 股交易日(非交易日直接跳过, 不消耗 DSA 费用)
  2. 保证本地 SQLite 有历史数据(缓存未命中时自动 backfill)
  3. 增量同步当日行情
  4. 跑全部策略, 存档信号, 按「共振优先」排序并截断
  5. 写入 PICKS_FILE 与 GITHUB_OUTPUT, 交给 DSA 做深度分析
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

MIN_SYMBOLS_FOR_BACKFILL = 100
DEFAULT_MAX_PICKS = 10


def _load_sequoia(sequoia_dir: str):
    sys.path.insert(0, os.path.abspath(sequoia_dir))

    from sequoia_x.core.config import get_settings
    from sequoia_x.data.engine import DataEngine
    from sequoia_x.data.signals import save_signals
    from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
    from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
    from sequoia_x.strategy.ma_volume import MaVolumeStrategy
    from sequoia_x.strategy.platform_breakout import PlatformBreakoutStrategy
    from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
    from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
    from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
    from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy

    return get_settings, DataEngine, save_signals, [
        MaVolumeStrategy,
        TurtleTradeStrategy,
        HighTightFlagStrategy,
        LimitUpShakeoutStrategy,
        UptrendLimitDownStrategy,
        RpsBreakoutStrategy,
        PlatformBreakoutStrategy,
        PrivatePlacementStrategy,
    ]


def _is_trading_day() -> bool | None:
    """返回 True/False/None(无法判断)。"""
    try:
        import baostock as bs
        from datetime import date

        lg = bs.login()
        if lg.error_code != "0":
            return None
        try:
            today = date.today().strftime("%Y-%m-%d")
            rs = bs.query_trade_dates(start_date=today, end_date=today)
            while rs.next():
                return rs.get_row_data()[1] == "1"
            return None
        finally:
            bs.logout()
    except Exception as exc:  # noqa: BLE001
        print(f"[bridge] 交易日判断失败, 继续执行: {exc}", flush=True)
        return None


def _rank(results: dict[str, list[str]], max_picks: int) -> tuple[list[str], dict[str, int]]:
    """共振优先: 被越多策略同时选中的股票越靠前。"""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for codes in results.values():
        for code in codes:
            if code not in first_seen:
                first_seen[code] = order
                order += 1
            counts[code] = counts.get(code, 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    return [code for code, _ in ordered[:max_picks]], counts


def _write_outputs(picks: list[str], summary: dict[str, list[str]], resonance: dict[str, int]) -> None:
    picks_file = os.environ.get("PICKS_FILE", "sequoia_picks.txt")
    with open(picks_file, "w", encoding="utf-8") as fh:
        fh.write(",".join(picks))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"count={len(picks)}\n")
            fh.write(f"picks={','.join(picks)}\n")

    lines = ["## Sequoia-X(增强版) 选股结果", "", f"共选出 **{len(picks)}** 只候选股。", ""]
    if picks:
        lines += ["| 排名 | 代码 | 共振 |", "| --- | --- | --- |"]
        lines += [f"| {i} | {code} | {resonance.get(code, 1)} |" for i, code in enumerate(picks, 1)]
        lines += ["", "各策略命中数：", ""]
        lines += [f"- {name}: {len(codes)}" for name, codes in summary.items()]
    else:
        lines.append("今日没有股票触发任何策略。")

    report = "\n".join(lines) + "\n"
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X(enhanced) -> DSA bridge")
    parser.add_argument(
        "--max-picks",
        type=int,
        default=int(os.environ.get("SEQUOIA_MAX_PICKS", DEFAULT_MAX_PICKS)),
    )
    parser.add_argument("--skip-sync", action="store_true", help="跳过当日增量同步(调试用)")
    parser.add_argument("--force", action="store_true", help="跳过交易日判断")
    args = parser.parse_args()

    if not args.force:
        trading = _is_trading_day()
        if trading is False:
            print("[bridge] 今天不是 A 股交易日, 跳过本轮(不消耗 DSA 费用)", flush=True)
            _write_outputs([], {}, {})
            return
        if trading is None:
            print("[bridge] 无法确认是否交易日, 继续执行", flush=True)

    get_settings, data_engine_cls, save_signals, strategy_classes = _load_sequoia(
        os.environ.get("SEQUOIA_DIR", "_sequoia")
    )
    settings = get_settings()
    engine = data_engine_cls(settings)

    local_symbols = engine.get_local_symbols()
    if len(local_symbols) < MIN_SYMBOLS_FOR_BACKFILL:
        print(f"[bridge] 本地仅有 {len(local_symbols)} 只股票, 开始历史回填 ...", flush=True)
        engine.backfill(engine.get_all_symbols())
    elif not args.skip_sync:
        count = engine.sync_today_bulk()
        print(f"[bridge] 增量同步写入 {count} 条行情", flush=True)

    from datetime import date

    trade_date = date.today().strftime("%Y-%m-%d")
    results: dict[str, list[str]] = {}
    for strategy_cls in strategy_classes:
        name = strategy_cls.__name__
        try:
            picked = strategy_cls(engine=engine, settings=settings).run()
        except Exception as exc:  # 单个策略失败不应中断整体
            print(f"[bridge] {name} 执行失败: {exc}", flush=True)
            traceback.print_exc()
            picked = []
        results[name] = picked
        print(f"[bridge] {name}: {len(picked)} 只", flush=True)
        try:
            save_signals(
                engine.db_path,
                trade_date,
                name,
                [{"symbol": code} for code in picked],
            )
        except Exception as exc:  # 存档失败不影响主流程
            print(f"[bridge] {name} 信号存档失败: {exc}", flush=True)

    picks, resonance = _rank(results, max(1, args.max_picks))
    _write_outputs(picks, results, resonance)
    print(json.dumps({"picks": picks, "count": len(picks), "resonance": {c: resonance[c] for c in picks}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
