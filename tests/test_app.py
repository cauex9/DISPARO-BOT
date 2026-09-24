from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(TEMP_DIR.name) / "bot.db")
os.environ["DATABASE_URL"] = ""
os.environ["DRY_RUN"] = "true"
os.environ["PUBLISH_INTERVAL_SECONDS"] = "5"
os.environ["FLASK_SECRET_KEY"] = "test-only-secret-key"
os.environ["ADMIN_EMAIL"] = "  ms8830203@gmail.com  "

from bot import app, queue  # noqa: E402
from database import get_connection, init_db  # noqa: E402
from subscriptions import _webhook_event_id, has_active_subscription  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


class BotAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO users(name, email, username, password_hash) VALUES(?, ?, ?, ?)",
                ("Admin Teste", "admin@example.com", "admin-teste", generate_password_hash("senha-teste")),
            )
            user_id = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("admin-teste",)
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO subscriptions
                   (user_id, provider, identifier, plan, amount, status)
                   VALUES(?, ?, ?, ?, ?, ?)""",
                (user_id, "poseidonpay", "test-active", "monthly", 15, "ACTIVE"),
            )
        cls.client = app.test_client()

    def setUp(self):
        self.client.post("/logout")

    def login(self, password="senha-teste"):
        return self.client.post("/login", data={"identifier": "admin-teste", "password": password})

    def test_admin_email_bypasses_subscription_case_insensitively(self):
        self.client.post("/logout")
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO users(name, email, username, password_hash)
                   VALUES(?, ?, ?, ?)""",
                ("Administrador", " MS8830203@GMAIL.COM ", "admin-sem-assinatura", generate_password_hash("senha-admin")),
            )
        self.assertEqual(
            self.client.post("/login", data={"identifier": "admin-sem-assinatura", "password": "senha-admin"}).status_code,
            302,
        )
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/assinar").status_code, 302)
        self.assertNotIn("/assinar", self.client.get("/assinar").headers["Location"])

    def test_normal_user_without_subscription_cannot_spoof_admin_email(self):
        self.client.post("/logout")
        response = self.client.get("/?email=MS8830203%40GMAIL.COM")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO users(name, email, username, password_hash)
                   VALUES(?, ?, ?, ?)""",
                ("Usuário Normal", "normal@example.com", "normal-sem-assinatura", generate_password_hash("senha-normal")),
            )
        self.client.post("/login", data={"identifier": "normal-sem-assinatura", "password": "senha-normal"})
        response = self.client.get("/?email=MS8830203%40GMAIL.COM")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assinar", response.headers["Location"])

    def test_authentication_flow(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertIn("/login", self.client.get("/").headers["Location"])
        self.assertEqual(self.client.post("/login", data={"identifier": "admin-teste", "password": "errada"}).status_code, 200)
        self.assertEqual(self.client.post("/login", data={"identifier": "admin-teste", "password": "senha-teste"}).status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_registration_validation_and_login_identifiers(self):
        self.client.post("/logout")
        response = self.client.post("/register", data={
            "name": "Pessoa Teste",
            "email": "pessoa@example.com",
            "username": "pessoa-teste",
            "password": "senha-segura",
            "password_confirmation": "senha-segura",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Conta criada com sucesso.", response.get_data(as_text=True))

        with get_connection() as connection:
            user = connection.execute(
                "SELECT name, email, username, password_hash FROM users WHERE username = ?",
                ("pessoa-teste",),
            ).fetchone()
        self.assertEqual(user["name"], "Pessoa Teste")
        self.assertNotEqual(user["password_hash"], "senha-segura")

        duplicate_email = self.client.post("/register", data={
            "name": "Outra Pessoa", "email": "pessoa@example.com", "username": "outro-usuario",
            "password": "senha", "password_confirmation": "senha",
        })
        self.assertEqual(duplicate_email.status_code, 200)
        self.assertIn("e-mail já está cadastrado", duplicate_email.get_data(as_text=True))

        duplicate_username = self.client.post("/register", data={
            "name": "Outra Pessoa", "email": "outro@example.com", "username": "pessoa-teste",
            "password": "senha", "password_confirmation": "senha",
        })
        self.assertEqual(duplicate_username.status_code, 200)
        self.assertIn("usuário já está cadastrado", duplicate_username.get_data(as_text=True))

        mismatch = self.client.post("/register", data={
            "name": "Outra Pessoa", "email": "mismatch@example.com", "username": "mismatch",
            "password": "senha", "password_confirmation": "diferente",
        })
        self.assertEqual(mismatch.status_code, 200)
        self.assertIn("senhas não coincidem", mismatch.get_data(as_text=True))

        self.assertEqual(self.client.post("/login", data={"identifier": "pessoa-teste", "password": "senha-segura"}).status_code, 302)
        self.client.post("/logout")
        self.assertEqual(self.client.post("/login", data={"identifier": "pessoa@example.com", "password": "senha-segura"}).status_code, 302)
        self.assertEqual(self.client.post("/logout").status_code, 302)

    def test_subscription_gating_and_pix_creation(self):
        self.client.post("/logout")
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO users(name, email, username, password_hash)
                   VALUES(?, ?, ?, ?)""",
                ("Assinante Teste", "assinante@example.com", "assinante-teste", generate_password_hash("senha")),
            )
        self.client.post("/login", data={"identifier": "assinante-teste", "password": "senha"})
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertIn("/assinar", self.client.get("/").headers["Location"])
        self.assertEqual(self.client.get("/assinar").status_code, 200)

        provider_response = Mock(
            status_code=200,
            json=lambda: {
                "transactionId": "transaction-test",
                "status": "OK",
                "webhookToken": "provider-token",
                "subscription": {
                    "id": "subscription-test",
                    "status": "INACTIVE",
                    "startAt": "2026-09-23T00:00:00Z",
                    "nextChargeAt": "2026-10-23T00:00:00Z",
                },
                "pix": {"code": "pix-copy-paste", "image": "https://example.test/qr.png"},
            },
        )
        with patch.dict(os.environ, {
            "POSEIDO_CLIENT_ID": "public-test",
            "POSEIDO_CLIENT_SECRET": "secret-test",
            "WEBHOOK_CALLBACK_URL": "https://example.test/webhook",
        }), patch("poseidon_pay.requests.post", return_value=provider_response) as post:
            response = self.client.post("/assinar/criar")
        self.assertEqual(response.status_code, 200)
        self.assertIn("pix-copy-paste", response.get_data(as_text=True))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["amount"], 15)
        self.assertEqual(payload["product"]["price"], 15)
        self.assertEqual(payload["subscription"]["periodicityType"], "MONTHS")
        self.assertEqual(payload["subscription"]["periodicity"], 1)
        self.assertEqual(payload["subscription"]["firstChargeIn"], 0)
        self.assertEqual(self.client.get("/").status_code, 302)

        with get_connection() as connection:
            status = connection.execute(
                "SELECT status FROM subscriptions WHERE provider_subscription_id = ?",
                ("subscription-test",),
            ).fetchone()["status"]
        self.assertEqual(status, "INACTIVE")

        failed_response = Mock(status_code=400, json=lambda: {"errorDescription": "invalid data"})
        with patch.dict(os.environ, {
            "POSEIDO_CLIENT_ID": "public-test",
            "POSEIDO_CLIENT_SECRET": "secret-test",
            "WEBHOOK_CALLBACK_URL": "https://example.test/webhook",
        }), patch("poseidon_pay.requests.post", return_value=failed_response):
            response = self.client.post("/assinar/criar")
        self.assertEqual(response.status_code, 302)
        with get_connection() as connection:
            failed = connection.execute(
                """SELECT s.status FROM subscriptions s JOIN users u ON u.id = s.user_id
                   WHERE u.username = ? ORDER BY s.id DESC LIMIT 1""",
                ("assinante-teste",),
            ).fetchone()["status"]
        self.assertEqual(failed, "FAILED")

    def test_failed_attempt_does_not_revoke_active_subscription(self):
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO users(name, email, username, password_hash)
                   VALUES(?, ?, ?, ?)""",
                ("Ativo Teste", "ativo@example.com", "ativo-teste", generate_password_hash("senha")),
            )
            user_id = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("ativo-teste",)
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO subscriptions
                   (user_id, provider, identifier, plan, amount, status)
                   VALUES(?, ?, ?, ?, ?, ?)""",
                (user_id, "poseidonpay", "active-attempt", "monthly", 15, "ACTIVE"),
            )
            connection.execute(
                """INSERT INTO subscriptions
                   (user_id, provider, identifier, plan, amount, status)
                   VALUES(?, ?, ?, ?, ?, ?)""",
                (user_id, "poseidonpay", "failed-attempt", "monthly", 15, "FAILED"),
            )
        self.assertTrue(has_active_subscription(user_id))

    def create_webhook_subscription(self, username, identifier, transaction_id, provider_subscription_id, token, amount=15):
        with get_connection() as connection:
            connection.execute(
                """INSERT INTO users(name, email, username, password_hash)
                   VALUES(?, ?, ?, ?)""",
                (username, f"{username}@example.com", username, generate_password_hash("senha")),
            )
            user_id = connection.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO subscriptions
                   (user_id, provider, provider_subscription_id, transaction_id,
                    identifier, plan, amount, status, webhook_token)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, "poseidonpay", provider_subscription_id, transaction_id,
                 identifier, "monthly", amount, "INACTIVE", token),
            )
        return user_id

    def webhook_payload(self, identifier, transaction_id, provider_subscription_id, token, event="TRANSACTION_PAID", amount=15):
        return {
            "event": event,
            "token": token,
            "transaction": {
                "id": transaction_id,
                "clientIdentifier": identifier,
                "amount": amount,
            },
            "subscription": {
                "id": provider_subscription_id,
                "status": "ACTIVE",
                "startAt": "2026-09-23T00:00:00Z",
                "nextChargeAt": "2026-10-23T00:00:00Z",
            },
        }

    def test_webhook_requires_token_and_existing_subscription(self):
        self.assertEqual(self.client.post("/webhooks/poseidon", json={}).status_code, 400)
        user_id = self.create_webhook_subscription("webhook-token", "identifier-token", "tx-token", "sub-token", "right-token")
        payload = self.webhook_payload("identifier-token", "tx-token", "sub-token", "wrong-token")
        self.assertEqual(self.client.post("/webhooks/poseidon", json=payload).status_code, 401)
        self.assertFalse(has_active_subscription(user_id))
        unknown = self.webhook_payload("unknown", "tx-unknown", "sub-unknown", "right-token")
        self.assertEqual(self.client.post("/webhooks/poseidon", json=unknown).status_code, 400)

    def test_created_event_does_not_activate(self):
        user_id = self.create_webhook_subscription("webhook-created", "identifier-created", "tx-created", "sub-created", "created-token")
        payload = self.webhook_payload("identifier-created", "tx-created", "sub-created", "created-token", event="TRANSACTION_CREATED")
        self.assertEqual(self.client.post("/webhooks/poseidon", json=payload).status_code, 200)
        self.assertFalse(has_active_subscription(user_id))

    def test_paid_event_activates_only_matching_subscription_and_is_idempotent(self):
        user_id = self.create_webhook_subscription("webhook-paid", "identifier-paid", "tx-paid", "sub-paid", "paid-token")
        other_id = self.create_webhook_subscription("webhook-other", "identifier-other", "tx-other", "sub-other", "other-token")
        payload = self.webhook_payload("identifier-paid", "tx-paid", "sub-paid", "paid-token")
        first = self.client.post("/webhooks/poseidon", json=payload)
        duplicate = self.client.post("/webhooks/poseidon", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(has_active_subscription(user_id))
        self.assertFalse(has_active_subscription(other_id))
        with get_connection() as connection:
            event_count = connection.execute(
                "SELECT COUNT(*) AS count FROM subscription_events WHERE provider_event_id = ?",
                (_webhook_event_id(payload),),
            ).fetchone()["count"]
        self.assertEqual(event_count, 1)

    def test_wrong_amount_does_not_activate(self):
        user_id = self.create_webhook_subscription("webhook-amount", "identifier-amount", "tx-amount", "sub-amount", "amount-token")
        payload = self.webhook_payload("identifier-amount", "tx-amount", "sub-amount", "amount-token", amount=20)
        self.assertEqual(self.client.post("/webhooks/poseidon", json=payload).status_code, 400)
        self.assertFalse(has_active_subscription(user_id))

    def test_admin_routes_require_authentication(self):
        for path in ("/fila", "/grupos", "/anuncios"):
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_health_and_home(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json["dry_run"])
        self.login()
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_group_ad_queue_and_dry_run(self):
        self.login()
        response = self.client.post("/grupos", data={"name": "Grupo teste", "reference": "https://example.com/grupo", "active": "on"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/anuncios", data={"title": "Produto teste", "description": "Descricao", "price": "R$ 10", "product_url": "https://example.com/produto", "variants": ["Texto alternativo"], "active": "on"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with get_connection() as connection:
            ad_id = connection.execute("SELECT id FROM ads ORDER BY id DESC LIMIT 1").fetchone()["id"]
        response = self.client.post("/fila", data={"action": "enqueue", "ad_id": ad_id, "times": "1"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(queue.process_next())
        with get_connection() as connection:
            publication = connection.execute("SELECT status, result FROM publications ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(publication["status"], "simulated")
        self.assertEqual(publication["result"], "Simulação concluída; nada foi publicado no Facebook.")

    def test_pause_and_continue(self):
        self.login()
        self.client.post("/fila", data={"action": "pause"})
        self.assertEqual(queue.state, "paused")
        self.client.post("/fila", data={"action": "continue"})
        self.assertEqual(queue.state, "running")
        queue.stop()
        self.assertEqual(queue.state, "stopped")


if __name__ == "__main__":
    unittest.main()
