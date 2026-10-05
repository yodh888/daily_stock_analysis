"""多通道通知：飞书为主通道，Server酱为可选副通道。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.notify.serverchan import ServerChanNotifier

logger = get_logger(__name__)


class MultiNotifier:
    def __init__(self, settings) -> None:
        self.feishu = FeishuNotifier(settings)
        self.serverchan = ServerChanNotifier(getattr(settings, "serverchan_key", ""))

    @staticmethod
    def _sc_text(symbols, strategy_name, analyses) -> str:
        lines = [f"{strategy_name} 选出 {len(symbols)} 只"]
        for a in (analyses or [])[:10]:
            nm = a.get("name") or a.get("symbol")
            ai = a.get("ai") or {}
            res = a.get("resonance") or 1
            tag = f" [共振{res}]" if res >= 2 else ""
            lines.append(f"{nm}({a.get('symbol')}){tag} 评分{ai.get('score', '-')}")
            if ai.get("comment"):
                lines.append(f"  {ai['comment']}")
        return "\n".join(lines)

    def send(self, symbols, strategy_name, webhook_key="default", analyses=None, note=None) -> None:
        self.feishu.send(
            symbols=symbols, strategy_name=strategy_name,
            webhook_key=webhook_key, analyses=analyses, note=note,
        )
        if self.serverchan.enabled:
            self.serverchan.send_text(
                f"Sequoia-X {strategy_name}",
                self._sc_text(symbols, strategy_name, analyses),
            )

    def send_text(self, text: str, webhook_key: str = "default") -> None:
        self.feishu.send_text(text, webhook_key)
        if self.serverchan.enabled:
            self.serverchan.send_text("Sequoia-X 预警", text)

    def send_digest(self, entries: list, total_strategies: int, note=None) -> None:
        self.feishu.send_digest(entries, total_strategies, note)
        if self.serverchan.enabled:
            symbols = [e.get("symbol") for e in entries]
            self.serverchan.send_text(
                "Sequoia-X 每日汇总", self._sc_text(symbols, "汇总", entries)
            )
