#!/bin/bash
# ⑳ 看门狗：检查今天的定时任务是否正常启动并完成
cd /root/Sequoia-X || exit 1
HOOK="https://open.feishu.cn/open-apis/bot/v2/hook/f91f550a-0162-4e26-b72d-f9587207a8f7"
TODAY=$(date +%Y-%m-%d)

send() {
  curl -s -X POST -H "Content-Type: application/json" \
    -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"$1\"}}" "$HOOK" >/dev/null 2>&1
}

S=$(cut -d' ' -f1 .job_started 2>/dev/null)
F=$(cut -d' ' -f1 .job_finished 2>/dev/null)

if [ "$S" != "$TODAY" ]; then
  send "Sequoia-X 预警：今天到现在(20:30)定时任务仍未启动\n请检查服务器状态与 crontab。"
elif [ "$F" != "$TODAY" ]; then
  send "Sequoia-X 预警：今天的任务启动了但一直没完成\n可能卡住了，请检查 log.txt。"
fi
