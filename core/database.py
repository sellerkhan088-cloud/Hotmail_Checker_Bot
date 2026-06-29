"""
Database — PostgreSQL-ready with WAL SQLite fallback.
Connection pooling, proper indexes, scan checkpoint for restart recovery.
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    DateTime, Float, Text, Index, BigInteger
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool, NullPool
from contextlib import contextmanager
from datetime import datetime
import os, json, logging

logger = logging.getLogger(__name__)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id                  = Column(Integer, primary_key=True)
    telegram_id         = Column(BigInteger, unique=True, nullable=False, index=True)
    username            = Column(String(64))
    first_name          = Column(String(64))
    plan                = Column(String(16), default="free", index=True)
    plan_expires        = Column(DateTime)
    daily_lines_used    = Column(Integer, default=0)
    daily_lines_bonus   = Column(Integer, default=0)
    last_reset          = Column(DateTime, default=datetime.utcnow)
    total_scans         = Column(Integer, default=0)
    total_hits          = Column(Integer, default=0)
    total_lines_checked = Column(BigInteger, default=0)
    referral_code       = Column(String(32), unique=True)
    referred_by         = Column(BigInteger)
    referral_count      = Column(Integer, default=0)
    is_banned           = Column(Boolean, default=False, index=True)
    created_at          = Column(DateTime, default=datetime.utcnow, index=True)
    last_active         = Column(DateTime, default=datetime.utcnow, index=True)
    current_threads     = Column(Integer, default=50)
    current_mode        = Column(Integer, default=7)
    keywords            = Column(Text)
    results_channel     = Column(String(64))
    discord_webhook     = Column(String(256))
    notify_hits         = Column(Boolean, default=True)
    notify_xgpu         = Column(Boolean, default=True)

    def get_keywords(self):
        try:
            return json.loads(self.keywords) if self.keywords else []
        except Exception:
            return []

    def set_keywords(self, kws):
        self.keywords = json.dumps(kws)

    def needs_daily_reset(self):
        if not self.last_reset:
            return True
        now = datetime.utcnow()
        return (now.date() > self.last_reset.date())

    def do_daily_reset(self):
        self.daily_lines_used = 0
        self.last_reset = datetime.utcnow()


class Proxy(Base):
    __tablename__ = "proxies"
    id                   = Column(Integer, primary_key=True)
    user_id              = Column(BigInteger, nullable=False, index=True)
    proxy_string         = Column(String(256), nullable=False)
    proxy_type           = Column(String(16), default="http")
    is_active            = Column(Boolean, default=True, index=True)
    total_uses           = Column(Integer, default=0)
    successful_uses      = Column(Integer, default=0)
    failed_uses          = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    average_response_time= Column(Float, default=0.0)
    last_used            = Column(DateTime)
    created_at           = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_proxy_user_active', 'user_id', 'is_active'),
    )


class ScanSession(Base):
    __tablename__ = "scan_sessions"
    id           = Column(Integer, primary_key=True)
    user_id      = Column(BigInteger, nullable=False, index=True)
    session_id   = Column(String(64), unique=True, nullable=False, index=True)
    mode         = Column(Integer, default=7)
    threads      = Column(Integer, default=50)
    keywords     = Column(Text)
    total_lines  = Column(Integer, default=0)
    checked      = Column(Integer, default=0)
    hits         = Column(Integer, default=0)
    bad          = Column(Integer, default=0)
    twofa        = Column(Integer, default=0)
    errors       = Column(Integer, default=0)
    status       = Column(String(16), default="running", index=True)
    started_at   = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    result_file  = Column(String(256))
    # Checkpoint: last processed line index for resume on restart
    checkpoint   = Column(Integer, default=0)
    # JSON-serialized list of remaining combos for resume
    pending_lines = Column(Text)

    def get_pending(self):
        try:
            return json.loads(self.pending_lines) if self.pending_lines else []
        except Exception:
            return []

    def set_pending(self, lines):
        self.pending_lines = json.dumps(lines)


class CheckResult(Base):
    """Individual check results stored per session."""
    __tablename__ = "check_results"
    id          = Column(Integer, primary_key=True)
    session_id  = Column(String(64), nullable=False, index=True)
    email       = Column(String(256), nullable=False)
    password    = Column(String(256), nullable=False)
    result_type = Column(String(16), nullable=False)  # HIT, 2FA, BAD, ERROR
    details     = Column(Text)   # JSON with full capture
    checked_at  = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id         = Column(Integer, primary_key=True)
    user_id    = Column(BigInteger, index=True)
    action     = Column(String(64))
    details    = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def _make_engine(database_url: str):
    if "sqlite" in database_url:
        engine = create_engine(
            database_url,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,
            },
            pool_pre_ping=True,
            # WAL mode — critical for concurrent reads+writes without locking
            echo=False,
        )
        # Enable WAL mode and optimise for concurrency
        from sqlalchemy import event
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-65536")   # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA busy_timeout=10000")  # 10s lock wait
            cursor.execute("PRAGMA mmap_size=2147483648") # 2GB mmap
            cursor.close()
        return engine
    else:
        # PostgreSQL — production setup
        return create_engine(
            database_url,
            poolclass=QueuePool,
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )


_engine = None
_SessionFactory = None


def init_db(database_url: str = None):
    global _engine, _SessionFactory
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///orbit_checker.db")
    _engine = _make_engine(url)
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    logger.debug(f"Database initialized: {url.split('://')[0]}")


class _DBSession:
    """
    Dual-mode DB session — works as both plain call AND context manager.
    db = get_db()          → returns session directly (legacy bot.py style)
    with get_db() as db:   → context manager with auto-commit/rollback
    """
    def __init__(self):
        if _SessionFactory is None:
            init_db()
        self._session = _SessionFactory()
        # Expose all session methods directly
        self.query        = self._session.query
        self.add          = self._session.add
        self.delete       = self._session.delete
        self.commit       = self._session.commit
        self.rollback     = self._session.rollback
        self.refresh      = self._session.refresh
        self.flush        = self._session.flush
        self.close        = self._session.close
        self.execute      = self._session.execute
        self.scalar       = self._session.scalar

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._session.rollback()
        else:
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
                raise
        self._session.close()


def get_db() -> _DBSession:
    """
    Get a database session.
    Works as plain call: db = get_db()
    Works as context manager: with get_db() as db:
    """
    return _DBSession()


def get_db_session():
    """Plain session for compatibility with existing code."""
    if _SessionFactory is None:
        init_db()
    return _SessionFactory()

# Compatibility alias — original code uses SessionLocal()
SessionLocal = get_db_session


class WeeklyLeaderboard(Base):
    """Weekly hit leaderboard — reset every Monday."""
    __tablename__ = "weekly_leaderboard"
    id          = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username    = Column(String(64))
    hits        = Column(Integer, default=0)
    checked     = Column(Integer, default=0)
    week_start  = Column(DateTime, nullable=False, index=True)
    updated_at  = Column(DateTime, default=datetime.utcnow)


class HitDeduplication(Base):
    """Track emails that have been hit to prevent duplicate results."""
    __tablename__ = "hit_deduplication"
    id          = Column(Integer, primary_key=True)
    email_hash  = Column(String(64), unique=True, nullable=False, index=True)
    first_seen  = Column(DateTime, default=datetime.utcnow)


class FlaggedFile(Base):
    """Track flagged combo files per user."""
    __tablename__ = "flagged_files"
    id          = Column(Integer, primary_key=True)
    user_id     = Column(BigInteger, nullable=False, index=True)
    filename    = Column(String(256))
    reason      = Column(String(128))
    flagged_at  = Column(DateTime, default=datetime.utcnow)


class TwoFAQueue(Base):
    """2FA accounts queued for retry after timeout."""
    __tablename__ = "twofa_queue"
    id          = Column(Integer, primary_key=True)
    user_id     = Column(BigInteger, nullable=False, index=True)
    session_id  = Column(String(64))
    email       = Column(String(256))
    password    = Column(String(256))
    retry_after = Column(DateTime)
    attempts    = Column(Integer, default=0)
    created_at  = Column(DateTime, default=datetime.utcnow)

class Plan(Base):
    """Admin-created custom membership plans."""
    __tablename__ = "plans"
    id            = Column(Integer, primary_key=True)
    name          = Column(String(64), unique=True, nullable=False)
    price_usd     = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=False)
    daily_limit   = Column(Integer, default=999999)
    threads       = Column(Integer, default=200)
    session_max   = Column(Integer, default=10000)
    resume        = Column(Boolean, default=True)
    queue_skip    = Column(Boolean, default=True)
    multi_files   = Column(Integer, default=5)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    description   = Column(String(256), default='')


class Coupon(Base):
    """Admin-created discount/free coupons."""
    __tablename__ = "coupons"
    id            = Column(Integer, primary_key=True)
    code          = Column(String(32), unique=True, nullable=False, index=True)
    plan_id       = Column(Integer, nullable=True)   # if set, gives this plan free
    discount_pct  = Column(Integer, default=0)       # 0-100 % off
    free_days     = Column(Integer, default=0)       # bonus days added
    max_uses      = Column(Integer, default=1)
    uses          = Column(Integer, default=0)
    is_active     = Column(Boolean, default=True)
    expires_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    """OxaPay payment records."""
    __tablename__ = "payments"
    id            = Column(Integer, primary_key=True)
    user_id       = Column(BigInteger, nullable=False, index=True)
    plan_id       = Column(Integer, nullable=False)
    plan_name     = Column(String(64))
    amount_usd    = Column(Float, nullable=False)
    coupon_code   = Column(String(32), nullable=True)
    discount_pct  = Column(Integer, default=0)
    track_id      = Column(String(128), unique=True, nullable=False, index=True)
    pay_link      = Column(String(512))
    status        = Column(String(16), default='pending', index=True)  # pending/paid/expired/failed
    created_at    = Column(DateTime, default=datetime.utcnow)
    paid_at       = Column(DateTime, nullable=True)

