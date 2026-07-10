"""MockDepositoryAdapter (п. 4.4 спеки): выписки из файлов-фикстур.

Единственная реализация DepositoryAdapter до появления реальных API.
Читает фикстуры CSV и JSON (схемы — docs/custody-fixtures.md; генератор —
scripts/custody_fixture_gen.py), эмулирует задержку и деградацию health
для тестов устойчивости.

capabilities() выводятся из загруженных данных (бедная выписка без хэшей
корректно объявит has_tx_hash=False) либо задаются явно в конфигурации.
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from connector.custody.base import Capabilities, DepositoryAdapter, EntryGranularity
from connector.custody.store import EntryPayload, StatementPayload
from connector.models import CustodyOperationType
from connector.sources.base import HealthStatus
from connector.sources.registry import register_custody_source


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _entry_from_dict(raw: dict) -> EntryPayload:
    return EntryPayload(
        entry_id=str(raw["entry_id"]),
        occurred_at=_parse_dt(raw["occurred_at"]),
        asset=raw["asset"],
        amount=Decimal(str(raw["amount"])),
        operation_type=CustodyOperationType(raw["operation_type"]),
        network=raw.get("network") or None,
        counterparty_ref=raw.get("counterparty_ref") or None,
        external_tx_hash=raw.get("external_tx_hash") or None,
        raw_line=raw,
    )


@register_custody_source("mock-depo")
class MockDepositoryAdapter(DepositoryAdapter):
    display_name = "Мок-депозитарий (фикстуры)"
    source_name = "mock-depo"

    def __init__(
        self,
        fixtures_dir: str,
        capabilities: Capabilities | None = None,
        latency_seconds: float = 0.0,
        health_override: str = "",  # "" | degraded | down — эмуляция сбоев
    ) -> None:
        self._dir = Path(fixtures_dir)
        self._explicit_capabilities = capabilities
        self._latency = latency_seconds
        self._health_override = health_override
        self._loaded: dict[str, tuple[StatementPayload, list[EntryPayload], str]] | None = None

    @classmethod
    def from_config(cls, **config) -> "MockDepositoryAdapter":
        return cls(
            fixtures_dir=config["fixtures_dir"],
            capabilities=config.get("capabilities"),
            latency_seconds=float(config.get("latency_seconds", 0)),
            health_override=config.get("health_override", ""),
        )

    # --- Загрузка фикстур -------------------------------------------------

    def _load(self) -> dict[str, tuple[StatementPayload, list[EntryPayload], str]]:
        if self._loaded is not None:
            return self._loaded
        loaded: dict[str, tuple[StatementPayload, list[EntryPayload], str]] = {}
        origins: dict[str, str] = {}

        def add(statement, entries, granularity, origin: str) -> None:
            sid = statement.statement_id
            if sid in loaded:
                raise ValueError(
                    f"Дубль statement_id «{sid}» в фикстурах: {origins[sid]} и "
                    f"{origin} — выписки должны иметь уникальные id"
                )
            loaded[sid] = (statement, entries, granularity)
            origins[sid] = origin

        for path in sorted(self._dir.glob("*.json")):
            add(*self._parse_json(path), origin=path.name)
        for path in sorted(self._dir.glob("*.csv")):
            for statement, entries, granularity in self._parse_csv(path):
                add(statement, entries, granularity, origin=path.name)
        self._loaded = loaded
        return loaded

    def _parse_json(self, path: Path):
        document = json.loads(path.read_text(encoding="utf-8"))
        statement = StatementPayload(
            statement_id=document["statement_id"],
            source_id=self.source_name,
            period_from=_parse_dt(document["period_from"]),
            period_to=_parse_dt(document["period_to"]),
            issued_at=_parse_dt(document.get("issued_at")),
            raw_payload=document,
        )
        entries = [_entry_from_dict(raw) for raw in document.get("entries", [])]
        return statement, entries, document.get("granularity", "per_tx")

    def _parse_csv(self, path: Path):
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        by_statement: dict[str, list[dict]] = {}
        for row in rows:
            by_statement.setdefault(row["statement_id"], []).append(row)
        for statement_id, statement_rows in by_statement.items():
            head = statement_rows[0]
            statement = StatementPayload(
                statement_id=statement_id,
                source_id=self.source_name,
                period_from=_parse_dt(head["period_from"]),
                period_to=_parse_dt(head["period_to"]),
                issued_at=_parse_dt(head.get("issued_at")),
                raw_payload={"format": "csv", "file": path.name, "rows": statement_rows},
            )
            entries = [_entry_from_dict(row) for row in statement_rows]
            yield statement, entries, head.get("granularity") or "per_tx"

    # --- Контракт DepositoryAdapter ----------------------------------------

    def capabilities(self) -> Capabilities:
        if self._explicit_capabilities is not None:
            return self._explicit_capabilities
        loaded = self._load()
        entries = [e for _, batch, _ in loaded.values() for e in batch]
        granularities = {gran for _, _, gran in loaded.values()} or {"per_tx"}
        granularity = (
            EntryGranularity(granularities.pop())
            if len(granularities) == 1
            else EntryGranularity.MIXED
        )
        return Capabilities(
            has_tx_hash=any(e.external_tx_hash for e in entries),
            has_counterparty=any(e.counterparty_ref for e in entries),
            has_network=any(e.network for e in entries),
            entry_granularity=granularity,
        )

    async def fetch_statements(
        self, period_from: datetime, period_to: datetime
    ) -> list[StatementPayload]:
        await self._simulate_conditions()
        return [
            statement
            for statement, _, _ in self._load().values()
            if statement.period_from <= period_to and statement.period_to >= period_from
        ]

    async def fetch_entries(self, statement_id: str) -> list[EntryPayload]:
        await self._simulate_conditions()
        try:
            _, entries, _ = self._load()[statement_id]
        except KeyError:
            raise LookupError(f"Выписка {statement_id} не найдена у депозитария") from None
        return list(entries)

    async def health(self) -> HealthStatus:
        if self._health_override:
            return HealthStatus(self._health_override, "эмуляция сбоя (конфигурация мока)")
        try:
            count = len(self._load())
        except Exception as exc:  # noqa: BLE001 — health не бросает
            return HealthStatus("down", f"{type(exc).__name__}: {exc}")
        return HealthStatus("ok", f"statements={count}")

    async def _simulate_conditions(self) -> None:
        if self._health_override == "down":
            raise ConnectionError("Мок-депозитарий недоступен (эмуляция)")
        if self._latency:
            await asyncio.sleep(self._latency)
