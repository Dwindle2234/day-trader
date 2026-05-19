#!/usr/bin/env bash
# =============================================================================
# run.sh — Crypto Trader management script
# Usage:  ./run.sh <command>
# =============================================================================

CONTAINER="crypto_trader"
SERVICE="crypto-trader"
COMPOSE="docker compose"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗${RESET}  $*"; exit 1; }

check_env() {
    if [ -f ".env" ]; then return 0; fi
    warn ".env not found — creating it now..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        cat > .env << 'ENVEOF'
MYSQL_ROOT_PASSWORD=change_me_root
MYSQL_DATABASE=crypto_trader
MYSQL_USER=trader
MYSQL_PASSWORD=change_me_trader
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
COINBASE_API_KEY=organizations/YOUR_ORG_ID/apiKeys/YOUR_KEY_ID
COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\nYOUR_KEY_HERE\n-----END EC PRIVATE KEY-----\n"
COINGECKO_API_KEY=
CRYPTOPANIC_API_KEY=
AI_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
PAPER_TRADING_INITIAL_BALANCE=10000.00
MAX_POSITION_PCT=0.10
STOP_LOSS_PCT=0.03
MIN_AI_CONFIDENCE=0.70
MAX_OPEN_POSITIONS=5
WATCHLIST=BTC-USD,ETH-USD,SOL-USD,DOGE-USD,AVAX-USD
SECRET_KEY=change_me_flask_secret
FLASK_ENV=development
ENVEOF
    fi
    echo ""
    ok ".env created. Fill in your API keys then run:  ./run.sh up"
    exit 1
}

usage() {
    echo -e "${BOLD}Crypto Trader — run.sh${RESET}"
    echo ""
    echo -e "${BOLD}Lifecycle:${RESET}  up | down | restart | build | reset"
    echo -e "${BOLD}Logs:${RESET}       logs | logs-flask | logs-worker | logs-beat | logs-mysql | logs-redis | logs-flower"
    echo -e "${BOLD}Process:${RESET}    ps | restart-app"
    echo -e "${BOLD}Dev:${RESET}        shell | backfill | status | test-collector | test-analysis | test-signal [SYMBOL] | ai-provider"
}

need_docker()    { docker info &>/dev/null || err "Docker not running."; }
need_container() { docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$" || err "Container not running. Run: ./run.sh up"; }

cmd_up() {
    need_docker; check_env
    info "Building image and starting container..."
    $COMPOSE up -d --build
    echo ""
    ok "Container started."
    echo -e "   ${BOLD}Dashboard${RESET} : http://localhost:5002"
    echo -e "   ${BOLD}Flower${RESET}    : http://localhost:5556"
    echo ""
    info "Tip: run  ./run.sh logs  to watch startup progress."
}

cmd_down()        { need_docker; check_env; info "Stopping..."; $COMPOSE down; ok "Stopped."; }
cmd_restart()     { need_docker; info "Restarting..."; $COMPOSE restart "$CONTAINER"; ok "Done."; }
cmd_build()       { need_docker; check_env; info "Rebuilding..."; $COMPOSE build --no-cache; ok "Done."; }

cmd_reset() {
    need_docker; check_env
    warn "This will DELETE all data. Are you sure? [y/N]"
    read -r confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }
    $COMPOSE down -v && $COMPOSE up -d --build
    ok "Fresh start complete."
}

cmd_logs()        { need_docker; $COMPOSE logs -f "$SERVICE"; }
cmd_logs_flask()  { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/flask-stdout.log; }
cmd_logs_worker() { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/celery-worker.log; }
cmd_logs_beat()   { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/celery-beat.log; }
cmd_logs_mysql()  { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/mysql.log; }
cmd_logs_redis()  { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/redis.log; }
cmd_logs_flower() { need_docker; need_container; docker exec "$CONTAINER" tail -f /data/logs/flower-stdout.log; }

cmd_ps() {
    need_docker; need_container
    echo ""
    docker exec "$CONTAINER" supervisorctl status
    echo ""
}

cmd_restart_app() {
    need_docker; need_container
    info "Restarting Python services..."
    docker exec "$CONTAINER" supervisorctl restart "python-services:*"
    ok "Done."
}

cmd_shell()    { need_docker; need_container; docker exec -it "$CONTAINER" bash; }
cmd_status()   { need_docker; need_container; docker exec "$CONTAINER" celery -A celery_app inspect active; }

cmd_backfill() {
    need_docker; need_container
    info "Triggering 90-day historical data back-fill..."
    docker exec "$CONTAINER" celery -A celery_app call celery_app.backfill_history_task
    ok "Task queued — check  ./run.sh logs-worker  for progress."
}

cmd_test_collector() {
    need_docker; need_container
    info "Running Coinbase collector smoke-test..."
    docker exec "$CONTAINER" python -c "
from app.collectors.coinbase import CoinbaseOHLCVCollector
n = CoinbaseOHLCVCollector(granularity='1h', lookback_hours=2).run()
print(f'Saved {n} rows')
"
}

cmd_test_analysis() {
    need_docker; need_container
    info "Running technical analysis pipeline (1h candles)..."
    docker exec "$CONTAINER" python -c "
from app.analysis.indicators import run_analysis
n = run_analysis('1h')
print(f'Saved {n} indicator rows')
"
}

cmd_test_signal() {
    need_docker; need_container
    local SYMBOL="${2:-BTC-USD}"
    info "Generating AI signal for $SYMBOL..."
    docker exec "$CONTAINER" python -c "
from app.ai.signals import generate_signal
sig = generate_signal('$SYMBOL')
if sig:
    print(f\"Action    : {sig['action']}\")
    print(f\"Confidence: {sig['confidence']:.0%}\")
    print(f\"Reasoning : {sig['reasoning']}\")
else:
    print('No signal generated — check logs')
"
}

cmd_ai_provider() {
    need_docker; need_container
    info "Active AI provider:"
    docker exec "$CONTAINER" python -c "
from app.ai.providers import get_provider
print(f'  {get_provider().name()}')
"
}

# ── Dispatch ── (all functions must be defined above this line) ────────────────
case "${1:-}" in
    up)               cmd_up ;;
    down)             cmd_down ;;
    restart)          cmd_restart ;;
    build)            cmd_build ;;
    reset)            cmd_reset ;;
    logs)             cmd_logs ;;
    logs-flask)       cmd_logs_flask ;;
    logs-worker)      cmd_logs_worker ;;
    logs-beat)        cmd_logs_beat ;;
    logs-mysql)       cmd_logs_mysql ;;
    logs-redis)       cmd_logs_redis ;;
    logs-flower)      cmd_logs_flower ;;
    ps)               cmd_ps ;;
    restart-app)      cmd_restart_app ;;
    shell)            cmd_shell ;;
    backfill)         cmd_backfill ;;
    status)           cmd_status ;;
    test-collector)   cmd_test_collector ;;
    test-analysis)    cmd_test_analysis ;;
    test-signal)      cmd_test_signal "$@" ;;
    ai-provider)      cmd_ai_provider ;;
    help|--help|-h)   usage ;;
    "")               usage ;;
    *)                err "Unknown command: '$1'. Run  ./run.sh help  to see options." ;;
esac
