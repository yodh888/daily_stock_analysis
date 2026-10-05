# Sequoia-X V2（个人增强版）

> A 股量化选股系统 | 基于 [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) 二次开发

---

## 一、这是什么

每个交易日收盘后，自动扫描**全市场 5200+ 只 A 股**，用 **8 个策略**筛选，把命中的股票连同**基本面数据**推送到飞书。

**只做筛选和提醒，不做回测、不做自动交易、不做仓位管理。**

### 核心特性

| 特性 | 说明 |
|---|---|
| 8 个选股策略 | 海龟突破 / 均线放量 / 高窄旗形 / 涨停洗盘 / 趋势跌停 / RPS强度 / 平台突破 / 定增公告 |
| 基本面数据 | ROE、净利率、净利同比、资产负债率、所属行业（来自 baostock，非 AI 生成） |
| 支撑/压力位 | 直接由 K 线算出（20日最低 / 60日最高） |
| 多策略共振 | 同一只票被多个策略同时选中会特别标注 |
| 涨跌停 / ST 标记 | 自动识别并标注 |
| 交易日识别 | 节假日自动跳过，不做无用请求、不推陈旧数据 |
| 全程告警 | 数据源异常 / 同步不完整 / 任务卡死 / 磁盘内存不足，都会飞书通知 |
| 数据安全 | 每天自动备份数据库，增量同步不会误删历史数据 |
| 多通道推送 | 飞书 + Server酱（可选） |

---

## 二、环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux（Debian 12 实测）/ Windows |
| Python | >= 3.10 |
| 磁盘 | >= 3 GB |
| 内存 | >= 1 GB（建议再加 2GB Swap） |

---

## 三、部署步骤（Debian 12）

### 1. 装工具

    apt update
    apt install -y git python3 python3-venv python3-pip

### 2. 设置时区（很重要，否则定时任务时间会错）

    timedatectl set-timezone Asia/Shanghai
    date

### 3. 下载代码

    cd /root
    git clone https://github.com/sngyai/Sequoia-X.git
    cd Sequoia-X

### 4. 建虚拟环境

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .

### 5. 配置 .env

    cp .env.example .env
    nano .env

最小配置（三行）：

    DB_PATH=data/sequoia_v2.db
    START_DATE=2024-01-01
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的token

> 飞书机器人：飞书群 -> 设置 -> 群机器人 -> 添加自定义机器人。
> **不要开启「签名校验」**（本程序不支持）；建议用「自定义关键词」填 Sequoia。

### 6. 首次回填历史数据

    nohup .venv/bin/python main.py --backfill > backfill.log 2>&1 &
    tail -f backfill.log

- 约 5224 只股票；国内服务器约 12 分钟，美国服务器约 3.7 小时
- 中断可重跑，会自动跳过已下载的

### 7. 手动试跑一次

    .venv/bin/python main.py

### 8. 设置定时任务

    crontab -e

加入两行：

    15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
    30 20 * * 1-5 cd /root/Sequoia-X && ./check_job.sh >> watchdog.log 2>&1

第一行：每工作日 19:15 跑选股。第二行：20:30 看门狗检查。

---

## 四、配置项完整说明（.env）

### 基础

| 配置 | 默认 | 说明 |
|---|---|---|
| DB_PATH | data/sequoia_v2.db | 数据库路径 |
| START_DATE | 2024-01-01 | 回填起始日期 |
| FEISHU_WEBHOOK_URL | 必填 | 默认飞书机器人地址 |
| STRATEGY_WEBHOOK_<KEY> | 可选 | 给某个策略单独配机器人 |

### 策略参数（不用改代码就能调）

| 配置 | 默认 | 对应策略 |
|---|---|---|
| MA_VOLUME_RATIO | 1.5 | 均线放量：量能倍数 |
| TURTLE_BREAKOUT_DAYS | 20 | 海龟：突破 N 日新高 |
| TURTLE_MIN_TURNOVER | 100000000 | 海龟：最小成交额 |
| FLAG_MOMENTUM_RATIO | 1.6 | 旗形：前置涨幅 |
| FLAG_CONSOLIDATION_RATIO | 1.15 | 旗形：振幅上限 |
| FLAG_VOLUME_SHRINK | 0.6 | 旗形：缩量比例 |
| SHAKEOUT_LIMIT_PCT | 1.095 | 涨停洗盘：涨停判定 |
| SHAKEOUT_VOLUME_RATIO | 2.0 | 涨停洗盘：放量倍数 |
| LIMITDOWN_PCT | 0.905 | 跌停反包：跌停判定 |
| LIMITDOWN_VOLUME_RATIO | 2.0 | 跌停反包：放量倍数 |
| RPS_PERIOD | 120 | RPS：回看天数 |
| RPS_THRESHOLD | 90 | RPS：强度阈值（90 = 前 10%） |

### 行为开关

| 配置 | 默认 | 说明 |
|---|---|---|
| MAX_DETAIL_PER_STRATEGY | 10 | 每个策略最多补全几只股票的基本面 |
| EXCLUDE_ST | false | true = 剔除 ST 股（默认只标记不剔除） |
| DIGEST_MODE | false | true = 8个策略合并成 1 张汇总卡 |
| WEEKLY_REPORT | true | 每周五推送本周信号周报 |
| SERVERCHAN_KEY | 空 | 填了才启用 Server酱副通道 |
| STALE_DATA_OK | false | true = 数据源挂了也用旧数据继续跑（卡片会标注数据日期） |

---

## 五、8 个策略

| 策略 | 触发条件 |
|---|---|
| TurtleTrade | 突破前 20 日新高 + 成交额过亿 + 实体阳线且真涨 |
| MaVolume | 5 日均线上穿 20 日均线 + 放量 1.5 倍 |
| HighTightFlag | 40 日涨幅 > 60% + 近 10 日振幅 < 15% + 缩量 |
| LimitUpShakeout | 昨日涨停 + 今日收阴放量 + 不破昨收 |
| UptrendLimitDown | 昨日 MA20 > MA60 + 今日跌停放量 2 倍 |
| RpsBreakout | 120 日涨幅全市场排名前 10% + 贴近 120 日新高 |
| PlatformBreakout | 前 60 日振幅 < 25% + 今日突破平台上沿 + 放量 |
| PrivatePlacement | 最近 7 天有定向增发公告（走 AKShare） |

---

## 六、告警机制

程序会在以下情况主动发飞书通知：

| 场景 | 触发条件 |
|---|---|
| 数据源不可用 | baostock 登录失败（被限流/拉黑） |
| 同步不完整 | 当天有数据的股票 < 70% |
| 磁盘不足 | 剩余 < 1.5 GB |
| 内存不足 | 可用 < 150 MB |
| 任务没启动 | 20:30 看门狗发现今天没有启动记录 |
| 任务卡死 | 20:30 看门狗发现启动了但没写完结束标记 |

---

## 七、数据表结构

| 表 | 用途 |
|---|---|
| stock_daily | 日 K 线（后复权），约 340 万行 |
| stock_fundamental | 基本面数据缓存 |
| stock_info | 股票名称缓存（减少 baostock 登录） |
| signal_history | 每日信号存档 |

---

## 八、常见问题

**Q：被 baostock 拉黑（黑名单用户）怎么办？**
A：等 1~2 天通常自动解封。期间定时任务会主动告警。不要再手动跑回填（那是触发限流的主因）。

**Q：飞书推送失败，报 19001？**
A：Webhook 地址填错，检查是否还在用 .env.example 里的占位符。

**Q：日志里中文乱码？**
A：Windows 下执行 chcp 65001；Linux 一般无此问题。

**Q：想改策略参数？**
A：直接改 .env 里的对应配置，不用改代码。

**Q：数据库多大？**
A：约 440 MB（5224 只，2024 年至今）。每天增量约 1 MB。

---

## 九、相比官方版的改动

| 维度 | 官方版 | 本版 |
|---|---|---|
| 策略数 | 7 | 8（+平台突破） |
| 选股信息 | 代码 + 名称 | + 基本面 + 支撑压力 + 涨跌停 + ST + 共振 |
| 增量同步 | 先删整天再插入（有误删风险） | INSERT OR REPLACE（已修复） |
| 同步失败 | 静默少数据 | 覆盖率校验 + 自动重试 |
| 假期 | 照常运行（推陈旧数据） | 自动识别交易日并跳过 |
| 数据源故障 | 静默失败 | 主动告警 + 可选降级 |
| 任务监控 | 无 | 心跳 + 看门狗 |
| 数据库备份 | 无 | 每天自动备份（保留 3 份） |
| RPS 内存 | 约 900 MB | 约 217 MB（分块读取） |
| baostock 登录/天 | 约 22 次 | 约 8 次（名称本地缓存） |
| 策略参数 | 写死在代码 | 12 项可在 .env 调整 |
| 信号存档 | 无 | signal_history 表 |
| 推送通道 | 飞书 | 飞书 + Server酱 |
| 日志 | 无限增长 | logrotate 轮转 |

---

## 十、免责声明

本项目仅为**技术学习与个人研究**用途，**不是投资建议**。
选股结果由固定规则的技术指标计算得出，**未经历史回测验证**，不构成任何买卖建议。据此操作，盈亏自负。
数据来源为第三方免费接口（baostock / AKShare），可能出现延迟、缺失或错误。

---

## 十一、许可证

原项目为 MIT 许可证。本增强版供个人学习使用。
