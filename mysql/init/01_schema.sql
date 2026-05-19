-- =============================================================================
-- Crypto Trader — MySQL schema
-- Runs automatically on first `docker-compose up` via initdb.d
-- =============================================================================

CREATE DATABASE IF NOT EXISTS crypto_trader CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE crypto_trader;

-- ── OHLCV candle data ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlcv (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol        VARCHAR(20)    NOT NULL,          -- e.g. "BTC-USD"
    granularity   VARCHAR(10)    NOT NULL,          -- "1m", "1h", "1d"
    ts            DATETIME       NOT NULL,          -- candle open time (UTC)
    open          DECIMAL(20,8)  NOT NULL,
    high          DECIMAL(20,8)  NOT NULL,
    low           DECIMAL(20,8)  NOT NULL,
    close         DECIMAL(20,8)  NOT NULL,
    volume        DECIMAL(30,8)  NOT NULL,
    created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_ohlcv (symbol, granularity, ts),
    INDEX idx_ohlcv_sym_gran_ts (symbol, granularity, ts)
) ENGINE=InnoDB;

-- ── Technical indicators (computed from ohlcv) ────────────────────────────────
CREATE TABLE IF NOT EXISTS indicators (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol        VARCHAR(20)    NOT NULL,
    granularity   VARCHAR(10)    NOT NULL,
    ts            DATETIME       NOT NULL,
    rsi_14        DECIMAL(10,4),
    macd          DECIMAL(20,8),
    macd_signal   DECIMAL(20,8),
    macd_hist     DECIMAL(20,8),
    bb_upper      DECIMAL(20,8),
    bb_mid        DECIMAL(20,8),
    bb_lower      DECIMAL(20,8),
    ema_12        DECIMAL(20,8),
    ema_26        DECIMAL(20,8),
    atr_14        DECIMAL(20,8),
    volume_sma_20 DECIMAL(30,8),
    created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_indicators (symbol, granularity, ts),
    INDEX idx_ind_sym_gran_ts (symbol, granularity, ts)
) ENGINE=InnoDB;

-- ── AI signals (Claude's buy/sell/hold decisions) ─────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol        VARCHAR(20)    NOT NULL,
    ts            DATETIME       NOT NULL,
    action        ENUM('BUY','SELL','HOLD') NOT NULL,
    confidence    DECIMAL(4,3)   NOT NULL,          -- 0.000–1.000
    target_price  DECIMAL(20,8),                    -- Claude's price target
    stop_loss     DECIMAL(20,8),                    -- suggested stop
    reasoning     TEXT           NOT NULL,          -- full Claude explanation
    prompt_tokens INT UNSIGNED,
    completion_tokens INT UNSIGNED,
    created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_signals_sym_ts (symbol, ts),
    INDEX idx_signals_action (action)
) ENGINE=InnoDB;

-- ── Paper trading portfolio state ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    cash_balance  DECIMAL(20,8)  NOT NULL DEFAULT 10000.00,
    total_value   DECIMAL(20,8)  NOT NULL DEFAULT 10000.00,
    updated_at    DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Seed with the initial portfolio row
INSERT INTO portfolio (cash_balance, total_value) VALUES (10000.00, 10000.00);

-- ── Open positions (virtual holdings) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol        VARCHAR(20)    NOT NULL,
    quantity      DECIMAL(20,8)  NOT NULL,
    avg_buy_price DECIMAL(20,8)  NOT NULL,
    stop_loss     DECIMAL(20,8),
    opened_at     DATETIME       DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_positions_sym (symbol)
) ENGINE=InnoDB;

-- ── Trade history ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    symbol        VARCHAR(20)    NOT NULL,
    side          ENUM('BUY','SELL') NOT NULL,
    quantity      DECIMAL(20,8)  NOT NULL,
    price         DECIMAL(20,8)  NOT NULL,
    total_value   DECIMAL(20,8)  NOT NULL,           -- quantity * price
    fee           DECIMAL(20,8)  NOT NULL DEFAULT 0, -- paper = 0
    pnl           DECIMAL(20,8),                     -- realised P&L (sells only)
    signal_id     BIGINT UNSIGNED,                   -- FK to signals
    reason        VARCHAR(50),                       -- "AI_SIGNAL", "STOP_LOSS"
    executed_at   DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_orders_sym (symbol),
    INDEX idx_orders_side (side),
    INDEX idx_orders_ts (executed_at)
) ENGINE=InnoDB;

-- ── Market sentiment & news ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_sentiment (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ts            DATETIME       NOT NULL,
    fear_greed_value  TINYINT UNSIGNED,              -- 0–100
    fear_greed_label  VARCHAR(30),                   -- "Extreme Fear", "Greed", etc.
    created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sentiment_ts (ts)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS news_events (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_id     VARCHAR(100)   NOT NULL,           -- CryptoPanic item ID
    title         TEXT           NOT NULL,
    url           VARCHAR(1000),
    published_at  DATETIME,
    currencies    VARCHAR(200),                      -- "BTC,ETH" etc.
    sentiment     ENUM('positive','negative','neutral') DEFAULT 'neutral',
    votes_positive SMALLINT UNSIGNED DEFAULT 0,
    votes_negative SMALLINT UNSIGNED DEFAULT 0,
    created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_news_source (source_id),
    INDEX idx_news_published (published_at)
) ENGINE=InnoDB;

-- ── Collector audit log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collector_log (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    collector     VARCHAR(50)    NOT NULL,           -- "coinbase", "coingecko", etc.
    status        ENUM('ok','error') NOT NULL,
    records_saved INT UNSIGNED   DEFAULT 0,
    error_msg     TEXT,
    duration_ms   INT UNSIGNED,
    ran_at        DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_clog_collector (collector),
    INDEX idx_clog_ran_at (ran_at)
) ENGINE=InnoDB;
