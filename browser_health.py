from __future__ import annotations

import base64
import json
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
    configured = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured:
        return configured

    project_root = os.getenv("RENDER_PROJECT_ROOT") or os.getenv("PROJECT_ROOT") or os.getenv("PWD")
    if not project_root:
        return None

    project_root = project_root.rstrip("/")
    candidate = os.path.join(project_root, "src", ".cache", "ms-playwright")
    if os.path.isdir(os.path.join(project_root, "src")):
        return candidate
    return os.path.join(project_root, ".cache", "ms-playwright")


def _browser_timeout_seconds() -> int:
    return max(5, int(os.getenv("BROWSER_TIMEOUT_SECONDS", "30")))


def _browser_headless() -> bool:
    return os.getenv("BROWSER_HEADLESS", "true").strip().lower() == "true"


def _safe_exception_message(exc: BaseException) -> str:
    message = str(exc).strip().replace("\r", " ").replace("\n", " ")
    return message if message else f"{type(exc).__name__}"


def _parse_facebook_storage_state(raw: str | None) -> dict | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        if value.startswith("{") or value.startswith("["):
            payload = json.loads(value)
        else:
            decoded = base64.b64decode(value, validate=True)
            payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def check_facebook_session(storage_state: str | None = None) -> dict:
    """Check whether a supplied Playwright storage state yields an authenticated Facebook session without publishing or logging any secrets."""
    configured_state = storage_state if storage_state is not None else os.getenv("FACEBOOK_STORAGE_STATE", "").strip()
    if not configured_state:
        return {
            "ok": False,
            "status": "requires_human_action",
            "details": "facebook session storage state not configured",
        }

    parsed_state = _parse_facebook_storage_state(configured_state)
    if parsed_state is None:
        return {
            "ok": False,
            "status": "requires_human_action",
            "details": "facebook session storage state is invalid or malformed",
        }

    if sync_playwright is None:
        return {
            "ok": False,
            "status": "error",
            "details": "playwright not installed",
        }

    playwright: Playwright | None = None
    browser: Browser | None = None
    context = None
    page = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=_browser_headless(),
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(storage_state=parsed_state, viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.set_default_timeout(_browser_timeout_seconds() * 1000)
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

        final_url = (page.url or "").lower()
        page_text = ""
        with suppress(Exception):
            page_text = (page.locator("body").inner_text() or "").lower()
        combined = f"{final_url} {page_text}"

        challenge_tokens = (
            "checkpoint",
            "challenge",
            "verify it's you",
            "security check",
            "confirm your identity",
            "two-factor",
            "two factor",
            "suspicious login",
        )
        login_tokens = ("log in", "login", "email or phone", "password", "create new account")
        positive_url_tokens = (
            "/home.php",
            "/feed/",
            "/messages",
            "/notifications",
            "/watch",
            "/marketplace",
            "/profile.php",
            "/groups/",
        )
        positive_body_tokens = (
            "what's on your mind",
            "stories",
            "messages",
            "notifications",
            "watch",
            "marketplace",
            "profile",
            "feed",
        )

        if any(token in combined for token in challenge_tokens):
            return {
                "ok": False,
                "status": "requires_human_action",
                "details": "facebook session requires human action or a security challenge",
            }
        if any(token in combined for token in login_tokens):
            return {
                "ok": False,
                "status": "unauthenticated",
                "details": "facebook session is not authenticated",
            }

        has_positive_url = any(token in final_url for token in positive_url_tokens)
        has_positive_body = any(token in page_text for token in positive_body_tokens)
        if has_positive_url or has_positive_body:
            return {
                "ok": True,
                "status": "authenticated",
                "details": "facebook session showed authenticated-only DOM or URL evidence and no publish action was attempted",
            }

        return {
            "ok": False,
            "status": "requires_human_action",
            "details": "facebook session could not be positively identified as authenticated",
        }
    except Exception as exc:
        logger.warning(
            "Facebook session check failed: type=%s message=%s",
            type(exc).__name__,
            _safe_exception_message(exc),
        )
        return {
            "ok": False,
            "status": "error",
            "details": "facebook session check failed",
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

    effective_playwright_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not effective_playwright_path:
        effective_playwright_path = _playwright_browsers_path() or ""
    if effective_playwright_path and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = effective_playwright_path
    logger.info("PLAYWRIGHT_BROWSERS_PATH effective=%s", os.getenv("PLAYWRIGHT_BROWSERS_PATH", "<unset>"))

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
            logger.info("playwright.chromium.executable_path=%s exists=%s", chromium_executable, os.path.exists(chromium_executable))
        else:
            logger.warning("playwright.chromium.executable_path returned empty value; PLAYWRIGHT_BROWSERS_PATH effective=%s", os.getenv("PLAYWRIGHT_BROWSERS_PATH", "<unset>"))

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
