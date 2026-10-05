"""基本面数据模块：按需从 baostock 拉取财务指标，缓存到本地 SQLite。

设计原则：
- 只对策略选出的少量股票查询（不扫全市场），避免大量请求。
- 结果按 (symbol, stat_date) 缓存，35 天内不重复请求。
- 查询失败不影响主流程，返回 None。
"""

import sqlite3
from datetime import date, datetime

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stock_fundamental (
    symbol            TEXT NOT NULL,
    stat_date         TEXT NOT NULL,
    pub_date          TEXT,
    roe               REAL,
    np_margin         REAL,
    gp_margin         REAL,
    net_profit        REAL,
    eps_ttm           REAL,
    revenue           REAL,
    liqa_share        REAL,
    yoy_ni            REAL,
    yoy_equity        REAL,
    liability_to_asset REAL,
    current_ratio     REAL,
    industry          TEXT,
    updated_at        TEXT,
    UNIQUE (symbol, stat_date)
);
"""


def _to_baostock_code(symbol: str) -> str:
    """纯数字代码 -> baostock 格式：6/9 开头 -> sh，其余 -> sz。"""
    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _latest_period(today: date) -> tuple[int, int]:
    """根据当前日期估算最新已披露的报告期。

    A 股披露节奏：年报/一季报 4 月底前，半年报 8 月底前，三季报 10 月底前。
    """
    m = today.month
    if m <= 4:
        return (today.year - 1, 4)  # 年报
    if m <= 8:
        return (today.year, 1)      # 一季报
    if m <= 10:
        return (today.year, 2)      # 半年报
    return (today.year, 3)          # 三季报


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_SQL)
        conn.commit()


def _read_cache(db_path: str, symbol: str) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM stock_fundamental WHERE symbol = ? ORDER BY stat_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    updated = row["updated_at"]
    try:
        age_days = (datetime.now() - datetime.fromisoformat(updated)).days
    except (TypeError, ValueError):
        age_days = 999
    return dict(row) if age_days <= 35 else None


def _store(db_path: str, fund: dict) -> None:
    cols = [
        "symbol", "stat_date", "pub_date", "roe", "np_margin", "gp_margin",
        "net_profit", "eps_ttm", "revenue", "liqa_share", "yoy_ni",
        "yoy_equity", "liability_to_asset", "current_ratio", "industry", "updated_at",
    ]
    placeholders = ",".join(["?"] * len(cols))
    values = [fund.get(c) for c in cols]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO stock_fundamental ({','.join(cols)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()


def get_fundamentals(db_path: str, symbols: list[str]) -> dict[str, dict]:
    """批量拉取基本面数据，返回 {symbol: dict}。

    字段：roe / np_margin / gp_margin / net_profit / eps_ttm / revenue /
         liqa_share / yoy_ni / yoy_equity / liability_to_asset /
         current_ratio / industry / stat_date / pub_date
    """
    if not symbols:
        return {}

    _ensure_table(db_path)

    result: dict[str, dict] = {}
    to_fetch: list[str] = []
    for s in symbols:
        cached = _read_cache(db_path, s)
        if cached is not None:
            result[s] = cached
        else:
            to_fetch.append(s)

    if not to_fetch:
        return result

    import baostock as bs

    year, quarter = _latest_period(date.today())

    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"baostock 登录失败，跳过基本面查询：{lg.error_msg}")
        return result

    try:
        for symbol in to_fetch:
            code = _to_baostock_code(symbol)
            fund: dict = {
                "symbol": symbol, "stat_date": None, "pub_date": None,
                "roe": None, "np_margin": None, "gp_margin": None,
                "net_profit": None, "eps_ttm": None, "revenue": None,
                "liqa_share": None, "yoy_ni": None, "yoy_equity": None,
                "liability_to_asset": None, "current_ratio": None, "industry": None,
            }
            try:
                rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
                if rs.error_code == "0" and rs.next():
                    r = dict(zip(rs.fields, rs.get_row_data()))
                    fund["stat_date"] = r.get("statDate")
                    fund["pub_date"] = r.get("pubDate")
                    fund["roe"] = _to_float(r.get("roeAvg"))
                    fund["np_margin"] = _to_float(r.get("npMargin"))
                    fund["gp_margin"] = _to_float(r.get("gpMargin"))
                    fund["net_profit"] = _to_float(r.get("netProfit"))
                    fund["eps_ttm"] = _to_float(r.get("epsTTM"))
                    fund["revenue"] = _to_float(r.get("MBRevenue"))
                    fund["liqa_share"] = _to_float(r.get("liqaShare"))
                else:
                    continue

                rs = bs.query_growth_data(code=code, year=year, quarter=quarter)
                if rs.error_code == "0" and rs.next():
                    r = dict(zip(rs.fields, rs.get_row_data()))
                    fund["yoy_ni"] = _to_float(r.get("YOYNI"))
                    fund["yoy_equity"] = _to_float(r.get("YOYEquity"))

                rs = bs.query_balance_data(code=code, year=year, quarter=quarter)
                if rs.error_code == "0" and rs.next():
                    r = dict(zip(rs.fields, rs.get_row_data()))
                    fund["liability_to_asset"] = _to_float(r.get("liabilityToAsset"))
                    fund["current_ratio"] = _to_float(r.get("currentRatio"))

                rs = bs.query_stock_industry(code=code)
                if rs.error_code == "0" and rs.next():
                    r = dict(zip(rs.fields, rs.get_row_data()))
                    fund["industry"] = r.get("industry")
            except Exception as exc:
                logger.warning(f"[{symbol}] 基本面查询失败：{exc}")
                continue

            fund["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                _store(db_path, fund)
            except Exception as exc:
                logger.warning(f"[{symbol}] 基本面缓存写入失败：{exc}")

            result[symbol] = fund
    finally:
        bs.logout()

    return result


_CREATE_INFO_SQL = """
CREATE TABLE IF NOT EXISTS stock_info (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    updated_at TEXT
);
"""


def get_stock_names(db_path: str, symbols: list[str]) -> dict[str, str]:
    """带本地缓存的股票名称查询。

    优先读本地 stock_info 表，只有缺失的才登录 baostock 补查并缓存。
    这样每天最多只产生一次登录，大幅降低被 baostock 限流的风险。
    """
    if not symbols:
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_INFO_SQL)
        conn.commit()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, name FROM stock_info WHERE symbol IN (%s)"
            % ",".join("?" * len(symbols)),
            symbols,
        ).fetchall()

    result: dict[str, str] = {r["symbol"]: r["name"] for r in rows if r["name"]}
    missing = [s for s in symbols if s not in result]
    if not missing:
        return result

    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"baostock 登录失败，跳过股票名称查询：{lg.error_msg}")
        return result
    try:
        for code in missing:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            rs = bs.query_stock_basic(code=f"{prefix}.{code}")
            while rs.next():
                row = rs.get_row_data()
                result[code] = row[1]
                break
    finally:
        bs.logout()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pairs = [(s, n, now) for s, n in result.items() if n]
    if pairs:
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO stock_info (symbol, name, updated_at) VALUES (?,?,?)",
                pairs,
            )
            conn.commit()

    return result
