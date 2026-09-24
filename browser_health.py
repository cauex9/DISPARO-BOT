from __future__ import annotations

import logging
import os
from contextlib import suppress

try:  # pragma: no cover - environment may lack Playwright until runtime install
    from playwright.sync_api import Browser, Playwright, sync_playwright
except Exception:  # pragma: no cover - handled as a sanitized infrastructure error
    Browser = None  # type: ignore[assignment]
    Playwright = None  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]

logger = logging.getLogger("browser_health")


def _browser_timeout_seconds() -> int:
    return max(5, int(os.getenv("BROWSER_TIMEOUT_SECONDS", "30")))


def _browser_headless() -> bool:
    return os.getenv("BROWSER_HEADLESS", "true").strip().lower() == "true"


def browser_health_check() -> dict:
    """Neutral browser smoke test without visiting Facebook or any authenticated destination."""
    result = {
        "ok": False,
        "status": "error",
        "details": "browser not initialized",
    }
    playwright: Playwright | None = None
    browser: Browser | None = None

    if sync_playwright is None:
        logger.warning("Browser health check aborted: Playwright is not installed in the runtime environment.")
        return {
            "ok": False,
            "status": "error",
            "details": "playwright not installed",
        }

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=_browser_headless(),
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_default_timeout(_browser_timeout_seconds() * 1000)
        page.goto("about:blank", wait_until="domcontentloaded")
        rendered = page.evaluate("document.readyState") == "complete"
        if rendered:
            result = {
                "ok": True,
                "status": "ok",
                "details": "browser rendered a neutral test page successfully",
            }
            logger.info("Browser health check successful.")
        else:
            result = {
                "ok": False,
                "status": "error",
                "details": "browser did not fully render the neutral page",
            }
            logger.warning("Browser health check failed: page did not render completely.")
    except Exception as exc:  # pragma: no cover - sanitised infrastructure error path
        logger.warning("Browser health check failed: %s", type(exc).__name__)
        result = {
            "ok": False,
            "status": "error",
            "details": f"browser startup failed: {type(exc).__name__}",
        }
    finally:
        with suppress(Exception):
            if page is not None:
                page.close()
        with suppress(Exception):
            if browser is not None:
                browser.close()
        with suppress(Exception):
            if playwright is not None:
                playwright.stop()
    return result
