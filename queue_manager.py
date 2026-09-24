from __future__ import annotations

import os
import threading

from database import mark_publication_status, reserve_next_publication, setting, set_setting


class QueueManager:
    def __init__(self, api=None) -> None:
        self.api = api
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
        worker_id = f"queue-manager:{os.getpid()}"
        lease_seconds = max(5, int(os.getenv("BROWSER_WORKER_LEASE_SECONDS", "60")))
        max_attempts = max(1, int(os.getenv("BROWSER_WORKER_MAX_ATTEMPTS", "3")))
        publication = reserve_next_publication(worker_id=worker_id, lease_seconds=lease_seconds, max_attempts=max_attempts)
        if publication is None:
            return False

        dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        if dry_run:
            mark_publication_status(
                publication["id"],
                "simulated",
                result="Simulação concluída; nada foi publicado no Facebook.",
                worker_id=worker_id,
            )
            return True

        result_message = (
            "Teste de infraestrutura do worker: automação externa desativada. "
            "Nenhuma publicação real foi enviada ao Facebook."
        )
        mark_publication_status(
            publication["id"],
            "error",
            error=result_message,
            result="Mock interno seguro concluído; não é uma publicação real.",
            worker_id=worker_id,
        )
        return True

    @staticmethod
    def build_text(ad: dict, variant: dict | None = None) -> str:
        variant_text = (variant or {}).get("text") or ad.get("variant_text") or ad["description"]
        return f"{ad['title']}\n\n{variant_text}\n\nPreço: {ad['price']}\n{ad['product_url']}"
