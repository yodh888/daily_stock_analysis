"""Sequoia-X V2 主程序入口。

两种运行模式：
  python main.py               # 日常模式：增量补数据 + 跑策略 + 基本面 + 飞书推送
  python main.py --backfill    # 回填模式：baostock 拉全市场历史K线
"""

import argparse
import socket
import sys

from dotenv import load_dotenv
load_dotenv()

socket.setdefaulttimeout(10.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.data.fundamental import get_fundamentals, get_stock_names
from sequoia_x.data.signals import save_signals, count_resonance
from sequoia_x.notify.multi import MultiNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.platform_breakout import PlatformBreakoutStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy


def _tech_summary(engine: DataEngine, symbol: str) -> dict:
    """从本地 K 线算出技术摘要。"""
    df = engine.get_ohlcv(symbol)
    if df is None or len(df) == 0:
        return {}
    last = df.iloc[-1]
    close = float(last["close"])
    prev_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else close
    pct_change = (close / prev_close - 1) * 100 if prev_close else None
    turnover = last.get("turnover")
    turnover_yi = float(turnover) / 1e8 if turnover else None
    pct_20d = None
    if len(df) >= 21:
        base = float(df.iloc[-21]["close"])
        if base:
            pct_20d = (close / base - 1) * 100
    # 支撑/压力位：直接从 K 线算
    support = float(df["low"].tail(20).min()) if len(df) >= 20 else None
    resistance = float(df["high"].tail(60).max()) if len(df) >= 60 else None

    # 涨跌停标记（后复权价算出的涨跌幅与真实一致）
    limit_flag = None
    if pct_change is not None:
        if pct_change >= 9.8:
            limit_flag = "涨停"
        elif pct_change <= -9.8:
            limit_flag = "跌停"

    return {
        "close": close,
        "pct_change": pct_change,
        "turnover_yi": turnover_yi,
        "pct_20d": pct_20d,
        "support": support,
        "resistance": resistance,
        "limit_flag": limit_flag,
    }


def _build_analyses(engine, settings, symbols):
    """为选出的股票补全：名称、技术摘要、基本面。"""
    names = get_stock_names(engine.db_path, symbols)

    limit = getattr(settings, "max_detail_per_strategy", 10)
    detail_symbols = symbols[:limit] if limit > 0 else symbols
    funds = get_fundamentals(engine.db_path, detail_symbols)

    techs = {}
    for s in symbols:
        try:
            techs[s] = _tech_summary(engine, s)
        except Exception:
            techs[s] = {}

    analyses = []
    for s in symbols:
        analyses.append({
            "symbol": s,
            "name": names.get(s, s),
            "tech": techs.get(s, {}),
            "fund": funds.get(s),
        })
    return analyses


def _baostock_status() -> tuple[bool, str, bool | None]:
    """体检 baostock：返回 (是否可用, 错误信息, 今天是否 A 股交易日)。

    交易日返回 None 表示无法判断（不影响主流程）。
    """
    import baostock as bs
    from datetime import date

    try:
        lg = bs.login()
    except Exception as exc:
        return False, str(exc), None
    if lg.error_code != "0":
        return False, lg.error_msg, None

    trading: bool | None = None
    try:
        today = date.today().strftime("%Y-%m-%d")
        rs = bs.query_trade_dates(start_date=today, end_date=today)
        while rs.next():
            row = rs.get_row_data()
            trading = row[1] == "1"
            break
    except Exception:
        trading = None
    finally:
        bs.logout()

    return True, "", trading


def _send_weekly_report(engine, notifier) -> None:
    """⑰ 每周五推送本周信号汇总。"""
    import sqlite3
    from datetime import date, timedelta

    log = get_logger(__name__)
    end = date.today()
    start = end - timedelta(days=6)
    try:
        with sqlite3.connect(engine.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, COUNT(DISTINCT strategy) AS n, COUNT(DISTINCT trade_date) AS d "
                "FROM signal_history WHERE trade_date BETWEEN ? AND ? "
                "GROUP BY symbol ORDER BY n DESC, d DESC LIMIT 15",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM signal_history "
                "WHERE trade_date BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()
    except Exception as exc:
        log.warning(f"周报统计失败：{exc}")
        return

    if not rows or not total or not total[0]:
        log.info("本周暂无信号，跳过周报")
        return

    lines = [
        f"Sequoia-X 本周信号周报（{start} ~ {end}）",
        f"信号总数：{total[0]} 条，涉及 {total[1]} 只股票",
        "",
        "本周出现最多的标的：",
    ]
    for sym, n, d in rows:
        lines.append(f"  {sym}  被 {n} 个策略选中 / 出现 {d} 天")
    notifier.send_text("\n".join(lines))
    log.info("周报已推送")


def _sync_coverage(engine, trade_date: str) -> tuple:
    """返回 (当天有数据的股票数, 本地股票总数)。"""
    import sqlite3
    try:
        with sqlite3.connect(engine.db_path) as conn:
            covered = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM stock_daily WHERE date = ?",
                (trade_date,),
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM stock_daily"
            ).fetchone()[0]
        return covered, total
    except Exception:
        return 0, 0


def _verify_sync(engine, covered: int, total: int, notifier) -> None:
    """④ 同步完整性校验：覆盖率明显偏低时告警。"""
    log = get_logger(__name__)
    if total and 0 < covered < total * 0.7:
        log.warning(f"同步覆盖率异常：{covered} / {total}")
        notifier.send_text(
            "Sequoia-X 预警：今日行情同步可能不完整\n"
            f"仅 {covered} / {total} 只股票有今日数据。\n"
            "可能是 baostock 限流，选股结果可能不完整。"
        )


def _check_resources(notifier) -> None:
    """㉑ 磁盘 / 内存不足时告警。"""
    import shutil
    log = get_logger(__name__)
    try:
        free_gb = shutil.disk_usage("/").free / 1024 ** 3
        if free_gb < 1.5:
            log.warning(f"磁盘空间不足：剩余 {free_gb:.1f} GB")
            notifier.send_text(
                f"Sequoia-X 预警：服务器磁盘空间不足\n剩余 {free_gb:.1f} GB，请及时清理。"
            )
    except Exception as exc:
        log.warning(f"磁盘检查失败：{exc}")

    try:
        avail_mb = 0.0
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail_mb = int(line.split()[1]) / 1024
                    break
        if 0 < avail_mb < 150:
            log.warning(f"可用内存不足：{avail_mb:.0f} MB")
            notifier.send_text(
                f"Sequoia-X 预警：服务器可用内存不足\n仅剩 {avail_mb:.0f} MB，可能影响策略运行。"
            )
    except Exception as exc:
        log.warning(f"内存检查失败：{exc}")


def _backup_db(db_path: str, keep: int = 3) -> None:
    """⑤ 每天备份数据库，只保留最近 N 份。"""
    import shutil
    from datetime import date
    from pathlib import Path

    log = get_logger(__name__)
    src = Path(db_path)
    if not src.exists():
        return
    bak_dir = src.parent / "backup"
    bak_dir.mkdir(parents=True, exist_ok=True)
    dst = bak_dir / f"{src.stem}-{date.today():%Y%m%d}.db"
    try:
        if not dst.exists():
            shutil.copy2(src, dst)
            log.info(f"数据库已备份：{dst}")
        for old in sorted(bak_dir.glob(f"{src.stem}-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        log.warning(f"数据库备份失败：{exc}")


def _mark_job(kind: str) -> None:
    """⑳ 记录任务开始/结束时间，供看门狗判断是否卡死。"""
    from datetime import datetime
    from pathlib import Path

    try:
        Path(f".job_{kind}").write_text(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8"
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument("--backfill", action="store_true", help="回填模式：拉全市场历史K线")
    args = parser.parse_args()

    _mark_job("started")

    try:
        settings = get_settings()
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")

        engine = DataEngine(settings)
        notifier = MultiNotifier(settings)

        if args.backfill:
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        # 数据源健康检查
        stale_note = None
        ok, err, trading = _baostock_status()
        if not ok:
            logger.error(f"baostock 不可用：{err}")
            if getattr(settings, "stale_data_ok", False):
                logger.warning("STALE_DATA_OK=true：改用本地已有数据继续选股")
                try:
                    import sqlite3 as _sq
                    with _sq.connect(engine.db_path) as _c:
                        _d = _c.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
                    stale_note = f"数据源异常，本次基于 {_d} 的数据"
                except Exception:
                    stale_note = "数据源异常，本次可能使用陈旧数据"
                notifier.send_text(
                    "Sequoia-X 预警：数据源 baostock 不可用\n"
                    f"错误信息：{err or '未知'}\n"
                    f"{stale_note}\n"
                    "解封后会自动恢复。"
                )
            else:
                notifier.send_text(
                    "Sequoia-X 预警：数据源 baostock 不可用\n"
                    f"错误信息：{err or '未知'}\n"
                    "可能是被限流/拉黑，本轮选股已跳过。\n"
                    "历史数据不受影响，解封后会自动恢复。"
                )
                return

        if trading is False:
            logger.info("今天不是 A 股交易日，跳过本轮选股（不推送）")
            return

        # ㉑ 资源体检
        _check_resources(notifier)

        from datetime import date as _date
        trade_date = _date.today().strftime("%Y-%m-%d")

        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 条")

        # ⑥ 覆盖率不足时自动重试一次
        covered, total = _sync_coverage(engine, trade_date)
        if total and covered < total * 0.7:
            logger.warning(f"今日数据覆盖率偏低（{covered}/{total}），重试一次同步...")
            count += engine.sync_today_bulk()
            covered, total = _sync_coverage(engine, trade_date)
            logger.info(f"重试后覆盖率：{covered}/{total}")

        # ④ 完整性校验 + ⑤ 数据库备份
        _verify_sync(engine, covered, total, notifier)
        _backup_db(settings.db_path)

        strategies: list[BaseStrategy] = [
            MaVolumeStrategy(engine=engine, settings=settings),
            TurtleTradeStrategy(engine=engine, settings=settings),
            HighTightFlagStrategy(engine=engine, settings=settings),
            LimitUpShakeoutStrategy(engine=engine, settings=settings),
            UptrendLimitDownStrategy(engine=engine, settings=settings),
            RpsBreakoutStrategy(engine=engine, settings=settings),
            PlatformBreakoutStrategy(engine=engine, settings=settings),
            PrivatePlacementStrategy(engine=engine, settings=settings),
        ]

        # 先跑完所有策略并收集结果（这样才能统计"多策略共振"）
        collected = []
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")

            if not selected:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")
                continue

            analyses = _build_analyses(engine, settings, selected)

            # ST 股处理：默认只标记，.env 里设 EXCLUDE_ST=true 才会剔除
            if getattr(settings, "exclude_st", False):
                analyses = [a for a in analyses if "ST" not in (a.get("name") or "").upper()]
                selected = [a["symbol"] for a in analyses]
                if not selected:
                    logger.info(f"{strategy_name} 剔除 ST 后无剩余，跳过推送")
                    continue

            collected.append((strategy_name, strategy.webhook_key, selected, analyses))

            # 信号存档（供回顾 / 回测 / 评分校准使用）
            try:
                saved = save_signals(engine.db_path, trade_date, strategy_name, analyses)
                logger.info(f"{strategy_name} 信号已存档 {saved} 条")
            except Exception as exc:
                logger.warning(f"{strategy_name} 信号存档失败：{exc}")

        # 统计多策略共振（同一只票被几个策略同时选中）
        try:
            resonance = count_resonance(engine.db_path, trade_date)
        except Exception as exc:
            logger.warning(f"共振统计失败：{exc}")
            resonance = {}

        # 统一推送（带上共振标记）
        for strategy_name, webhook_key, selected, analyses in collected:
            for a in analyses:
                a["resonance"] = resonance.get(a.get("symbol"), 1)

        if getattr(settings, "digest_mode", False):
            # ⑫ 合并成一张汇总卡
            entries = []
            for strategy_name, _wk, _sel, analyses in collected:
                for a in analyses:
                    item = dict(a)
                    item["strategy"] = strategy_name
                    entries.append(item)
            if entries:
                notifier.send_digest(
                    entries, total_strategies=len(collected), note=stale_note
                )
        else:
            for strategy_name, webhook_key, selected, analyses in collected:
                notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=webhook_key,
                    analyses=analyses,
                    note=stale_note,
                )

        # ⑰ 周末周报（默认周五，weekday()==4）
        if getattr(settings, "weekly_report", True) and _date.today().weekday() == 4:
            _send_weekly_report(engine, notifier)

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        _mark_job("finished")

    logger.info("Sequoia-X V2 运行完成")


if __name__ == "__main__":
    main()
