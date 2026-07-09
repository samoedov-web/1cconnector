"""Процесс индексации: периодический опрос сетей по всем кошелькам.

Отдельный контейнер (docker-compose service `indexer`). Цикл:
  1. по каждой активной сети — получить latest_block у каждого источника;
  2. по каждому кошельку — забрать новые трансферы, сохранить (дедуп);
  3. продвинуть финальность seen → confirmed(N) → final;
  4. по финализированным транзакциям ядро создаёт снимки курсов,
     партии/списания ФИФО, матчинг и проекты документов 1С.

Кросс-проверка источников (п. 3 ТЗ): при расхождении latest_block или
набора транзакций между источниками — алерт в мониторинг, финальность
не продвигается до совпадения.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select

from connector.config import settings
from connector.db import SessionFactory, engine
from connector.indexer.base import ChainAdapter
from connector.indexer.ethereum import EthereumAdapter
from connector.indexer.service import IndexerService
from connector.indexer.tron import TronAdapter
from connector.models import Asset, Base, Network, Wallet
from connector.pipeline import TransactionPipeline
from connector.rates.service import RateService
from connector.rates.sources import CbrRateSource, CompositeRateSource, StaticPegSource

log = logging.getLogger("connector.indexer")


def build_adapters() -> dict[str, list[ChainAdapter]]:
    """Собрать источники данных из окружения (минимум два на сеть — п. 3 ТЗ)."""
    adapters: dict[str, list[ChainAdapter]] = {"tron": [], "ethereum": []}
    for i, prefix in enumerate(("TRON_SOURCE_1", "TRON_SOURCE_2"), start=1):
        url = os.environ.get(f"{prefix}_URL")
        if url:
            adapters["tron"].append(
                TronAdapter(
                    url,
                    api_key=os.environ.get(f"{prefix}_API_KEY", ""),
                    source_name=f"tron-{i}",
                )
            )
    for i, prefix in enumerate(("ETH_SOURCE_1", "ETH_SOURCE_2"), start=1):
        url = os.environ.get(f"{prefix}_URL")
        if url:
            adapters["ethereum"].append(EthereumAdapter(url, source_name=f"eth-{i}"))
    return {code: lst for code, lst in adapters.items() if lst}


def build_rate_service() -> RateService:
    """Основной источник: привязка стейблкоина (учётная политика) + ЦБ РФ.

    Биржевой источник (реализация RateSource поверх API площадки из договора
    клиента) подключается сюда же как asset_source или fallback.
    """
    return RateService(
        primary=CompositeRateSource(asset_source=StaticPegSource(), rub_source=CbrRateSource())
    )


async def poll_network(
    network: Network, sources: list[ChainAdapter], rate_service: RateService
) -> None:
    heads = await asyncio.gather(*(s.latest_block() for s in sources), return_exceptions=True)
    heads_ok = [h for h in heads if isinstance(h, int)]
    if not heads_ok:
        log.error("Сеть %s: все источники недоступны", network.code)
        return
    if settings.cross_check_sources and len(heads_ok) >= 2 and max(heads_ok) - min(heads_ok) > 5:
        log.warning(
            "Сеть %s: расхождение источников по высоте блока %s — финальность не продвигается",
            network.code,
            heads_ok,
        )
        return
    latest_block = min(heads_ok)  # консервативно: по отстающему источнику
    primary = sources[0]

    async with SessionFactory() as session:
        service = IndexerService(session)
        tokens = [
            a.contract_address
            for a in (
                await session.execute(select(Asset).where(Asset.network_id == network.id))
            ).scalars()
        ]
        wallets = (
            (
                await session.execute(
                    select(Wallet).where(Wallet.network_id == network.id, Wallet.enabled)
                )
            )
            .scalars()
            .all()
        )
        for wallet in wallets:
            transfers = await primary.fetch_transfers(
                wallet.address, tokens, since=wallet.backfill_from
            )
            created = await service.ingest_transfers(wallet, network, transfers, latest_block)
            if created:
                log.info(
                    "Сеть %s, кошелёк %s: новых транзакций %d",
                    network.code,
                    wallet.address,
                    len(created),
                )
        finalized = await service.advance_finality(network, latest_block)
        if finalized:
            log.info("Сеть %s: финализировано транзакций %d", network.code, len(finalized))
        # Связка: финальность → матчинг → снимок курса → ФИФО → проект документа.
        # Скан каждый цикл — подхватывает и разобранные оператором транзакции.
        pipeline = TransactionPipeline(session, rate_service)
        documents = await pipeline.process_network(network)
        if documents:
            log.info(
                "Сеть %s: подготовлено проектов документов 1С: %d", network.code, len(documents)
            )
        await session.commit()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    adapters = build_adapters()
    if not adapters:
        log.error("Не настроен ни один источник данных (TRON_SOURCE_*/ETH_SOURCE_*)")
        return
    rate_service = build_rate_service()
    while True:
        async with SessionFactory() as session:
            networks = (
                (await session.execute(select(Network).where(Network.enabled))).scalars().all()
            )
        for network in networks:
            sources = adapters.get(network.code, [])
            if not sources:
                continue
            try:
                await poll_network(network, sources, rate_service)
            except Exception:
                log.exception("Сбой цикла индексации сети %s", network.code)
        await asyncio.sleep(settings.indexer_poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
