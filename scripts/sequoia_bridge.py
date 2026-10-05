#!/usr/bin/env python3
"""Sequoia-X -> daily_stock_analysis (DSA) 桥接脚本.

在 GitHub Actions 中运行, 职责:
  1. 保证 Sequoia-X 本地 SQLite 有历史数据(缓存未命中时自动 backfill)
  2. 增量同步当日行情
  3. 依次运行 Sequoia-X 全部策略
  4. 去重 + 轮询采样 + 截断, 写入 PICKS_FILE 与 GITHUB_OUTPUT

它直接调用 Sequoia-X 的策略类, 不需要修改 Sequoia-X 本身的代码。
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
    from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
    from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
    from sequoia_x.strategy.ma_volume import MaVolumeStrategy
    from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
    from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
    from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
    from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy

    return get_settings, DataEngine, [
        MaVolumeStrategy,
        TurtleTradeStrategy,
        HighTightFlagStrategy,
        LimitUpShakeoutStrategy,
        UptrendLimitDownStrategy,
        RpsBreakoutStrategy,
        PrivatePlacementStrategy,
    ]


def _interleave(results: dict[str, list[str]], max_picks: int) -> list[str]:
    """按策略轮询取样, 避免某一个策略占满全部名额。"""
    queues = [list(codes) for codes in results.values() if codes]
    picks: list[str] = []
    seen: set[str] = set()

    while queues and len(picks) < max_picks:
        for queue in list(queues):
            if not queue:
                queues.remove(queue)
                continue
            code = queue.pop(0)
            if code in seen:
                continue
            seen.add(code)
            picks.append(code)
            if len(picks) >= max_picks:
                break

    return picks


def _write_outputs(picks: list[str], summary: dict[str, list[str]]) -> None:
    picks_file = os.environ.get("PICKS_FILE", "sequoia_picks.txt")
    with open(picks_file, "w", encoding="utf-8") as fh:
        fh.write(",".join(picks))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"count={len(picks)}\n")
            fh.write(f"picks={','.join(picks)}\n")

    lines = ["## Sequoia-X 选股结果", "", f"共选出 **{len(picks)}** 只候选股。", ""]
    if picks:
        lines += ["| 排名 | 代码 |", "| --- | --- |"]
        lines += [f"| {i} | {code} |" for i, code in enumerate(picks, 1)]
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
    parser = argparse.ArgumentParser(description="Sequoia-X -> DSA bridge")
    parser.add_argument(
        "--max-picks",
        type=int,
        default=int(os.environ.get("SEQUOIA_MAX_PICKS", DEFAULT_MAX_PICKS)),
        help="最多交给 DSA 分析的股票数量",
    )
    parser.add_argument("--skip-sync", action="store_true", help="跳过当日增量同步(调试用)")
    args = parser.parse_args()

    get_settings, data_engine_cls, strategy_classes = _load_sequoia(
        os.environ.get("SEQUOIA_DIR", "_sequoia")
    )
    settings = get_settings()
    engine = data_engine_cls(settings)

    local_symbols = engine.get_local_symbols()
    if len(local_symbols) < MIN_SYMBOLS_FOR_BACKFILL:
        print(f"[bridge] 本地仅有 {len(local_symbols)} 只股票，执行首次历史回填 ...", flush=True)
        engine.backfill(engine.get_all_symbols())
    elif not args.skip_sync:
        count = engine.sync_today_bulk()
        print(f"[bridge] 增量同步写入 {count} 条行情", flush=True)

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

    picks = _interleave(results, max(1, args.max_picks))
    _write_outputs(picks, results)
    print(json.dumps({"picks": picks, "count": len(picks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
