from __future__ import annotations

import logging
import os
import sys
import time

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


def run_once() -> bool:
    worker_id = _worker_id()
    if not _automation_enabled():
        logger.info("Worker %s: BROWSER_AUTOMATION_ENABLED=false; browser not started and no browser automation happens.", worker_id)
        return False

    if not _health_check_only():
        logger.info(
            "Worker %s: BROWSER_HEALTH_CHECK_ONLY=false; browser remains disabled in this deployment because only the neutral health check is allowed.",
            worker_id,
        )
        return False

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
