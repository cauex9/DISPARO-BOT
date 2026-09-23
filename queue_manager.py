from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from database import get_connection, row_dict, setting, set_setting
from meta_api import MetaAPI


class QueueManager:
    def __init__(self, api: MetaAPI | None = None) -> None:
        self.api = api or MetaAPI()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    @property
    def state(self) -> str:
        return setting("queue_state", "stopped") or "stopped"

    def set_interval(self, seconds: int) -> None:
        set_setting("interval_seconds", max(5, int(seconds)))
        self._wake_event.set()

    def start(self) -> None:
        set_setting("queue_state", "running")
        self._stop_event.clear()
        self._wake_event.set()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def pause(self) -> None:
        set_setting("queue_state", "paused")
        self._wake_event.set()

    def stop(self) -> None:
        set_setting("queue_state", "stopped")
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            if self.state != "running":
                self._wake_event.wait(0.25)
                self._wake_event.clear()
                continue
            if not self.process_next():
                self.stop()
                return
            interval = max(5, int(setting("interval_seconds", "30") or 30))
            self._wake_event.wait(interval)
            self._wake_event.clear()

    def process_next(self) -> bool:
        with get_connection() as connection:
            publication = connection.execute(
                """
                SELECT p.*, g.name AS group_name, g.reference, g.active AS group_active,
                       a.title, a.description, a.price, a.product_url, a.image_url, a.active AS ad_active,
                       v.text AS variant_text
                FROM publications p
                JOIN groups_table g ON g.id = p.group_id
                JOIN ads a ON a.id = p.ad_id
                LEFT JOIN ad_variants v ON v.id = p.variant_id
                WHERE p.status = 'queued' AND g.active = 1 AND a.active = 1
                ORDER BY p.id LIMIT 1
                """
            ).fetchone()
            if not publication:
                return False
            item = row_dict(publication) or {}
            text = self.build_text(item)
            result = self.api.publish_to_group(item, item, text)
            connection.execute(
                """
                UPDATE publications SET status = ?, result = ?, error = ?,
                    prepared_text = ?, published_at = ? WHERE id = ?
                """,
                (result.status, result.result, result.error, text, datetime.now(timezone.utc).isoformat(), item["id"]),
            )
            return True

    @staticmethod
    def build_text(ad: dict, variant: dict | None = None) -> str:
        variant_text = (variant or {}).get("text") or ad.get("variant_text") or ad["description"]
        return f"{ad['title']}\n\n{variant_text}\n\nPreço: {ad['price']}\n{ad['product_url']}"
