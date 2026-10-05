"""配置管理模块：通过 pydantic-settings 从环境变量或 .env 文件加载系统配置。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_path: str = "data/sequoia_v2.db"
    start_date: str = "2024-01-01"
    feishu_webhook_url: str  # 必填字段，缺失时抛出 ValidationError
    strategy_webhooks: dict[str, str] = {}

    max_detail_per_strategy: int = 10   # 每个策略最多补全几只股票的基本面与指标

    # ── 策略参数（可在 .env 里调，不用改代码）──
    ma_volume_ratio: float = 1.5            # 均线放量：量能倍数
    turtle_breakout_days: int = 20          # 海龟：突破 N 日新高
    turtle_min_turnover: float = 100000000  # 海龟：最小成交额（1亿）
    flag_momentum_ratio: float = 1.6        # 高窄旗形：前置涨幅
    flag_consolidation_ratio: float = 1.15  # 高窄旗形：振幅上限
    flag_volume_shrink: float = 0.6         # 高窄旗形：缩量比例
    shakeout_limit_pct: float = 1.095       # 涨停洗盘：涨停判定
    shakeout_volume_ratio: float = 2.0      # 涨停洗盘：放量倍数
    limitdown_pct: float = 0.905            # 跌停反包：跌停判定
    limitdown_volume_ratio: float = 2.0     # 跌停反包：放量倍数
    rps_period: int = 120                   # RPS：回看天数
    rps_threshold: int = 90                 # RPS：强度阈值

    # ── 过滤开关 ──
    exclude_st: bool = False                # True = 剔除 ST 股（默认只标记不剔除）
    digest_mode: bool = False               # True = 合并成一张汇总卡（默认每个策略一张）
    weekly_report: bool = True              # 每周五推送本周信号周报
    serverchan_key: str = ""                # Server酱 SendKey（填了才启用副通道）
    stale_data_ok: bool = False             # True = 数据源挂了也用旧数据继续选股（会标注数据日期）

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 放行未定义的变量
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):  # type: ignore[override]
        """扩展配置源，支持从环境变量中扫描 STRATEGY_WEBHOOK_ 前缀的键。"""
        import os

        sources = super().settings_customise_sources(settings_cls, **kwargs)

        prefix = "STRATEGY_WEBHOOK_"
        webhooks: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.upper().startswith(prefix):
                strategy_key = key[len(prefix):].lower()
                webhooks[strategy_key] = value

        if webhooks:
            os.environ.setdefault("_STRATEGY_WEBHOOKS_PARSED", "1")
            cls._parsed_strategy_webhooks = webhooks

        return sources

    def model_post_init(self, __context: object) -> None:
        """初始化后合并 STRATEGY_WEBHOOK_ 前缀的环境变量到 strategy_webhooks。"""
        import os

        prefix = "STRATEGY_WEBHOOK_"
        webhooks: dict[str, str] = dict(self.strategy_webhooks)
        for key, value in os.environ.items():
            if key.upper().startswith(prefix):
                strategy_key = key[len(prefix):].lower()
                webhooks[strategy_key] = value

        object.__setattr__(self, "strategy_webhooks", webhooks)

    def get_webhook_url(self, webhook_key: str) -> str:
        """根据 webhook_key 返回对应的 Webhook URL。"""
        return self.strategy_webhooks.get(webhook_key.lower(), self.feishu_webhook_url)


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回全局 Settings 单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
