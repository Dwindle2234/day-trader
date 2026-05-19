# =============================================================================
# Crypto Trader — single-container image
# Runs: MySQL 8, Redis 7, Celery worker, Celery beat, Flower, Flask
# Process manager: supervisord
# =============================================================================
FROM python:3.12-slim


# ── 1. System packages ─────────────────────────────────────────────────────────
#   mysql-server   : embedded MySQL 8
#   redis-server   : embedded Redis 7
#   supervisor     : process manager (keeps all services alive)
#   gcc / pkg-config / libmysqlclient-dev : needed to compile mysqlclient wheel
#   gosu           : lets entrypoint drop privileges for mysql init
RUN apt-get update && apt-get install -y --no-install-recommends \
        mariadb-server \
        redis-server \
        supervisor \
        gcc \
        pkg-config \
        libmariadb-dev \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Python dependencies ─────────────────────────────────────────────────────
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 3. Application code ────────────────────────────────────────────────────────
COPY . .

# ── 4. Data directories ────────────────────────────────────────────────────────
#   /data/mysql  — MySQL datadir  (mount a volume here to persist data)
#   /data/redis  — Redis RDB dump (mount a volume here to persist data)
#   /data/logs   — All service logs
RUN mkdir -p /data/mysql /data/redis /data/logs /var/run/mysqld \
    && chown -R mysql:mysql /data/mysql /var/run/mysqld

# ── 5. MySQL config ────────────────────────────────────────────────────────────
RUN mkdir -p /etc/mysql/conf.d && cat > /etc/mysql/conf.d/zz-crypto.cnf << 'EOF'
[mysqld]
datadir                = /data/mysql
socket                 = /var/run/mysqld/mysqld.sock
pid-file               = /var/run/mysqld/mysqld.pid
bind-address           = 127.0.0.1
max_connections        = 100
innodb_buffer_pool_size = 128M
log_error              = /data/logs/mysql.log
general_log            = 0
slow_query_log         = 0
EOF

# ── 6. Redis config ────────────────────────────────────────────────────────────
RUN cat > /etc/redis/redis-crypto.conf << 'EOF'
bind 127.0.0.1
port 6379
dir /data/redis
dbfilename dump.rdb
save 900 1
save 300 10
loglevel notice
logfile /data/logs/redis.log
EOF

# ── 7. Supervisord + entrypoint ────────────────────────────────────────────────
COPY docker/supervisord.conf /etc/supervisor/conf.d/crypto.conf
COPY docker/entrypoint.sh    /entrypoint.sh
RUN  chmod +x /entrypoint.sh

# ── 8. Environment defaults ────────────────────────────────────────────────────
ENV PYTHONPATH=/workspace
ENV PYTHONUNBUFFERED=1
# All services talk to each other on localhost inside the container
ENV DATABASE_URL=mysql://trader:trader_pass@127.0.0.1:3306/crypto_trader
ENV CELERY_BROKER_URL=redis://127.0.0.1:6379/0
ENV CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
ENV REDIS_URL=redis://127.0.0.1:6379/0

EXPOSE 5000
# Flask dashboard (mapped to 5001 on host via docker-compose)
EXPOSE 5555
# Flower monitor (mapped to 5556 on host via docker-compose)
EXPOSE 3306
EXPOSE 6379

# Mount this volume to persist MySQL + Redis data across container restarts
VOLUME ["/data"]

ENTRYPOINT ["/entrypoint.sh"]
