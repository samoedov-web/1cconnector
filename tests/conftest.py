"""Shared fixtures for canonical Stage tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    Direction,
    Invoice,
    Network,
    Operation,
    Transaction,
    TxStatus,
    Wallet,
)


@pytest_asyncio.fixture
async def async_session():
    """Fresh in-memory SQLite database for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_session):
    """Backward-compatible alias used by Stage 1/2 tests."""
    yield async_session


@pytest_asyncio.fixture
async def sample_asset(async_session):
    network = Network(
        code="testnet",
        name="Test Network",
        finality_depth=1,
        enabled=True,
    )
    async_session.add(network)
    await async_session.flush()

    asset = Asset(
        network_id=network.id,
        symbol="TST",
        contract_address="0xTESTASSET",
        decimals=18,
    )
    async_session.add(asset)
    await async_session.flush()

    return asset


@pytest_asyncio.fixture
async def sample_invoice(async_session):
    counterparty = Counterparty(
        name="Test Counterparty",
        onec_ref="TEST-CP",
    )
    async_session.add(counterparty)
    await async_session.flush()

    contract = Contract(
        counterparty_id=counterparty.id,
        number="TEST-CONTRACT",
        registration_number="TEST-REG",
        currency="USD",
        kvvo="",
        onec_ref="TEST-CONTRACT-REF",
    )
    async_session.add(contract)
    await async_session.flush()

    invoice = Invoice(
        contract_id=contract.id,
        number="TEST-INVOICE",
        amount=Decimal("100"),
        currency="USD",
        paid_amount=Decimal("0"),
        onec_ref="TEST-INVOICE-REF",
        crypto_address="0xTESTRECIPIENT",
    )
    async_session.add(invoice)
    await async_session.flush()

    return invoice


@pytest_asyncio.fixture
async def sample_transaction(async_session, sample_asset):
    wallet = Wallet(
        network_id=sample_asset.network_id,
        address="0xTESTWALLET",
        label="Test Wallet",
        enabled=True,
    )
    async_session.add(wallet)
    await async_session.flush()

    tx = Transaction(
        network_id=sample_asset.network_id,
        asset_id=sample_asset.id,
        wallet_id=wallet.id,
        tx_hash="0xTESTTX",
        log_index=0,
        block_number=100,
        block_time=datetime.now(timezone.utc),
        direction=Direction.IN,
        from_address="0xFROM",
        to_address="0xTESTWALLET",
        amount=Decimal("100"),
        fee_amount=Decimal("0"),
        fee_asset="TST",
        status=TxStatus.FINAL,
        confirmations=10,
        finalized_at=datetime.now(timezone.utc),
        raw_response={"test": True},
        source="test",
        cross_checked=True,
    )
    async_session.add(tx)
    await async_session.flush()

    return tx


@pytest_asyncio.fixture
async def sample_operation(async_session, sample_invoice, sample_asset):
    contract = await async_session.get(
        Contract,
        sample_invoice.contract_id,
    )

    operation = Operation(
        invoice_id=sample_invoice.id,
        counterparty_id=contract.counterparty_id,
        asset_id=sample_asset.id,
        network_id=sample_asset.network_id,
        wallet_address="0xTESTWALLET",
    )
    async_session.add(operation)
    await async_session.flush()

    return operation
