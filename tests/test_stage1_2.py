import pytest
from connector.models import Operation, OperationState, RegulatoryVersion, CustodyStatement

@pytest.mark.asyncio
async def test_operation_creation(db_session):
    op = Operation(invoice_id=1, counterparty_id=1, asset_id=1, network_id=1, wallet_address="0x123")
    db_session.add(op)
    await db_session.flush()
    assert op.state == OperationState.DRAFT

@pytest.mark.asyncio
async def test_regulatory_version_unique_constraint(db_session):
    rv1 = RegulatoryVersion(code="RV1", source_document="doc", effective_from="2024-01-01", checksum="abc")
    rv2 = RegulatoryVersion(code="RV1", source_document="doc2", effective_from="2024-01-01", checksum="def")
    db_session.add(rv1)
    await db_session.flush()
    db_session.add(rv2)
    with pytest.raises(Exception): # Unique constraint violation
        await db_session.flush()
