#!/usr/bin/env bash
# =============================================================================
# entrypoint.sh — single-container startup sequence
#
# First boot  : initialises MySQL datadir, creates DB + user, runs schema SQL
# Every boot  : waits for MySQL to be healthy, then starts supervisord
# =============================================================================
set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
log()  { echo "[entrypoint] $*"; }
ok()   { echo "[entrypoint] ✓ $*"; }
warn() { echo "[entrypoint] ⚠ $*"; }

# ── Config (mirrors .env defaults, overridden by real .env at runtime) ─────────
MYSQL_DATABASE="${MYSQL_DATABASE:-crypto_trader}"
MYSQL_USER="${MYSQL_USER:-trader}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-trader_pass}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root_pass}"

MYSQL_SOCKET="/var/run/mysqld/mysqld.sock"
MYSQL_DATADIR="/data/mysql"

# =============================================================================
# 1. MySQL first-boot initialisation
# =============================================================================
if [ ! -d "${MYSQL_DATADIR}/mysql" ]; then
    log "First boot detected — initialising MySQL datadir at ${MYSQL_DATADIR}..."

    # Initialise the datadir (no password yet)
    gosu mysql mysql_install_db \
        --datadir="${MYSQL_DATADIR}" \
        --user=mysql \
        --skip-test-db \
        2>/data/logs/mysql-init.log

    ok "MySQL datadir initialised"

    # Start a temporary MySQL instance so we can set up the DB + user
    log "Starting temporary MySQL to apply schema..."
    gosu mysql mysqld \
        --defaults-file=/etc/mysql/conf.d/zz-crypto.cnf \
        --skip-networking \
        --pid-file=/tmp/mysql-init.pid \
        2>/data/logs/mysql-init-tmp.log &

    # Wait for the socket
    for i in $(seq 1 30); do
        if mysqladmin --socket="${MYSQL_SOCKET}" ping --silent 2>/dev/null; then
            ok "MySQL socket ready"
            break
        fi
        log "Waiting for MySQL socket... (${i}/30)"
        sleep 2
    done

    # ── Set root password + create app user + database ─────────────────────────
    log "Creating database '${MYSQL_DATABASE}' and user '${MYSQL_USER}'..."
    mysql --socket="${MYSQL_SOCKET}" -u root << SQL
-- Secure root
ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';

-- Create application database
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- Create application user
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%'
    IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';

FLUSH PRIVILEGES;
SQL
    ok "Database and user created"

    # ── Apply schema SQL files ─────────────────────────────────────────────────
    if [ -d /workspace/mysql/init ]; then
        for f in $(ls /workspace/mysql/init/*.sql 2>/dev/null | sort); do
            log "Applying schema: $(basename "$f")..."
            mysql --socket="${MYSQL_SOCKET}" \
                  -u root \
                  -p"${MYSQL_ROOT_PASSWORD}" \
                  "${MYSQL_DATABASE}" < "$f"
            ok "Applied $(basename "$f")"
        done
    else
        warn "No SQL init files found at /workspace/mysql/init/"
    fi

    # ── Shut down the temporary instance ──────────────────────────────────────
    log "Shutting down temporary MySQL..."
    mysqladmin --socket="${MYSQL_SOCKET}" \
               -u root \
               -p"${MYSQL_ROOT_PASSWORD}" \
               shutdown
    sleep 3
    ok "MySQL first-boot setup complete"

else
    ok "MySQL datadir already exists — skipping initialisation"
fi

# =============================================================================
# 2. Pre-flight checks
# =============================================================================
log "Ensuring /data/logs and /data/redis directories exist..."
mkdir -p /data/logs /data/redis
chown -R mysql:mysql /var/run/mysqld /data/mysql

# =============================================================================
# 3. Hand off to supervisord (manages all services from here)
# =============================================================================
log "Starting supervisord (manages MySQL, Redis, Celery, Flask, Flower)..."
exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/crypto.conf
