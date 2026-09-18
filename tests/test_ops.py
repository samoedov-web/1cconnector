"""Тесты эксплуатационных блоков: сид справочников, алерты, комиссии,
цепочка источников котировок."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.alerts import send_alert
from connector.indexer.service import IndexerService, merge_sources
from connector.models import Alert, Asset, Base, Direction, Network, Wallet
from connector.rates.base import Quote, RateSource
from connector.rates.sources import FallbackRateSource, StaticPegSource
from connector.seed import seed_defaults

from tests.test_indexer import transfer


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# --- Сид справочников --------------------------------------------------------


async def test_seed_fills_empty_directories(session):
    assert await seed_defaults(session) is True
    networks = {n.code: n for n in (await session.execute(select(Network))).scalars()}
    assert set(networks) == {"tron", "ethereum"}
    assert networks["tron"].finality_depth == 19
    assets = (await session.execute(select(Asset))).scalars().all()
    assert {(a.symbol, a.network_id) for a in assets} == {
        ("USDT", networks["tron"].id),
        ("USDC", networks["tron"].id),
        ("USDT", networks["ethereum"].id),
        ("USDC", networks["ethereum"].id),
    }


async def test_seed_never_touches_configured_base(session):
    session.add(Network(code="tron", name="Кастомная", finality_depth=50))
    await session.flush()
    assert await seed_defaults(session) is False
    networks = (await session.execute(select(Network))).scalars().all()
    assert len(networks) == 1
    assert networks[0].finality_depth == 50  # настройка клиента не тронута


# --- Алерты -------------------------------------------------------------------


async def test_alert_written_without_webhook(session):
    alert = await send_alert(session, "warning", "Тест", {"x": 1}, webhook_url="")
    assert alert.sent is False
    stored = await session.scalar(select(Alert))
    assert stored.title == "Тест"
    assert stored.details == {"x": 1}


async def test_alert_survives_dead_webhook(session):
    # Недоступный webhook не роняет вызов — алерт остаётся в БД.
    alert = await send_alert(
        session, "error", "Webhook мёртв", {}, webhook_url="http://127.0.0.1:1/dead"
    )
    assert alert.sent is False


# --- Комиссии ------------------------------------------------------------------


class FeeAdapter:
    def __init__(self, fees: dict[str, Decimal]) -> None:
        self.fees = fees

    async def get_transaction_fee(self, tx_hash: str):
        if tx_hash not in self.fees:
            raise RuntimeError("нет данных")
        return self.fees[tx_hash], "TRX"


async def test_enrich_fees_only_outgoing_and_error_tolerant(session):
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    session.add_all([asset, wallet])
    await session.flush()

    service = IndexerService(session)
    incoming = transfer("in1")
    outgoing = transfer("out1")
    object.__setattr__(outgoing, "from_address", "TMyWallet")
    object.__setattr__(outgoing, "to_address", "TPartner")
    failing = transfer("out2")
    object.__setattr__(failing, "from_address", "TMyWallet")
    object.__setattr__(failing, "to_address", "TPartner")

    created = await service.ingest_transfers(
        wallet, network, merge_sources([[incoming, outgoing, failing]]), latest_block=200
    )
    adapter = FeeAdapter({"out1": Decimal("13.5")})  # out2 упадёт
    enriched = await service.enrich_fees(created, adapter)

    assert enriched == 1
    by_hash = {t.tx_hash: t for t in created}
    assert by_hash["out1"].fee_amount == Decimal("13.5")
    assert by_hash["out1"].fee_asset == "TRX"
    assert by_hash["in1"].fee_amount == 0  # входящие не трогаем
    assert by_hash["out2"].fee_amount == 0  # сбой источника не уронил цикл
    assert by_hash["out1"].direction == Direction.OUT


# --- Цепочка источников котировок ----------------------------------------------


class NativeCoinSource(RateSource):
    source_name = "native"

    async def get_quote(self, base, quote, as_of):
        if base.upper() != "TRX":
            raise LookupError("только TRX")
        return Quote("TRX", quote.upper(), Decimal("0.12"), as_of, self.source_name, {})


async def test_fallback_chain_tries_sources_in_order():
    chain = FallbackRateSource(StaticPegSource(), NativeCoinSource())
    as_of = datetime(2026, 7, 1, tzinfo=timezone.utc)

    peg = await chain.get_quote("USDT", "USD", as_of)
    assert peg.source == "static-peg"  # первый источник знает пару

    native = await chain.get_quote("TRX", "USD", as_of)
    assert native.source == "native"  # второй подхватил неизвестную первому

    with pytest.raises(LookupError):
        await chain.get_quote("DOGE", "USD", as_of)
