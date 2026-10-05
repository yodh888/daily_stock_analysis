"""飞书通知模块：将选股结果（技术面 + 基本面）通过 Webhook 推送至飞书群。"""

import json
from datetime import date

import requests

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class FeishuNotifier:
    """飞书 Webhook 推送器。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _to_xueqiu_code(code: str) -> str:
        if code.startswith("6"):
            return f"SH{code}"
        if code.startswith(("4", "8")):
            return f"BJ{code}"
        return f"SZ{code}"

    @staticmethod
    def _get_stock_names(symbols: list[str]) -> dict[str, str]:
        import baostock as bs
        bs.login()
        mapping = {}
        try:
            for code in symbols:
                prefix = "sh" if code.startswith(("6", "9")) else "sz"
                rs = bs.query_stock_basic(code=f"{prefix}.{code}")
                while rs.next():
                    row = rs.get_row_data()
                    mapping[code] = row[1]
                    break
        finally:
            bs.logout()
        return mapping

    @staticmethod
    def _fmt_pct(v):
        return "—" if v is None else f"{v*100:.2f}%"

    def _build_content(self, symbols, strategy_name, analyses, note=None) -> str:
        today = date.today().strftime("%Y-%m-%d")
        by_symbol = {}
        for a in analyses or []:
            by_symbol[a.get("symbol")] = a

        lines = [
            f"**日期：** {today}",
            f"**策略：** {strategy_name}",
            f"**选股数量：** {len(symbols)}",
        ]
        if note:
            lines.append(f"**{note}**")
        lines.append("--------------------------")

        for i, code in enumerate(symbols, 1):
            a = by_symbol.get(code, {})
            name = a.get("name") or code
            xq = self._to_xueqiu_code(code)
            lines.append(f"**{i}. {name}** [{xq}](https://xueqiu.com/S/{xq})")
            res = a.get("resonance")
            if res and res >= 2:
                lines.append(f"多策略共振：{res} 个策略同时选中")
            if "ST" in (name or "").upper():
                lines.append("提示：ST 股（风险警示）")

            tech = a.get("tech") or {}
            t_parts = []
            if tech.get("close") is not None:
                t_parts.append(f"收盘 {tech['close']:.2f}")
            if tech.get("pct_change") is not None:
                t_parts.append(f"{tech['pct_change']:+.2f}%")
            if tech.get("turnover_yi") is not None:
                t_parts.append(f"成交额 {tech['turnover_yi']:.2f}亿")
            if tech.get("support") is not None:
                t_parts.append(f"支撑 {tech['support']:.2f}")
            if tech.get("resistance") is not None:
                t_parts.append(f"压力 {tech['resistance']:.2f}")
            if tech.get("limit_flag"):
                t_parts.append(str(tech["limit_flag"]))
            if t_parts:
                lines.append(" | ".join(t_parts))

            fund = a.get("fund") or {}
            f_parts = []
            if fund.get("roe") is not None:
                f_parts.append(f"ROE {self._fmt_pct(fund['roe'])}")
            if fund.get("np_margin") is not None:
                f_parts.append(f"净利率 {self._fmt_pct(fund['np_margin'])}")
            if fund.get("yoy_ni") is not None:
                f_parts.append(f"净利同比 {self._fmt_pct(fund['yoy_ni'])}")
            if fund.get("liability_to_asset") is not None:
                f_parts.append(f"负债率 {self._fmt_pct(fund['liability_to_asset'])}")
            if fund.get("industry"):
                f_parts.append(str(fund["industry"]))
            if f_parts:
                lines.append(" | ".join(f_parts))


            lines.append("")

        return "\n".join(lines)

    def _build_card(self, symbols, strategy_name, analyses, note=None) -> dict:
        content = self._build_content(symbols, strategy_name, analyses, note)
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"Sequoia-X 选股播报 | {strategy_name}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content},
                    },
                ],
            },
        }

    def _render_item(self, it: dict, idx: int) -> str:
        sym = it["symbol"]
        name = it["name"]
        xq = self._to_xueqiu_code(sym)
        parts = [f"**{idx}. {name}** [{xq}](https://xueqiu.com/S/{xq})"]
        res = it.get("resonance") or 1
        strat = "、".join(sorted(set(it.get("strategies") or [])))
        if res >= 2:
            parts.append(f"多策略共振 {res} 个：{strat}")
        else:
            parts.append(f"策略：{strat}")
        tech = it.get("tech") or {}
        t = []
        if tech.get("close") is not None:
            t.append(f"{tech['close']:.2f}")
        if tech.get("pct_change") is not None:
            t.append(f"{tech['pct_change']:+.2f}%")
        if tech.get("turnover_yi") is not None:
            t.append(f"额{tech['turnover_yi']:.2f}亿")
        if tech.get("support") is not None:
            t.append(f"支撑{tech['support']:.2f}")
        if tech.get("resistance") is not None:
            t.append(f"压力{tech['resistance']:.2f}")
        if tech.get("limit_flag"):
            t.append(str(tech["limit_flag"]))
        if t:
            parts.append(" | ".join(t))
        fund = it.get("fund") or {}
        f = []
        if fund.get("roe") is not None:
            f.append(f"ROE {self._fmt_pct(fund['roe'])}")
        if fund.get("yoy_ni") is not None:
            f.append(f"净利同比 {self._fmt_pct(fund['yoy_ni'])}")
        if fund.get("liability_to_asset") is not None:
            f.append(f"负债率 {self._fmt_pct(fund['liability_to_asset'])}")
        if fund.get("industry"):
            f.append(str(fund["industry"]))
        if f:
            parts.append(" | ".join(f))
        if "ST" in (name or "").upper():
            parts.append("提示：ST 股")
        return "\n".join(parts) + "\n"

    def build_digest_card(self, entries: list, total_strategies: int, note=None) -> dict:
        """把多个策略的信号合并成一张汇总卡。"""
        merged: dict = {}
        for e in entries:
            sym = e.get("symbol")
            if not sym:
                continue
            if sym not in merged:
                merged[sym] = {
                    "symbol": sym,
                    "name": e.get("name") or sym,
                    "tech": e.get("tech") or {},
                    "fund": e.get("fund") or {},
                    "strategies": [],
                }
            if e.get("strategy"):
                merged[sym]["strategies"].append(e["strategy"])
        items = list(merged.values())
        for it in items:
            it["resonance"] = len(set(it["strategies"]))
        items.sort(key=lambda x: -x["resonance"])

        today = date.today().strftime("%Y-%m-%d")
        lines = [
            f"**日期：** {today}",
            f"**有信号的策略：** {total_strategies} 个",
            f"**信号：** 共 {len(entries)} 条，去重后 {len(items)} 只股票",
        ]
        if note:
            lines.append(f"**{note}**")
        lines.append("--------------------------")
        for i, it in enumerate(items[:30], 1):
            lines.append(self._render_item(it, i))
        if len(items) > 30:
            lines.append(f"...还有 {len(items) - 30} 只未展示")
        content = "\n".join(lines)
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "Sequoia-X 每日选股汇总"},
                    "template": "turquoise",
                },
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
            },
        }

    def send_digest(self, entries: list, total_strategies: int, note=None) -> None:
        url = self.settings.get_webhook_url("default")
        payload = self.build_digest_card(entries, total_strategies, note)
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(f"汇总推送失败 HTTP={resp.status_code} {resp.text}")
            else:
                logger.info(f"汇总推送成功，共 {len(entries)} 条信号")
        except requests.RequestException as exc:
            logger.error(f"汇总推送异常：{exc}")

    def send_text(self, text: str, webhook_key: str = "default") -> None:
        """发送一条纯文本通知（用于系统告警）。"""
        url = self.settings.get_webhook_url(webhook_key)
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(f"飞书文本推送失败 [{webhook_key}] HTTP={resp.status_code} {resp.text}")
            else:
                logger.info(f"飞书文本推送成功 [{webhook_key}]")
        except requests.RequestException as exc:
            logger.error(f"飞书文本推送异常 [{webhook_key}]：{exc}")

    def send(self, symbols, strategy_name, webhook_key="default", analyses=None, note=None) -> None:
        url = self.settings.get_webhook_url(webhook_key)

        if analyses is None:
            names = self._get_stock_names(symbols)
            analyses = [{"symbol": s, "name": names.get(s, s)} for s in symbols]

        payload = self._build_card(symbols, strategy_name, analyses, note)

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"飞书推送失败 [{webhook_key}] HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info(f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只股票")
        except requests.RequestException as exc:
            logger.error(f"飞书推送请求异常 [{webhook_key}]：{exc}")
