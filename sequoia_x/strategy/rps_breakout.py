"""RPS 极强动量突破策略。

分块读取 K 线（每次 500 只股票），避免把 300 多万行一次性读进内存。
老写法峰值内存约 900MB，分块后降到几十 MB。
"""

import sqlite3

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略。"""

    webhook_key: str = "rps"
    rps_period: int = 120
    rps_threshold: int = 90
    chunk_size: int = 500
    breakout_ratio: float = 0.90

    def run(self) -> list[str]:
        self.rps_period = int(getattr(self.settings, "rps_period", self.rps_period))
        self.rps_threshold = int(getattr(self.settings, "rps_threshold", self.rps_threshold))
        symbols = sorted(self.engine.get_local_symbols())
        if not symbols:
            return []

        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
            latest_date = row[0] if row else None
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if not latest_date:
            return []

        records: list[tuple] = []
        for i in range(0, len(symbols), self.chunk_size):
            chunk = symbols[i : i + self.chunk_size]
            records.extend(self._process_chunk(chunk, latest_date))

        if not records:
            logger.info("RpsBreakoutStrategy 选出 0 只股票")
            return []

        df = pd.DataFrame(records, columns=["symbol", "close", "roll_high", "pct_change"])
        df = df.dropna(subset=["pct_change"])
        if df.empty:
            logger.info("RpsBreakoutStrategy 选出 0 只股票")
            return []

        # 横向排名（此时数据量只有几千行，内存无压力）
        df["rps"] = df["pct_change"].rank(pct=True) * 100
        strong = df[df["rps"] >= self.rps_threshold]
        selected = strong[strong["close"] >= strong["roll_high"] * self.breakout_ratio]

        result = selected["symbol"].tolist()
        logger.info(f"RpsBreakoutStrategy 选出 {len(result)} 只股票")
        return result

    def _process_chunk(self, chunk: list[str], latest_date: str) -> list[tuple]:
        """对一小批股票计算：N 日涨幅 + N 日最高价。"""
        placeholders = ",".join(["?"] * len(chunk))
        sql = (
            "SELECT symbol, date, close, high FROM stock_daily "
            f"WHERE symbol IN ({placeholders}) ORDER BY symbol, date"
        )
        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql(sql, conn, params=tuple(chunk))
        except Exception as exc:
            logger.warning(f"RPS 分块读取失败（{len(chunk)} 只）：{exc}")
            return []

        if df.empty:
            return []

        min_bars = self.rps_period // 2
        out: list[tuple] = []
        for symbol, g in df.groupby("symbol", sort=False):
            if len(g) < min_bars:
                continue
            last = g.iloc[-1]
            if last["date"] != latest_date:
                continue  # 只统计最新交易日仍有数据的股票
            if len(g) > self.rps_period:
                base = g["close"].iloc[-1 - self.rps_period]
                if base is None or pd.isna(base) or float(base) == 0:
                    continue
                pct = (float(last["close"]) - float(base)) / float(base)
            else:
                continue
            roll_high = g["high"].tail(self.rps_period).max()
            if pd.isna(roll_high):
                continue
            out.append((symbol, float(last["close"]), float(roll_high), pct))
        return out
