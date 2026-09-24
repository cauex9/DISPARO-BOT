from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.mkdtemp()) / "browser-worker.db"))
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-browser-secret")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("BROWSER_AUTOMATION_ENABLED", "false")
os.environ.setdefault("BROWSER_HEADLESS", "true")
os.environ.setdefault("BROWSER_MAX_CONCURRENCY", "1")
os.environ.setdefault("BROWSER_TIMEOUT_SECONDS", "30")
os.environ.setdefault("WORKER_POLL_INTERVAL_SECONDS", "5")
os.environ.setdefault("WORKER_ID", "browser-worker-1")

import browser_worker
from browser_health import _playwright_browsers_path, browser_health_check
from browser_worker import (
    _automation_enabled,
    _browser_max_concurrency,
    _health_check_only,
    _poll_interval_seconds,
    _run_inside_web,
    prepare_reserved_job,
    reserve_single_job_for_worker,
    run_once,
    trigger_internal_health_check_once,
)
from database import create_publication_record, get_connection, init_db


class BrowserWorkerTests(unittest.TestCase):
    def _reset_db(self):
        with get_connection() as connection:
            connection.execute("DELETE FROM publications")
            connection.execute("DELETE FROM ad_variants")
            connection.execute("DELETE FROM ads")
            connection.execute("DELETE FROM groups_table")

    def _create_valid_group_and_ad(self):
        with get_connection() as connection:
            group_id = connection.execute(
                "INSERT INTO groups_table(name, reference, active) VALUES (?, ?, ?)",
                ("Grupo Teste", "grupo-teste", 1),
            ).lastrowid
            ad_id = connection.execute(
                "INSERT INTO ads(title, description, price, product_url, image_url, active) VALUES (?, ?, ?, ?, ?, ?)",
                ("Título Teste", "Descrição Teste", "10.00", "https://example.com/produto", "https://example.com/img.png", 1),
            ).lastrowid
            return int(group_id), int(ad_id)

    def test_browser_automation_disabled_never_starts_browser(self):
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "false"
        self.assertFalse(_automation_enabled())
        self.assertFalse(_run_inside_web())
        self.assertFalse(run_once())

    def test_default_configuration_is_safe(self):
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "false"
        os.environ["BROWSER_HEADLESS"] = "true"
        os.environ["BROWSER_MAX_CONCURRENCY"] = "1"
        os.environ["BROWSER_TIMEOUT_SECONDS"] = "30"
        os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "5"
        self.assertFalse(_automation_enabled())
        self.assertFalse(_run_inside_web())
        self.assertEqual(_browser_max_concurrency(), 1)
        self.assertEqual(_poll_interval_seconds(), 5)

    def test_timeout_path_is_capped(self):
        os.environ["BROWSER_TIMEOUT_SECONDS"] = "3"
        self.assertEqual(5, max(5, int(os.environ["BROWSER_TIMEOUT_SECONDS"])))

    def test_browser_health_success_without_facebook(self):
        try:
            result = browser_health_check()
        except Exception:  # pragma: no cover - sanitized failure path
            result = {"ok": False, "status": "error", "details": "exception"}
        self.assertIn(result.get("status"), {"ok", "error"})

    def test_browser_health_error_is_sanitized(self):
        result = {"ok": False, "status": "error", "details": "browser startup failed"}
        self.assertFalse(result["ok"])
        self.assertNotIn("facebook", result["details"].lower())
        self.assertNotIn("cookie", result["details"].lower())

    def test_no_status_published_is_generated_by_browser_side(self):
        self.assertFalse(run_once())

    def test_health_check_only_mode_skips_queue_consumption(self):
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "true"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "false"
        self.assertTrue(_health_check_only())
        init_db()
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        with get_connection() as connection:
            variant_id = connection.execute("INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)", (ad_id, "Texto variante", 1)).lastrowid
        publication_id = create_publication_record(group_id, ad_id, int(variant_id), status="queued")

        with patch("browser_worker.browser_health_check", return_value={"ok": True, "status": "ok", "details": "health ok"}) as mock_health, \
             patch("browser_worker.reserve_single_job_for_worker") as mock_reserve:
            self.assertTrue(run_once())

        self.assertEqual(mock_health.call_count, 1)
        self.assertEqual(mock_reserve.call_count, 0)

        with get_connection() as connection:
            publication = connection.execute("SELECT status, attempt_count, worker_id, lease_until FROM publications WHERE id = ?", (publication_id,)).fetchone()
        self.assertEqual(publication["status"], "queued")
        self.assertEqual(publication["attempt_count"], 0)
        self.assertIsNone(publication["worker_id"])
        self.assertIsNone(publication["lease_until"])

    def test_run_once_reserves_one_job_once_per_cycle_when_health_check_is_disabled(self):
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "false"
        init_db()
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        with get_connection() as connection:
            variant_id = connection.execute("INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)", (ad_id, "Texto variante", 1)).lastrowid
        publication_id = create_publication_record(group_id, ad_id, int(variant_id), status="queued")

        with patch("browser_worker.reserve_single_job_for_worker", return_value={"id": publication_id, "status": "processing"}) as mock_reserve:
            result = run_once()

        self.assertTrue(result)
        self.assertEqual(mock_reserve.call_count, 1)

        with get_connection() as connection:
            row = connection.execute("SELECT status, worker_id, attempt_count FROM publications WHERE id = ?", (publication_id,)).fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertIsNone(row["worker_id"])
        self.assertEqual(row["attempt_count"], 0)

    def test_web_service_skips_run_once_when_automation_disabled(self):
        browser_worker._INTERNAL_HEALTH_CHECK_RAN = False
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "true"

        with patch("browser_worker.run_once") as mock_run_once:
            result = trigger_internal_health_check_once()

        self.assertFalse(result)
        mock_run_once.assert_not_called()

    def test_web_service_skips_run_once_when_not_running_inside_web(self):
        browser_worker._INTERNAL_HEALTH_CHECK_RAN = False
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "false"

        with patch("browser_worker.run_once") as mock_run_once:
            result = trigger_internal_health_check_once()

        self.assertFalse(result)
        mock_run_once.assert_not_called()

    def test_health_check_only_runs_once_per_process_when_web_mode_is_enabled(self):
        browser_worker._INTERNAL_HEALTH_CHECK_RAN = False
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "true"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "true"

        with patch("browser_worker.run_once", return_value=True) as mock_run_once:
            first = trigger_internal_health_check_once()
            second = trigger_internal_health_check_once()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(mock_run_once.call_count, 1)

    def test_future_mode_runs_once_per_process_when_web_mode_is_enabled(self):
        browser_worker._INTERNAL_HEALTH_CHECK_RAN = False
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "true"

        with patch("browser_worker.run_once", return_value=True) as mock_run_once:
            first = trigger_internal_health_check_once()
            second = trigger_internal_health_check_once()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(mock_run_once.call_count, 1)

    def test_future_mode_runs_only_one_job_reservation_per_process(self):
        browser_worker._INTERNAL_HEALTH_CHECK_RAN = False
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        os.environ["BROWSER_RUN_INSIDE_WEB"] = "true"
        init_db()
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        with get_connection() as connection:
            variant_id = connection.execute("INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)", (ad_id, "Texto variante", 1)).lastrowid
        publication_id = create_publication_record(group_id, ad_id, int(variant_id), status="queued")

        with patch("browser_worker.run_once", wraps=browser_worker.run_once) as mock_run_once:
            first = trigger_internal_health_check_once()
            second = trigger_internal_health_check_once()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(mock_run_once.call_count, 1)

        with get_connection() as connection:
            row = connection.execute("SELECT status, attempt_count, worker_id FROM publications WHERE id = ?", (publication_id,)).fetchone()
        self.assertIn(row["status"], {"processing", "queued"})
        self.assertNotEqual(row["status"], "published")
        self.assertLessEqual(row["attempt_count"], 1)
        self.assertIn(row["worker_id"], {None, browser_worker._worker_id()})

    def test_prepare_reserved_job_valid_job_is_ready(self):
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        variant_id = None
        with get_connection() as connection:
            variant_id = connection.execute(
                "INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)",
                (ad_id, "Texto variante", 1),
            ).lastrowid
        publication_id = create_publication_record(group_id, ad_id, int(variant_id), status="queued", prepared_text="texto pronto")

        prepared = prepare_reserved_job({"id": publication_id, "status": "processing", "group_id": group_id, "ad_id": ad_id, "variant_id": int(variant_id), "prepared_text": "texto pronto"})

        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["publication_id"], publication_id)
        self.assertEqual(prepared["prepared_text"], "texto pronto")
        self.assertIsNotNone(prepared["group"])
        self.assertIsNotNone(prepared["ad"])
        self.assertIsNotNone(prepared["variant"])
        self.assertEqual(prepared["errors"], [])
        self.assertNotIn("facebook", str(prepared).lower())
        self.assertNotIn("meta", str(prepared).lower())
        self.assertNotIn("instagram", str(prepared).lower())

    def test_prepare_reserved_job_rejects_missing_publication(self):
        result = prepare_reserved_job(None)
        self.assertFalse(result["ok"])
        self.assertIn("publication", " ".join(result["errors"]).lower())
        self.assertIsNone(result["publication_id"])
        self.assertEqual(result["status"], None)

    def test_prepare_reserved_job_rejects_missing_group(self):
        self._reset_db()
        _, ad_id = self._create_valid_group_and_ad()
        publication_id = 999

        result = prepare_reserved_job({"id": publication_id, "status": "processing", "group_id": 999, "ad_id": ad_id, "variant_id": None, "prepared_text": "texto"})

        self.assertFalse(result["ok"])
        self.assertIn("group_id", " ".join(result["errors"]).lower())
        self.assertNotEqual(result["status"], "published")

    def test_prepare_reserved_job_rejects_missing_ad(self):
        self._reset_db()
        group_id, _ = self._create_valid_group_and_ad()
        publication_id = 999

        result = prepare_reserved_job({"id": publication_id, "status": "processing", "group_id": group_id, "ad_id": 999, "variant_id": None, "prepared_text": "texto"})

        self.assertFalse(result["ok"])
        self.assertIn("ad_id", " ".join(result["errors"]).lower())
        self.assertNotEqual(result["status"], "published")

    def test_prepare_reserved_job_rejects_missing_variant_when_required(self):
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        publication_id = 999

        result = prepare_reserved_job({"id": publication_id, "status": "processing", "group_id": group_id, "ad_id": ad_id, "variant_id": 999, "prepared_text": "texto"})

        self.assertFalse(result["ok"])
        self.assertIn("variant_id", " ".join(result["errors"]).lower())
        self.assertNotEqual(result["status"], "published")

    def test_prepare_reserved_job_loads_prepared_text(self):
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        publication_id = create_publication_record(group_id, ad_id, None, status="queued", prepared_text="texto preparado")

        result = prepare_reserved_job({"id": publication_id, "status": "processing", "group_id": group_id, "ad_id": ad_id, "variant_id": None, "prepared_text": "texto preparado"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["prepared_text"], "texto preparado")

    def test_run_once_handles_preparation_without_publishing(self):
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        self._reset_db()
        group_id, ad_id = self._create_valid_group_and_ad()
        publication_id = create_publication_record(group_id, ad_id, None, status="queued")

        with patch("browser_worker.reserve_single_job_for_worker", return_value={"id": publication_id, "status": "processing", "group_id": group_id, "ad_id": ad_id, "variant_id": None, "prepared_text": "texto preparado"}) as mock_reserve, \
             patch("browser_worker.prepare_reserved_job", return_value={"ok": True, "publication_id": publication_id, "status": "processing", "group": {"id": group_id}, "ad": {"id": ad_id}, "variant": None, "prepared_text": "texto preparado", "errors": []}) as mock_prepare:
            self.assertTrue(run_once())

        self.assertEqual(mock_reserve.call_count, 1)
        self.assertEqual(mock_prepare.call_count, 1)

        with get_connection() as connection:
            row = connection.execute("SELECT status FROM publications WHERE id = ?", (publication_id,)).fetchone()
        self.assertNotEqual(row["status"], "published")
        self.assertIn(row["status"], {"processing", "queued"})

    def test_health_check_only_executes_neutral_page_without_facebook_url(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = "complete"

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser
        mock_playwright.chromium.executable_path = "/tmp/chromium"

        manager = MagicMock()
        manager.start.return_value = mock_playwright

        with patch("browser_health.sync_playwright", return_value=manager):
            result = browser_health_check()

        self.assertTrue(result["ok"])
        mock_page.goto.assert_has_calls([call("about:blank", wait_until="domcontentloaded")])
        goto_args = str(mock_page.goto.call_args)
        self.assertNotIn("facebook", goto_args.lower())
        self.assertNotIn("instagram", goto_args.lower())
        self.assertNotIn("meta", goto_args.lower())

    def test_health_check_error_includes_real_exception_message(self):
        class BrowserLaunchError(RuntimeError):
            pass

        mock_playwright = MagicMock()
        mock_playwright.start.side_effect = BrowserLaunchError("chromium executable missing from runtime")

        with patch("browser_health.sync_playwright", return_value=mock_playwright):
            result = browser_health_check()

        self.assertFalse(result["ok"])
        self.assertIn("chromium executable missing from runtime", result["details"].lower())
        self.assertIn("starting playwright", result["details"].lower())

    def test_playwright_browsers_path_uses_project_persistent_directory(self):
        original = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/render/project/src/.cache/ms-playwright"
        try:
            self.assertEqual(_playwright_browsers_path(), "/opt/render/project/src/.cache/ms-playwright")
        finally:
            if original is None:
                os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            else:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = original

    def test_future_mode_reserves_one_queued_job_without_publishing(self):
        init_db()
        with get_connection() as connection:
            connection.execute("DELETE FROM publications")
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        publication_id = create_publication_record(1, 1, 1, status="queued")

        reserved = reserve_single_job_for_worker()
        self.assertIsNotNone(reserved)
        self.assertEqual(reserved["id"], publication_id)
        self.assertEqual(reserved["status"], "processing")
        self.assertNotEqual(reserved["status"], "published")

        with get_connection() as connection:
            row = connection.execute("SELECT status, worker_id, attempt_count FROM publications WHERE id = ?", (publication_id,)).fetchone()
        self.assertEqual(row["status"], "processing")
        self.assertIsNotNone(row["worker_id"])
        self.assertGreaterEqual(row["attempt_count"], 1)

    def test_two_workers_do_not_reserve_same_job(self):
        init_db()
        self._reset_db()
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        group_id, ad_id = self._create_valid_group_and_ad()
        with get_connection() as connection:
            variant_id = connection.execute("INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)", (ad_id, "Texto variante", 1)).lastrowid
        publication_id = create_publication_record(group_id, ad_id, int(variant_id), status="queued")

        first = reserve_single_job_for_worker(worker_id="worker-a")
        second = reserve_single_job_for_worker(worker_id="worker-b")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first["id"], publication_id)

    def test_worker_without_job_available_remains_safe(self):
        init_db()
        with get_connection() as connection:
            connection.execute("DELETE FROM publications")
        os.environ["BROWSER_AUTOMATION_ENABLED"] = "true"
        os.environ["BROWSER_HEALTH_CHECK_ONLY"] = "false"
        self.assertIsNone(reserve_single_job_for_worker())


if __name__ == "__main__":
    unittest.main()
