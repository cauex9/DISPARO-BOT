# Resolved merge marker.
from __future__ import annotations

import os
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class PublicationResult:
    status: str
    result: str | None = None
    error: str | None = None


class MetaAPI:
    """Only exposes officially supported operations.

    Meta removed publish_to_groups and the Groups API in Graph API v19.
    Therefore this adapter never invents a group-publishing endpoint.
    """

    def __init__(self) -> None:
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        self.graph_version = os.getenv("META_GRAPH_VERSION", "v26.0")
        self.access_token = os.getenv("META_ACCESS_TOKEN", "")
        self.session = requests.Session()

    def publish_to_group(self, group: dict, ad: dict, text: str) -> PublicationResult:
        if self.dry_run:
            return PublicationResult("published", "Publicação simulada com sucesso")
        return PublicationResult(
            "error",
            error=(
                "Publicação automática em grupos não está disponível pela API oficial atual da Meta. "
                "Use Preparar publicação para concluir manualmente."
            ),
        )

    @staticmethod
    def prepare_manual(group: dict, ad: dict, text: str) -> dict:
        return {"group_reference": group["reference"], "text": text, "status": "prepared"}
# Legacy duplicate implementation marker.

import os
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class PublicationResult:
    status: str
    result: str | None = None
    error: str | None = None


class MetaAPI:
    """Only exposes officially supported operations.

    Meta removed publish_to_groups and the Groups API in Graph API v19.
    Therefore this adapter never invents a group-publishing endpoint.
    """

    def __init__(self) -> None:
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        self.graph_version = os.getenv("META_GRAPH_VERSION", "v26.0")
        self.access_token = os.getenv("META_ACCESS_TOKEN", "")
        self.session = requests.Session()

    def publish_to_group(self, group: dict, ad: dict, text: str) -> PublicationResult:
        if self.dry_run:
            return PublicationResult("published", "Publicação simulada com sucesso")
        return PublicationResult(
            "error",
            error=(
                "Publicação automática em grupos não está disponível pela API oficial atual da Meta. "
                "Use Preparar publicação para concluir manualmente."
            ),
        )

    @staticmethod
    def prepare_manual(group: dict, ad: dict, text: str) -> dict:
        return {"group_reference": group["reference"], "text": text, "status": "prepared"}
# End legacy duplicate implementation.
