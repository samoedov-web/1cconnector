import pytest
from decimal import Decimal
from datetime import datetime, timezone
from connector.models import OnecDocType, OnecDocStatus, Lot
from connector.onec.exchange import OneCExchangeService
from connector.reports.tax_registry import TaxRegistryService, TaxLot

@pytest.mark.asyncio
async def test_create_receipt_doc(async_session, sample_transaction):
    service = OneCExchangeService(async_session)
    payload = {"counterparty": "Test LLC", "contract": "123"}
    
    doc = await service.create_receipt_doc(sample_transaction, payload)
    
    assert doc.doc_type == OnecDocType.RECEIPT
    assert doc.status == OnecDocStatus.DRAFT
    assert doc.idempotency_key is not None

@pytest.mark.asyncio
async def test_fifo_calculation(async_session, sample_asset):
    registry = TaxRegistryService(async_session)
    
    # Создаем две партии: старая (дешевле) и новая (дороже)
    lot1 = Lot(asset_id=sample_asset.id, quantity=Decimal(100), remaining=Decimal(100), 
               unit_cost_rub=Decimal(50), acquired_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    lot2 = Lot(asset_id=sample_asset.id, quantity=Decimal(100), remaining=Decimal(100), 
               unit_cost_rub=Decimal(60), acquired_at=datetime(2025, 2, 1, tzinfo=timezone.utc))
    
    async_session.add_all([lot1, lot2])
    await async_session.flush()
    
    lots = await registry.get_open_lots(sample_asset.id)
    
    assert len(lots) == 2
    assert lots[0].unit_cost_rub < lots[1].unit_cost_rub  # Старая дешевле (или равна)
    assert lots[0].acquired_at < lots[1].acquired_at

def test_tax_disposal_profit():
    disposal = TaxDisposal(
        asset_symbol="USDT",
        quantity=Decimal(10),
        income_rub=Decimal(1000),
        cost_rub=Decimal(800),
        profit_rub=Decimal(200),
        disposal_date=datetime.now(timezone.utc)
    )
    assert disposal.profit_rub == Decimal(200)
    assert disposal.income_rub > disposal.cost_rub
