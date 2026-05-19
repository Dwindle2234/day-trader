"""
SQLAlchemy ORM models — mirrors mysql/init/01_schema.sql
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Column, DateTime, Enum, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class OHLCV(Base):
    __tablename__ = "ohlcv"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol      = Column(String(20),  nullable=False)
    granularity = Column(String(10),  nullable=False)
    ts          = Column(DateTime,    nullable=False)
    open        = Column(Numeric(20, 8), nullable=False)
    high        = Column(Numeric(20, 8), nullable=False)
    low         = Column(Numeric(20, 8), nullable=False)
    close       = Column(Numeric(20, 8), nullable=False)
    volume      = Column(Numeric(30, 8), nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "granularity", "ts", name="uq_ohlcv"),
        Index("idx_ohlcv_sym_gran_ts", "symbol", "granularity", "ts"),
    )

    def __repr__(self):
        return f"<OHLCV {self.symbol} {self.granularity} {self.ts} close={self.close}>"


class Indicator(Base):
    __tablename__ = "indicators"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol        = Column(String(20), nullable=False)
    granularity   = Column(String(10), nullable=False)
    ts            = Column(DateTime,   nullable=False)
    rsi_14        = Column(Numeric(10, 4))
    macd          = Column(Numeric(20, 8))
    macd_signal   = Column(Numeric(20, 8))
    macd_hist     = Column(Numeric(20, 8))
    bb_upper      = Column(Numeric(20, 8))
    bb_mid        = Column(Numeric(20, 8))
    bb_lower      = Column(Numeric(20, 8))
    ema_12        = Column(Numeric(20, 8))
    ema_26        = Column(Numeric(20, 8))
    atr_14        = Column(Numeric(20, 8))
    volume_sma_20 = Column(Numeric(30, 8))
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "granularity", "ts", name="uq_indicators"),
        Index("idx_ind_sym_gran_ts", "symbol", "granularity", "ts"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id                = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol            = Column(String(20), nullable=False)
    ts                = Column(DateTime,   nullable=False)
    action            = Column(Enum("BUY", "SELL", "HOLD"), nullable=False)
    confidence        = Column(Numeric(4, 3), nullable=False)
    target_price      = Column(Numeric(20, 8))
    stop_loss         = Column(Numeric(20, 8))
    reasoning         = Column(Text, nullable=False)
    prompt_tokens     = Column(Integer)
    completion_tokens = Column(Integer)
    created_at        = Column(DateTime, default=datetime.utcnow)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    cash_balance  = Column(Numeric(20, 8), nullable=False, default=Decimal("10000.00"))
    total_value   = Column(Numeric(20, 8), nullable=False, default=Decimal("10000.00"))
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol        = Column(String(20), nullable=False, unique=True)
    quantity      = Column(Numeric(20, 8), nullable=False)
    avg_buy_price = Column(Numeric(20, 8), nullable=False)
    stop_loss     = Column(Numeric(20, 8))
    opened_at     = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol      = Column(String(20), nullable=False)
    side        = Column(Enum("BUY", "SELL"), nullable=False)
    quantity    = Column(Numeric(20, 8), nullable=False)
    price       = Column(Numeric(20, 8), nullable=False)
    total_value = Column(Numeric(20, 8), nullable=False)
    fee         = Column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    pnl         = Column(Numeric(20, 8))
    signal_id   = Column(BigInteger)
    reason      = Column(String(50))
    executed_at = Column(DateTime, default=datetime.utcnow)


class MarketSentiment(Base):
    __tablename__ = "market_sentiment"

    id                = Column(BigInteger, primary_key=True, autoincrement=True)
    ts                = Column(DateTime, nullable=False)
    fear_greed_value  = Column(SmallInteger)
    fear_greed_label  = Column(String(30))
    created_at        = Column(DateTime, default=datetime.utcnow)


class NewsEvent(Base):
    __tablename__ = "news_events"

    id             = Column(BigInteger, primary_key=True, autoincrement=True)
    source_id      = Column(String(100), nullable=False, unique=True)
    title          = Column(Text, nullable=False)
    url            = Column(String(1000))
    published_at   = Column(DateTime)
    currencies     = Column(String(200))
    sentiment      = Column(Enum("positive", "negative", "neutral"), default="neutral")
    votes_positive = Column(SmallInteger, default=0)
    votes_negative = Column(SmallInteger, default=0)
    created_at     = Column(DateTime, default=datetime.utcnow)


class CollectorLog(Base):
    __tablename__ = "collector_log"

    id           = Column(BigInteger, primary_key=True, autoincrement=True)
    collector    = Column(String(50), nullable=False)
    status       = Column(Enum("ok", "error"), nullable=False)
    records_saved = Column(Integer, default=0)
    error_msg    = Column(Text)
    duration_ms  = Column(Integer)
    ran_at       = Column(DateTime, default=datetime.utcnow)
