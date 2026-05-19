# Crypto Trader 🤖

Paper-trading system powered by Claude AI. Collects market data from free APIs,
computes technical indicators, and uses Claude to generate buy/sell signals —
all running in Docker on your local server.

## Quick start

Everything — MySQL, Redis, Celery, Flask — runs inside **one container**,
managed by supervisord.

### 1. Copy and fill in your API keys

```bash
cp .env.example .env
```

Edit `.env` and set:

| Variable | Where to get it |
|---|---|
| `COINBASE_API_KEY` | [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) → API Keys → New (View permission only) |
| `COINBASE_API_SECRET` | Same page — paste the full PEM key |
| `CRYPTOPANIC_API_KEY` | [cryptopanic.com/api](https://cryptopanic.com/api) — free registration |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

CoinGecko and the Fear & Greed index need no API key.

### 2. Build and start

```bash
make up
```

First boot takes ~30 seconds — the entrypoint initialises MySQL,
creates the database and user, and applies the schema before supervisord
starts all the Python services.

### 3. Watch startup progress

```bash
make logs        # full container output
make ps          # show status of each supervised process
```

### 4. Back-fill 90 days of history (first run)

```bash
make backfill
```

### 5. Open the interfaces

| Interface | URL |
|---|---|
| Trading dashboard | http://localhost:5000 |
| Celery task monitor | http://localhost:5555 |

---

## Collection schedule

| Task | Frequency | Source |
|---|---|---|
| Live ticker prices | Every minute | Coinbase API |
| Crypto news | Every 15 min | CryptoPanic |
| Hourly candles | Every hour :02 | Coinbase API |
| Market cap / volume | Every hour :05 | CoinGecko |
| Fear & Greed index | Every hour :10 | alternative.me |
| Daily candles | 00:30 UTC | Coinbase API |

---

## Watchlist

Default: `BTC-USD,ETH-USD,SOL-USD,DOGE-USD,AVAX-USD`

To change, update `WATCHLIST` in `.env` using Coinbase product IDs and restart.

---

## Common commands

```bash
make logs           # follow all container output
make ps             # status of every supervised process
make logs-flask     # Flask-only log
make logs-worker    # Celery worker log
make logs-beat      # Celery beat scheduler log
make logs-mysql     # MySQL error log
make shell          # bash inside the container
make restart-app    # hot-reload Python services after a code change (no rebuild)
make status         # active Celery tasks
make test-collector # smoke-test the Coinbase collector
make reset          # wipe all data and start fresh
```

---

## Architecture

```
Single Docker container  (crypto_trader)
│
├── supervisord          — process manager, keeps everything alive
│   ├── mysql            — MySQL 8    (priority 10, starts first)
│   ├── redis            — Redis 7    (priority 20)
│   ├── celery-worker    — data collection tasks (priority 30)
│   ├── celery-beat      — cron scheduler        (priority 40)
│   ├── flower           — task monitor :5555    (priority 50)
│   └── flask            — dashboard    :5000    (priority 60, starts last)
│
└── /data  volume        — persists MySQL + Redis across restarts
    ├── mysql/           — MySQL datadir
    ├── redis/           — Redis RDB snapshot
    └── logs/            — all service logs

External APIs (no cost for data reads)
  ├── Coinbase Advanced Trade  →  OHLCV candles, ticker
  ├── CoinGecko               →  Market cap, volume, history
  ├── alternative.me          →  Fear & Greed index
  └── CryptoPanic             →  News & sentiment
```

---

## Next build steps

- [ ] Step 3: Technical analysis pipeline (RSI, MACD, Bollinger)
- [ ] Step 4: Claude AI signal engine
- [ ] Step 5: Paper trading engine + risk manager
- [ ] Step 6: Flask web dashboard
