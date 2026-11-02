#!/bin/sh
set -e
cat > /data/sentinel.conf <<EOF
port 26379
sentinel monitor ${REDIS_MASTER_NAME} ${REDIS_MASTER_IP} 6379 2
sentinel down-after-milliseconds ${REDIS_MASTER_NAME} 3000
sentinel failover-timeout ${REDIS_MASTER_NAME} 10000
sentinel parallel-syncs ${REDIS_MASTER_NAME} 1
EOF
exec redis-sentinel /data/sentinel.conf
