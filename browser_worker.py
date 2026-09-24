from __future__ import annotations

import logging
import os
import sys
import time

from database import reserve_next_publication
from browser_health import browser_health_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("browser_worker")
_INTERNAL_HEALTH_CHECK_RAN = False


def _worker_id() -> str:
    return os.getenv("WORKER_ID", os.getenv("BROWSER_WORKER_ID", f"browser-worker-{os.getpid()}"))


def _automation_enabled() -> bool:
    return os.getenv("BROWSER_AUTOMATION_ENABLED", "false").strip().lower() == "true"


def _health_check_only() -> bool:
    return os.getenv("BROWSER_HEALTH_CHECK_ONLY", "true").strip().lower() == "true"


def _run_inside_web() -> bool:
    return os.getenv("BROWSER_RUN_INSIDE_WEB", "false").strip().lower() == "true"


def _headless_mode() -> bool:
    return os.getenv("BROWSER_HEADLESS", "true").strip().lower() == "true"


def _browser_max_concurrency() -> int:
    raw = int(os.getenv("BROWSER_MAX_CONCURRENCY", "1"))
    return 1 if raw <= 1 else 1


def _browser_timeout_seconds() -> int:
    return max(5, int(os.getenv("BROWSER_TIMEOUT_SECONDS", "30")))


def _poll_interval_seconds() -> int:
    return max(1, int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", os.getenv("BROWSER_WORKER_POLL_SECONDS", "5"))))


def reserve_single_job_for_worker(worker_id: str | None = None, lease_seconds: int | None = None) -> dict | None:
    """Reserve at most one queued job without publishing externally. This is future-mode only."""
    if not _automation_enabled():
        logger.info("Worker %s: automation disabled; no job reservation in this mode.", worker_id or _worker_id())
        return None

    if _health_check_only():
        logger.info("Worker %s: health-check-only mode active; job reservation disabled.", worker_id or _worker_id())
        return None

    target_worker_id = worker_id or _worker_id()
    lease = int(lease_seconds or os.getenv("BROWSER_WORKER_LEASE_SECONDS", "60"))
    max_attempts = max(1, int(os.getenv("BROWSER_WORKER_MAX_ATTEMPTS", "3")))

    try:
        publication = reserve_next_publication(worker_id=target_worker_id, lease_seconds=lease, max_attempts=max_attempts)
    except Exception as exc:  # pragma: no cover - safe failure path, worker remains alive
        logger.warning(
            "Worker %s: failed to reserve a queued job: type=%s message=%s",
            target_worker_id,
            type(exc).__name__,
            str(exc).strip().replace("\r", " ").replace("\n", " ")[:500],
        )
        return None

    if publication is None:
        logger.info("Worker %s: no queued job available for reservation; worker remains safe.", target_worker_id)
        return None

    logger.info(
        "Worker %s: reserved a single queued job id=%s with status=%s and worker_id=%s; no publication was sent to Facebook.",
        target_worker_id,
        publication.get("id"),
        publication.get("status"),
        publication.get("worker_id"),
    )
    return publication


def run_once() -> bool:
    worker_id = _worker_id()
    if not _automation_enabled():
        logger.info("Worker %s: BROWSER_AUTOMATION_ENABLED=false; browser not started and no browser automation happens.", worker_id)
        return False

    if _health_check_only():
        logger.info(
            "Worker %s: BROWSER_HEALTH_CHECK_ONLY=true; running browser health check only and skipping any queue processing or database writes.",
            worker_id,
        )
        logger.info(
            "Worker %s: neutral browser health check started (headless=%s timeout=%ss concurrency=%s).",
            worker_id,
            str(_headless_mode()).lower(),
            _browser_timeout_seconds(),
            _browser_max_concurrency(),
        )
        try:
            health = browser_health_check()
        except Exception as exc:  # pragma: no cover - sanitized infrastructure error path
            logger.warning(
                "Worker %s: neutral browser health check raised type=%s message=%s",
                worker_id,
                type(exc).__name__,
                str(exc).strip().replace("\r", " ").replace("\n", " ")[:500],
            )
            return False

        if not health.get("ok"):
            logger.warning(
                "Worker %s: neutral browser health check reported unsuccessful result: status=%s details=%s",
                worker_id,
                health.get("status"),
                health.get("details"),
            )
            return False

        logger.info("Worker %s: neutral browser check succeeded without contacting Facebook or any authenticated destination.", worker_id)
        return True

    logger.info(
        "Worker %s: BROWSER_HEALTH_CHECK_ONLY=false; reserving one queued job at most once per cycle without publishing anywhere.",
        worker_id,
    )
    reserved = reserve_single_job_for_worker(worker_id=worker_id)
    if reserved is None:
        logger.info("Worker %s: no queued job available in this cycle; worker remains safe and does not publish.", worker_id)
        return False

    logger.info(
        "Worker %s: reserved a single queued job id=%s in safe future mode without publishing to Meta/Facebook/Instagram.",
        worker_id,
        reserved.get("id"),
    )
    return True


def trigger_internal_health_check_once() -> bool:
    """Runs the neutral browser health check once per process when explicitly enabled for the web service."""
    global _INTERNAL_HEALTH_CHECK_RAN

    if not _automation_enabled():
        logger.info("Web service browser check skipped because BROWSER_AUTOMATION_ENABLED is false.")
        return False

    if not _run_inside_web():
        logger.info("Web service browser check skipped because BROWSER_RUN_INSIDE_WEB is false.")
        return False

    if not _health_check_only():
        logger.info("Web service browser check skipped because BROWSER_HEALTH_CHECK_ONLY is false.")
        return False

    if _INTERNAL_HEALTH_CHECK_RAN:
        logger.info("Web service browser check already ran once in this process; no repeated Chromium launch.")
        return False

    _INTERNAL_HEALTH_CHECK_RAN = True
    logger.info("Web service activating one-time neutral browser health check inside the current Flask process.")
    return run_once()


def main() -> None:
    logger.info(
        "Background worker initialized. automation=%s headless=%s concurrency=%s timeout=%ss",
        str(_automation_enabled()).lower(),
        str(_headless_mode()).lower(),
        _browser_max_concurrency(),
        _browser_timeout_seconds(),
    )
    while True:
        run_once()
        time.sleep(_poll_interval_seconds())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        run_once()
    else:
        try:
            main()
        except KeyboardInterrupt:
            logger.info("Background worker interrupted manually.")
            raise SystemExit(0)
