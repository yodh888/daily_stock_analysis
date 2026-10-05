#!/bin/bash
DB=/root/Sequoia-X/data/sequoia_v2.db
LOG=/root/Sequoia-X/backfill.log
PY=/root/Sequoia-X/.venv/bin/python
HOOK="https://open.feishu.cn/open-apis/bot/v2/hook/f91f550a-0162-4e26-b72d-f9587207a8f7"
STEP=500
TOTAL=5224

send() {
  curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"$1\"}}" "$HOOK" >/dev/null 2>&1
}

count_stocks() {
  $PY -c "import sqlite3;c=sqlite3.connect('$DB');print(c.execute('select count(distinct symbol) from stock_daily').fetchone()[0])" 2>/dev/null
}

LAST=0
while true; do
  N=$(count_stocks)
  case "$N" in ''|*[!0-9]*) N=0 ;; esac

  M=$((N / STEP))
  if [ "$M" -gt "$LAST" ]; then
    LAST=$M
    PCT=$((N * 100 / TOTAL))
    send "Sequoia-X 回填进度\n已完成：${N} / ${TOTAL} 只（${PCT}%）"
  fi

  if ! pgrep -f "[m]ain.py --backfill" >/dev/null 2>&1; then
    ROWS=$($PY -c "import sqlite3;c=sqlite3.connect('$DB');print(c.execute('select count(*) from stock_daily').fetchone()[0])" 2>/dev/null)
    SUMMARY=$(grep "回填完成" "$LOG" | tail -1)
    [ -z "$SUMMARY" ] && SUMMARY="未找到完成日志（可能是异常中断，请检查）"
    send "Sequoia-X 历史数据回填结束\n股票数：${N} / ${TOTAL}\n数据行数：${ROWS}\n${SUMMARY}"
    exit 0
  fi

  sleep 60
done
