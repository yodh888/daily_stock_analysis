"""平台突破策略：长期横盘后放量突破箱体上沿。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class PlatformBreakoutStrategy(BaseStrategy):
    """平台突破策略。

    选股条件（全部向量化）：
    1. 前 60 日（不含今日）振幅 < 25%（长期横盘整理）
    2. 今日收盘 > 前 60 日最高价（突破平台上沿）
    3. 今日成交额 > 1 亿（流动性）
    4. 今日放量：成交量 > 前 20 日均量 × 1.5
    """

    webhook_key: str = "platform"
    _MIN_BARS: int = 61

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                hist = df.iloc[-61:-1]          # 前 60 日，不含今日
                today = df.iloc[-1]

                high60 = float(hist["high"].max())
                low60 = float(hist["low"].min())
                if not high60 or not low60 or low60 == 0:
                    continue

                consolidation = (high60 / low60) < 1.25          # 横盘
                breakout = float(today["close"]) > high60         # 突破上沿
                liquid = float(today["turnover"]) > 100_000_000    # 流动性
                vol_ma20 = df["volume"].iloc[-21:-1].mean()
                volume_surge = bool(vol_ma20) and float(today["volume"]) > float(vol_ma20) * 1.5

                if consolidation and breakout and liquid and volume_surge:
                    selected.append(symbol)
            except Exception as exc:
                logger.warning(f"[{symbol}] PlatformBreakoutStrategy 计算失败：{exc}")
                continue

        logger.info(f"PlatformBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected
