"""Bank of Russia registry provider foundation.

The transport layer is production-capable and read-only, while parsing is
kept behind an explicit parser boundary because the public machine-readable
format of the new digital-currency registries must be verified against the
actual CBR publication before production activation.

No private keys, signing or financial actions exist in this adapter.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

import httpx

from connector.adapters.stage3.adapters import RegistryResult
from connector.sources.registry import register_registry_source


class CBRRegistryType(StrEnum):
    DIGITAL_CURRENCY_EXCHANGE = "DIGITAL_CURRENCY_EXCHANGE"
    DIGITAL_DEPOSITORY = "DIGITAL_DEPOSITORY"
    INFORMATION_SYSTEM_OPERATOR = "INFORMATION_SYSTEM_OPERATOR"


@dataclass(frozen=True)
class CBRRegistryRequest:
    registry_type: CBRRegistryType
    subject_ref: str


@dataclass(frozen=True)
class CBRRawResponse:
    registry_type: CBRRegistryType
    source_url: str
    content: bytes
    content_type: str
    checked_at: datetime

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class CBRRegistryParser(Protocol):
    """Parser boundary for the actual CBR publication format."""

    def parse(
        self,
        request: CBRRegistryRequest,
        response: CBRRawResponse,
    ) -> RegistryResult:
        ...


class UnconfiguredCBRRegistryParser:
    """Fail-closed parser used until the publication schema is verified."""

    def parse(
        self,
        request: CBRRegistryRequest,
        response: CBRRawResponse,
    ) -> RegistryResult:
        raise RuntimeError(
            "CBR registry parser is not configured: "
            "verify the actual Bank of Russia registry publication format "
            "before enabling the production provider"
        )


class CBRRegistryTransport:
    """Read-only HTTP transport for Bank of Russia registry publications."""

    def __init__(
        self,
        base_url: str = "https://www.cbr.ru",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
            headers={
                "Accept": "application/json,text/csv,"
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet,text/html;q=0.5",
                "User-Agent": "1cconnector-registry-evidence/1.0",
            },
        )

    async def fetch(
        self,
        registry_type: CBRRegistryType,
        source_path: str,
    ) -> CBRRawResponse:
        if not source_path.startswith("/"):
            raise ValueError("CBR source_path must start with '/'")

        response = await self._client.get(source_path)
        response.raise_for_status()

        return CBRRawResponse(
            registry_type=registry_type,
            source_url=str(response.url),
            content=response.content,
            content_type=response.headers.get("content-type", ""),
            checked_at=datetime.now(timezone.utc),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class CBRRegistryAdapter:
    """Normalize official CBR registry evidence through a verified parser."""

    source_name = "cbr"

    def __init__(
        self,
        *,
        registry_type: CBRRegistryType,
        source_path: str,
        parser: CBRRegistryParser | None = None,
        base_url: str = "https://www.cbr.ru",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.registry_type = registry_type
        self.source_path = source_path
        self.parser = parser or UnconfiguredCBRRegistryParser()
        self.transport = CBRRegistryTransport(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_config(cls, **config) -> "CBRRegistryAdapter":
        registry_type = config.get("registry_type")
        source_path = config.get("source_path")

        if not registry_type:
            raise ValueError("CBR registry_type is required")
        if not source_path:
            raise ValueError("CBR source_path is required")

        return cls(
            registry_type=CBRRegistryType(registry_type),
            source_path=source_path,
            parser=config.get("parser"),
            base_url=config.get("base_url") or "https://www.cbr.ru",
            timeout_seconds=float(config.get("timeout_seconds", 15)),
            transport=config.get("transport"),
        )

    async def check(self, subject_ref: str) -> RegistryResult:
        request = CBRRegistryRequest(
            registry_type=self.registry_type,
            subject_ref=subject_ref,
        )

        raw = await self.transport.fetch(
            self.registry_type,
            self.source_path,
        )

        return self.parser.parse(request, raw)

    async def aclose(self) -> None:
        await self.transport.aclose()

CBR_OIS_SOURCE_PATH = (
    "/vfs/finmarkets/files/supervision/list_ois.xlsx"
)


@register_registry_source("cbr-ois")
class CBROISRegistryAdapter(CBRRegistryAdapter):
    """Production provider for the official Bank of Russia OIS registry."""

    source_name = "cbr-ois"

    def __init__(
        self,
        *,
        base_url: str = "https://www.cbr.ru",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Local import avoids making the generic transport module depend
        # on a concrete parser at import time.
        from connector.adapters.stage3.cbr_parser import CBROISXlsxParser

        super().__init__(
            registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
            source_path=CBR_OIS_SOURCE_PATH,
            parser=CBROISXlsxParser(),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_config(cls, **config) -> "CBROISRegistryAdapter":
        return cls(
            base_url=config.get("base_url") or "https://www.cbr.ru",
            timeout_seconds=float(config.get("timeout_seconds", 15)),
            transport=config.get("transport"),
        )

