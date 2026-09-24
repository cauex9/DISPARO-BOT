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


def _playwright_browsers_path() -> str | None:
    value = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if value:
        return value
    project_root = os.getenv("RENDER_PROJECT_ROOT") or os.getenv("PROJECT_ROOT") or os.getenv("PWD")
    if project_root:
        return os.path.join(project_root, ".cache", "ms-playwright")
    return None


def _browser_timeout_seconds() -> int:
    return max(5, int(os.getenv("BROWSER_TIMEOUT_SECONDS", "30")))


def _browser_headless() -> bool:
    return os.getenv("BROWSER_HEADLESS", "true").strip().lower() == "true"


def _safe_exception_message(exc: BaseException) -> str:
    message = str(exc).strip().replace("\r", " ").replace("\n", " ")
    return message if message else f"{type(exc).__name__}"


def browser_health_check() -> dict:
    """Neutral browser smoke test without visiting Facebook or any authenticated destination."""
    result = {
        "ok": False,
        "status": "error",
        "details": "browser not initialized",
    }
    playwright: Playwright | None = None
    browser: Browser | None = None
    context = None
    page = None
    chromium_executable: str | None = None
    stage = "initializing"

    if sync_playwright is None:
        logger.warning("Browser health check aborted: Playwright is not installed in the runtime environment.")
        return {
            "ok": False,
            "status": "error",
            "details": "playwright not installed",
        }

    playwright_browsers_path = _playwright_browsers_path()
    if playwright_browsers_path:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_browsers_path
        logger.info("Playwright browsers path configured to %s", playwright_browsers_path)

    try:
        stage = "starting Playwright"
        playwright = sync_playwright().start()

        stage = "locating Chromium executable"
        try:
            chromium_executable = playwright.chromium.executable_path
        except Exception as exc:  # pragma: no cover - environment-specific path lookup failure
            logger.warning(
                "Browser health check failed during %s: type=%s message=%s",
                stage,
                type(exc).__name__,
                _safe_exception_message(exc),
            )
            raise

        if chromium_executable:
            logger.info("Chromium executable path=%s exists=%s", chromium_executable, os.path.exists(chromium_executable))

        stage = "starting Chromium"
        browser = playwright.chromium.launch(
            headless=_browser_headless(),
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )

        stage = "creating browser context"
        context = browser.new_context(viewport={"width": 1280, "height": 720})

        stage = "creating page"
        page = context.new_page()
        page.set_default_timeout(_browser_timeout_seconds() * 1000)

        stage = "rendering page"
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
            logger.warning("Browser health check failed during %s: page did not render completely.", stage)
    except Exception as exc:  # pragma: no cover - sanitised infrastructure error path
        message = _safe_exception_message(exc)
        logger.warning(
            "Browser health check failed during %s: type=%s message=%s",
            stage,
            type(exc).__name__,
            message,
        )
        result = {
            "ok": False,
            "status": "error",
            "details": f"browser startup failed during {stage}: {type(exc).__name__}: {message}",
        }
    finally:
        with suppress(Exception):
            if page is not None:
                page.close()
        with suppress(Exception):
            if context is not None:
                context.close()
        with suppress(Exception):
            if browser is not None:
                browser.close()
        with suppress(Exception):
            if playwright is not None:
                playwright.stop()
    return result
