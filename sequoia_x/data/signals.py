"""信号存档模块：记录每日各策略的选股结果。

用途：
1. 随时回顾历史信号；
2. 统计"多策略共振"（同一只票被几个策略同时选中）；
3. 为回测 / 参数寻优提供数据基础。
"""

import sqlite3
from datetime import datetime

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS signal_history (
    trade_date TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    strategy   TEXT NOT NULL,
    price      REAL,
    created_at TEXT,
    PRIMARY KEY (trade_date, symbol, strategy)
);
"""


def _ensure(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_SQL)
        conn.commit()


def save_signals(db_path: str, trade_date: str, strategy: str, analyses: list) -> int:
    """把某个策略当天的信号写入 signal_history，返回写入条数。"""
    if not analyses:
        return 0
    _ensure(db_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for a in analyses:
        tech = a.get("tech") or {}
        rows.append((
            trade_date,
            a.get("symbol"),
            strategy,
            tech.get("close"),
            now,
        ))
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO signal_history "
            "(trade_date, symbol, strategy, price, created_at) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
    return len(rows)


def count_resonance(db_path: str, trade_date: str) -> dict:
    """统计当天每只票被多少个不同策略选中（共振次数）。"""
    _ensure(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, COUNT(DISTINCT strategy) FROM signal_history "
            "WHERE trade_date = ? GROUP BY symbol",
            (trade_date,),
        ).fetchall()
    return {sym: n for sym, n in rows}
