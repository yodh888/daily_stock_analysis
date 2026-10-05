"""Server酱推送通道（可选副通道）。

在 .env 里填 SERVERCHAN_KEY 即可启用，不填则完全跳过。
"""

import requests

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class ServerChanNotifier:
    """Server酱（sctapi.ftqq.com）推送器。"""

    def __init__(self, send_key: str = "") -> None:
        self.send_key = (send_key or "").strip()
        self.enabled = bool(self.send_key)

    def send_text(self, title: str, text: str) -> None:
        if not self.enabled:
            return
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{self.send_key}.send",
                data={"title": title[:80], "desp": text},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Server酱推送成功")
            else:
                logger.error(f"Server酱推送失败：HTTP {resp.status_code}")
        except requests.RequestException as exc:
            logger.error(f"Server酱推送异常：{exc}")
