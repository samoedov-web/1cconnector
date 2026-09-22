"""Shared fixtures for CryptoVED tests."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
import uuid

from connector.models import Base, Network, Asset, Wallet, Invoice, ExpectedPayment, Transaction, Direction, TxStatus


@pytest.fixture
async def session():
    """Create in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def create_expected_payment(session):
    """Factory for creating ExpectedPayment records."""
    counter = 0
    async def _create(
        invoice_id=None,
        to_address="0xtest",
        network="ethereum", 
        amount=100,
        currency="USD",
        tolerance=0.01,
        expected_tx_hash=None,
        status="pending_aml"
    ):
        nonlocal counter
        counter += 1
        if invoice_id is None:
            invoice_id = counter
        ep = ExpectedPayment(
            invoice_id=invoice_id,
            to_address=to_address,
            network=network,
            amount=amount,
            currency=currency,
            tolerance=tolerance,
            expected_tx_hash=expected_tx_hash,
            status=status
        )
        session.add(ep)
        await session.commit()
        return ep
    return _create


@pytest.fixture
async def create_transaction(session):
    """Factory for creating Transaction records."""
    counter = 0
    async def _create(
        tx_hash=None,
        network_id=1,
        asset_id=1,
        wallet_id=1,
        amount=100,
        from_address="0xfrom",
        to_address="0xto",
        block_number=100,
        direction="in"
    ):
        nonlocal counter
        counter += 1
        if tx_hash is None:
            tx_hash = f"0xtx{counter:04d}"
        
        # Ensure network/asset/wallet exist
        net = await session.get(Network, network_id)
        if not net:
            session.add(Network(id=network_id, code="eth", name="Ethereum", finality_depth=12))
        asset = await session.get(Asset, asset_id)
        if not asset:
            session.add(Asset(id=asset_id, network_id=network_id, symbol="ETH", contract_address="0x", decimals=18))
        wallet = await session.get(Wallet, wallet_id)
        if not wallet:
            session.add(Wallet(id=wallet_id, network_id=network_id, address="0xwallet"))
        await session.flush()
        
        tx = Transaction(
            network_id=network_id,
            asset_id=asset_id,
            wallet_id=wallet_id,
            tx_hash=tx_hash,
            log_index=counter,  # Unique log_index per transaction
            block_number=block_number + counter,
            block_time=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),
            direction=Direction(direction),
            from_address=from_address,
            to_address=to_address,
            amount=amount,
            fee_amount=0,
            status=TxStatus.FINAL,
            confirmations=100,
            raw_response={},
            source="test"
        )
        session.add(tx)
        await session.commit()
        return tx
    return _create
