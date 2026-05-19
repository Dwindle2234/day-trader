# ── Crypto Trader — single-container dev shortcuts ────────────────────────────
CONTAINER=crypto_trader
.PHONY: up down build logs shell backfill status restart reset \
        ps logs-mysql logs-redis logs-flask logs-worker logs-beat \
        supervisorctl test-collector

# ── Lifecycle ──────────────────────────────────────────────────────────────────

# Build image and start the single container (detached)
up:
	docker compose up -d --build
	@echo ""
	@echo "  Container starting — first boot takes ~30s for MySQL init."
	@echo "  Dashboard : http://localhost:5000"
	@echo "  Flower    : http://localhost:5555"
	@echo ""
	@echo "  Tip: run 'make logs' to watch startup progress."

# Stop and remove the container (data volume is preserved)
down:
	docker compose down

# Rebuild the image without layer cache
build:
	docker compose build --no-cache

# Restart just the container (no rebuild)
restart:
	docker compose restart $(CONTAINER)

# Wipe everything including the data volume (WARNING: deletes all DB data)
reset:
	docker compose down -v
	docker compose up -d --build

# ── Logs ───────────────────────────────────────────────────────────────────────

# All container output (supervisord + all services)
logs:
	docker compose logs -f $(CONTAINER)

# Individual service logs (supervisord routes each service to /data/logs/)
logs-mysql:
	docker exec $(CONTAINER) tail -f /data/logs/mysql.log

logs-redis:
	docker exec $(CONTAINER) tail -f /data/logs/redis.log

logs-flask:
	docker exec $(CONTAINER) tail -f /data/logs/flask-stdout.log

logs-worker:
	docker exec $(CONTAINER) tail -f /data/logs/celery-worker.log

logs-beat:
	docker exec $(CONTAINER) tail -f /data/logs/celery-beat.log

logs-flower:
	docker exec $(CONTAINER) tail -f /data/logs/flower-stdout.log

# ── Process management ─────────────────────────────────────────────────────────

# Show status of all supervisord-managed processes
ps:
	docker exec $(CONTAINER) supervisorctl status

# Interactive supervisorctl session
supervisorctl:
	docker exec -it $(CONTAINER) supervisorctl

# Restart just the Python services (not MySQL/Redis) after a code change
restart-app:
	docker exec $(CONTAINER) supervisorctl restart python-services:*

# ── Dev helpers ────────────────────────────────────────────────────────────────

# Bash shell inside the container
shell:
	docker exec -it $(CONTAINER) bash

# Trigger the 90-day historical data back-fill
backfill:
	docker exec $(CONTAINER) \
	  celery -A celery_app call celery_app.backfill_history_task

# Check active Celery tasks
status:
	docker exec $(CONTAINER) celery -A celery_app inspect active

# Quick smoke-test: fetch last 2 hours of BTC candles
test-collector:
	docker exec $(CONTAINER) python -c "\
from app.collectors.coinbase import CoinbaseOHLCVCollector; \
n = CoinbaseOHLCVCollector(granularity='1h', lookback_hours=2).run(); \
print(f'Saved {n} rows')"

