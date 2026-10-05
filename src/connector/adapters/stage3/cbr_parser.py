"""Verified-parser boundary helpers for CBR registry evidence.

The JSON schema implemented here is an internal normalized interchange
format used for frozen fixtures and controlled imports. It is NOT asserted
to be the native publication format of the Bank of Russia.

A native CBR parser must be added only after the real publication format
has been captured and verified.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from connector.adapters.stage3.adapters import RegistryResult
from connector.adapters.stage3.cbr_registry import (
    CBRRawResponse,
    CBRRegistryRequest,
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NormalizedCBRFixtureParser:
    """Parser for the connector's frozen normalized fixture format."""

    def parse(
        self,
        request: CBRRegistryRequest,
        response: CBRRawResponse,
    ) -> RegistryResult:
        try:
            payload: dict[str, Any] = json.loads(response.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("CBR fixture is not valid JSON") from exc

        subject_ref = str(payload.get("subject_ref") or "")
        if not subject_ref:
            raise ValueError("CBR fixture missing subject_ref")

        if subject_ref != request.subject_ref:
            raise ValueError(
                "CBR fixture subject_ref does not match requested subject"
            )

        registry_type = str(payload.get("registry_type") or "")
        if registry_type != request.registry_type.value:
            raise ValueError(
                "CBR fixture registry_type does not match requested registry"
            )

        status = str(payload.get("status") or "")
        if not status:
            raise ValueError("CBR fixture missing status")

        entry_id = payload.get("registry_entry_id")
        admission_mode = payload.get("admission_mode")

        return RegistryResult(
            subject_ref=subject_ref,
            status=status,
            registry_type=registry_type,
            record_number=str(entry_id) if entry_id else None,
            registry_entry_id=str(entry_id) if entry_id else None,
            admission_mode=(
                str(admission_mode) if admission_mode else None
            ),
            valid_from=_parse_dt(payload.get("valid_from")),
            valid_to=_parse_dt(payload.get("valid_to")),
            application_id=(
                str(payload["application_id"])
                if payload.get("application_id")
                else None
            ),
            admission_date=_parse_dt(payload.get("admission_date")),
            legal_basis=(
                str(payload["legal_basis"])
                if payload.get("legal_basis")
                else None
            ),
            checked_at=response.checked_at,
            raw_payload=payload,
            checksum=response.checksum,
            source="CBR",
            source_url=response.source_url,
        )
