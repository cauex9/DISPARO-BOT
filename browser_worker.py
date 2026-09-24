from __future__ import annotations

import logging
import os
import sys
import time

from browser_health import browser_health_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("browser_worker")


def _worker_id() -> str:
    return os.getenv("WORKER_ID", os.getenv("BROWSER_WORKER_ID", f"browser-worker-{os.getpid()}"))


def _automation_enabled() -> bool:
    return os.getenv("BROWSER_AUTOMATION_ENABLED", "false").strip().lower() == "true"


def _health_check_only() -> bool:
    return os.getenv("BROWSER_HEALTH_CHECK_ONLY", "true").strip().lower() == "true"


def _headless_mode() -> bool:
    return os.getenv("BROWSER_HEADLESS", "true").strip().lower() == "true"


def _browser_max_concurrency() -> int:
    raw = int(os.getenv("BROWSER_MAX_CONCURRENCY", "1"))
    return 1 if raw <= 1 else 1


def _browser_timeout_seconds() -> int:
    return max(5, int(os.getenv("BROWSER_TIMEOUT_SECONDS", "30")))


def _poll_interval_seconds() -> int:
    return max(1, int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", os.getenv("BROWSER_WORKER_POLL_SECONDS", "5"))))


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
    else:
        logger.info(
            "Worker %s: BROWSER_HEALTH_CHECK_ONLY=false; queue processing remains disabled in this deployment because this worker is only configured for neutral browser health checks.",
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
        logger.warning("Worker %s: neutral browser health check failed with %s.", worker_id, type(exc).__name__)
        return False

    if not health.get("ok"):
        logger.warning("Worker %s: neutral browser health check reported unsuccessful result: %s.", worker_id, health.get("details"))
        return False

    logger.info("Worker %s: neutral browser check succeeded without contacting Facebook or any authenticated destination.", worker_id)
    return True


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
