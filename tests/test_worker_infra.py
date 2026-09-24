from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.mkdtemp()) / "infra.db"))
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("FLASK_SECRET_KEY", "test-worker-secret")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")

from database import (
    create_publication_record,
    get_connection,
    init_db,
    mark_publication_status,
    recover_expired_publication_leases,
    reserve_next_publication,
)


class PublicationWorkerInfraTests(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as connection:
            connection.execute("DELETE FROM publications")
            connection.execute("DELETE FROM ad_variants")
            connection.execute("DELETE FROM ads")
            connection.execute("DELETE FROM groups_table")
            connection.execute("DELETE FROM users")
            connection.execute(
                "INSERT INTO groups_table(name, reference, active) VALUES (?, ?, ?)",
                ("Grupo Infra", "https://example.com/grupo", 1),
            )
            group_id = connection.execute("SELECT id FROM groups_table WHERE name = ?", ("Grupo Infra",)).fetchone()["id"]
            connection.execute(
                "INSERT INTO ads(title, description, price, product_url, active) VALUES (?, ?, ?, ?, ?)",
                ("Produto Infra", "Descrição", "R$ 99", "https://example.com/produto", 1),
            )
            ad_id = connection.execute("SELECT id FROM ads WHERE title = ?", ("Produto Infra",)).fetchone()["id"]
            connection.execute(
                "INSERT INTO ad_variants(ad_id, text, active) VALUES (?, ?, ?)",
                (ad_id, "Texto variante", 1),
            )
            self.group_id = group_id
            self.ad_id = ad_id
            self.variant_id = connection.execute("SELECT id FROM ad_variants WHERE ad_id = ?", (ad_id,)).fetchone()["id"]

    def test_creation_of_queued_publication(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        self.assertIsInstance(publication_id, int)
        with get_connection() as connection:
            publication = connection.execute(
                "SELECT status, idempotency_key, attempt_count, worker_id, lease_until FROM publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
        self.assertEqual(publication["status"], "queued")
        self.assertTrue(publication["idempotency_key"])
        self.assertEqual(publication["attempt_count"], 0)
        self.assertIsNone(publication["worker_id"])

    def test_atomic_reservation_moves_queued_to_processing(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        reserved = reserve_next_publication(worker_id="worker-1", lease_seconds=30, max_attempts=3)
        self.assertIsNotNone(reserved)
        self.assertEqual(reserved["id"], publication_id)
        self.assertEqual(reserved["status"], "processing")
        self.assertEqual(reserved["worker_id"], "worker-1")
        self.assertEqual(reserved["attempt_count"], 1)
        self.assertIsNotNone(reserved["lease_until"])

    def test_two_workers_cannot_reserve_the_same_job(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        results = []
        barrier = threading.Barrier(2)

        def worker(name):
            barrier.wait()
            results.append(reserve_next_publication(worker_id=name, lease_seconds=30, max_attempts=3))

        t1 = threading.Thread(target=worker, args=("worker-a",))
        t2 = threading.Thread(target=worker, args=("worker-b",))
        t1.start(); t2.start(); t1.join(); t2.join()

        reserved_ids = [item["id"] for item in results if item]
        self.assertEqual(len(reserved_ids), 1)
        self.assertIn(publication_id, reserved_ids)

    def test_idempotency_key_reuses_same_publication(self):
        first_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued", idempotency_key="job-123")
        second_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued", idempotency_key="job-123")
        self.assertEqual(first_id, second_id)

    def test_recovery_of_expired_lease(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        with get_connection() as connection:
            connection.execute(
                "UPDATE publications SET status = ?, worker_id = ?, processing_started_at = ?, lease_until = ?, attempt_count = ? WHERE id = ?",
                (
                    "processing",
                    "worker-lease",
                    datetime.now(timezone.utc).isoformat(),
                    (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                    2,
                    publication_id,
                ),
            )
        recovered = recover_expired_publication_leases(worker_id="worker-retry", lease_seconds=30, max_attempts=3)
        self.assertEqual(recovered["id"], publication_id)
        self.assertEqual(recovered["status"], "processing")
        self.assertEqual(recovered["worker_id"], "worker-retry")
        self.assertEqual(recovered["attempt_count"], 3)

    def test_max_attempts_limit_blocks_retry(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        with get_connection() as connection:
            connection.execute(
                "UPDATE publications SET attempt_count = ? WHERE id = ?",
                (3, publication_id),
            )
        reserved = reserve_next_publication(worker_id="worker-limit", lease_seconds=30, max_attempts=3)
        self.assertIsNone(reserved)
        with get_connection() as connection:
            publication = connection.execute(
                "SELECT status, error FROM publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
        self.assertEqual(publication["status"], "requires_human_action")
        self.assertIn("máximo de tentativas", publication["error"].lower())

    def test_error_and_requires_human_action_statuses(self):
        publication_id = create_publication_record(self.group_id, self.ad_id, self.variant_id, status="queued")
        error_id = mark_publication_status(publication_id, "error", "Falha fake", worker_id="worker-error")
        self.assertEqual(error_id, publication_id)
        self.assertEqual(
            mark_publication_status(publication_id, "requires_human_action", error="Requer revisão humana", worker_id="worker-human"),
            publication_id,
        )
        with get_connection() as connection:
            publication = connection.execute(
                "SELECT status, error, requires_human_action_at FROM publications WHERE id = ?",
                (publication_id,),
            ).fetchone()
        self.assertEqual(publication["status"], "requires_human_action")
        self.assertIn("revis", publication["error"].lower())
        self.assertIsNotNone(publication["requires_human_action_at"])

    def test_preserves_existing_data_during_schema_migration(self):
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO publications(group_id, ad_id, variant_id, status, result, error, prepared_text, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.group_id, self.ad_id, self.variant_id, "queued", "old-result", "old-error", "old-text", datetime.now(timezone.utc).isoformat()),
            )
            existing_id = connection.execute("SELECT id FROM publications ORDER BY id DESC LIMIT 1").fetchone()["id"]
            connection.execute("UPDATE publications SET status = 'queued' WHERE id = ?", (existing_id,))
        init_db()
        with get_connection() as connection:
            publication = connection.execute("SELECT id, status, result, error, prepared_text FROM publications WHERE id = ?", (existing_id,)).fetchone()
        self.assertEqual(publication["id"], existing_id)
        self.assertEqual(publication["status"], "queued")
        self.assertEqual(publication["result"], "old-result")
        self.assertEqual(publication["error"], "old-error")
        self.assertEqual(publication["prepared_text"], "old-text")


if __name__ == "__main__":
    unittest.main()
