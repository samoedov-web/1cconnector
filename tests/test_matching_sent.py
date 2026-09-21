"""Tests for Matching A+B and SENT semantics."""

import pytest
from sqlalchemy import select
from connector.models import ExpectedPayment, Match, MatchState


@pytest.mark.asyncio
async def test_expected_tx_hash_field_exists(session):
    """Verify expected_tx_hash column exists in ExpectedPayment model."""
    ep = ExpectedPayment(
        invoice_id=1,
        to_address="0xtest",
        network="ethereum",
        amount=100,
        currency="USD",
        tolerance=0.01,
        expected_tx_hash="0xabc123"
    )
    session.add(ep)
    await session.commit()
    
    # Verify it was stored
    result = await session.execute(select(ExpectedPayment).where(ExpectedPayment.expected_tx_hash == "0xabc123"))
    found = result.scalar_one()
    assert found is not None
    assert found.expected_tx_hash == "0xabc123"


@pytest.mark.asyncio
async def test_deterministic_match_exact_hash(session, create_transaction, create_expected_payment):
    """Path A: Exact tx_hash match should auto-match."""
    from connector.matching.engine import MatchingService
    
    expected_tx_hash = "0xdeadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678"
    
    ep = await create_expected_payment(expected_tx_hash=expected_tx_hash)
    tx = await create_transaction(tx_hash=expected_tx_hash)
    
    matching = MatchingService(session)
    result = await matching.attempt_match(tx)
    
    assert result is True
    assert ep.status.value == "matched"
    assert ep.transaction_id == tx.id


@pytest.mark.asyncio
async def test_no_match_unknown_hash(session, create_transaction, create_expected_payment):
    """Path B: Unknown hash should NOT auto-match."""
    from connector.matching.engine import MatchingService
    
    ep = await create_expected_payment(expected_tx_hash=None)
    tx = await create_transaction(tx_hash="0xrandom")
    
    matching = MatchingService(session)
    result = await matching.attempt_match(tx)
    
    assert result is False
    # Status should remain pending or unchanged, NOT matched
    assert ep.status.value != "matched"


@pytest.mark.asyncio
async def test_no_match_different_hash(session, create_transaction, create_expected_payment):
    """Hash mismatch should NOT auto-match."""
    from connector.matching.engine import MatchingService
    
    ep = await create_expected_payment(expected_tx_hash="0xexpected")
    tx = await create_transaction(tx_hash="0xdifferent")
    
    matching = MatchingService(session)
    result = await matching.attempt_match(tx)
    
    assert result is False
    assert ep.status.value != "matched"


@pytest.mark.asyncio  
async def test_read_only_no_broadcast_in_matching():
    """Matching process must NOT call any broadcast/sign methods."""
    from connector.matching.engine import MatchingService
    
    # This test verifies by inspection that MatchingService has no imports
    # of signing/broadcast modules
    import inspect
    source = inspect.getsource(MatchingService)
    
    forbidden = ["sign", "broadcast", "send_raw", "private_key", "nonce_manager"]
    for word in forbidden:
        assert word not in source.lower(), f"Forbidden word '{word}' found in MatchingService"
