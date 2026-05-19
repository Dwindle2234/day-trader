"""
Base class shared by all data collectors.
Provides structured logging, retry logic, and DB audit trail.
"""
import time
from abc import ABC, abstractmethod
from datetime import datetime

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.database import db_session
from app.models import CollectorLog


class BaseCollector(ABC):
    """
    Subclass this and implement `collect()`.
    Call `run()` from your Celery tasks.
    """

    name: str = "base"

    def run(self) -> int:
        """Execute the collector, log result to DB. Returns records saved."""
        start = time.monotonic()
        records = 0
        error_msg = None

        try:
            records = self.collect()
            logger.info(f"[{self.name}] saved {records} records")
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"[{self.name}] failed: {exc}")
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._log_run(
                status="ok" if error_msg is None else "error",
                records=records,
                error_msg=error_msg,
                duration_ms=duration_ms,
            )

        return records

    @abstractmethod
    def collect(self) -> int:
        """Fetch data and persist to DB. Return number of records saved."""
        ...

    def _log_run(self, status: str, records: int, error_msg: str | None, duration_ms: int):
        try:
            with db_session() as session:
                session.add(CollectorLog(
                    collector=self.name,
                    status=status,
                    records_saved=records,
                    error_msg=error_msg,
                    duration_ms=duration_ms,
                    ran_at=datetime.utcnow(),
                ))
        except Exception as e:
            logger.warning(f"[{self.name}] could not write collector log: {e}")

    # ── Shared retry decorator for HTTP calls ─────────────────────────────────
    @staticmethod
    def with_retry(func):
        """Decorator: retry up to 3 times with exponential back-off."""
        return retry(
            reraise=True,
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        )(func)
