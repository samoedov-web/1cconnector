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
from datetime import datetime, timedelta

from sqlalchemy import select

from connector.config import settings
from connector.db import SessionFactory, engine
from connector.indexer.base import ChainAdapter
# Импорт модулей адаптеров регистрирует их классы в реестре источников.
import connector.indexer.ethereum  # noqa: F401
from connector.indexer.service import IndexerService, merge_sources
import connector.indexer.tron  # noqa: F401
from connector import license as license_module
from connector.alerts import send_alert
from connector.models import Asset, Base, Network, Wallet, utcnow
from connector.pipeline import TransactionPipeline
from connector.rates.service import RateService
from connector.rates.sources import (
    CbrRateSource,
    CoinGeckoSource,
    CompositeRateSource,
    FallbackRateSource,
    StaticPegSource,
)
from connector.seed import seed_defaults
from connector.sources.registry import create_chain_source

log = logging.getLogger("connector.indexer")


def build_adapters() -> dict[str, list[ChainAdapter]]:
    """Собрать источники данных из окружения (минимум два на сеть — п. 3 ТЗ).

    Классы адаптеров берутся из реестра источников (фаза 1 depository-спеки);
    схема переменных окружения и имена источников не изменены.
    """
    env_prefixes = {"tron": ("TRON_SOURCE_1", "TRON_SOURCE_2"),
                    "ethereum": ("ETH_SOURCE_1", "ETH_SOURCE_2")}
    short = {"tron": "tron", "ethereum": "eth"}
    adapters: dict[str, list[ChainAdapter]] = {}
    for code, prefixes in env_prefixes.items():
        for i, prefix in enumerate(prefixes, start=1):
            url = os.environ.get(f"{prefix}_URL")
            if not url:
                continue
            adapters.setdefault(code, []).append(
                create_chain_source(
                    code,
                    url,
                    api_key=os.environ.get(f"{prefix}_API_KEY", ""),
                    source_name=f"{short[code]}-{i}",
                )
            )
    return adapters


def scan_window(
    backfill_from: datetime | None,
    last_scanned_block: int | None,
    last_scanned_at: datetime | None,
    finality_depth: int,
) -> tuple[int | None, datetime | None]:
    """(from_block, since) для следующего опроса кошелька.

    Первый опрос — полный бэкфилл с backfill_from; дальше — от курсора
    с запасом на реорг (2 × порог финальности по блокам, 1 час по времени),
    чтобы не перечитывать всю историю каждый цикл. Дедупликация в ingest
    делает перекрытие окон безопасным.
    """
    if last_scanned_block is None and last_scanned_at is None:
        return None, backfill_from
    from_block = (
        max(0, last_scanned_block - finality_depth * 2)
        if last_scanned_block is not None
        else None
    )
    since = last_scanned_at - timedelta(hours=1) if last_scanned_at is not None else None
    return from_block, since


def build_rate_service() -> RateService:
    """Курсы: стейблкоины — привязка (учётная политика), нативные монеты
    (комиссии TRX/ETH) — CoinGecko, нога в рубли — ЦБ РФ.

    Биржевой источник площадки из договора клиента подключается первым
    звеном FallbackRateSource при внедрении.
    """
    return RateService(
        primary=CompositeRateSource(
            asset_source=FallbackRateSource(StaticPegSource(), CoinGeckoSource()),
            rub_source=CbrRateSource(),
        )
    )


async def poll_network(
    network: Network, sources: list[ChainAdapter], rate_service: RateService
) -> None:
    heads = await asyncio.gather(*(s.latest_block() for s in sources), return_exceptions=True)
    heads_ok = [h for h in heads if isinstance(h, int)]
    if not heads_ok:
        log.error("Сеть %s: все источники недоступны", network.code)
        async with SessionFactory() as session:
            await send_alert(
                session, "error", f"Сеть {network.code}: все источники данных недоступны",
                {"sources": [s.source_name for s in sources]},
            )
            await session.commit()
        return
    if settings.cross_check_sources and len(heads_ok) >= 2 and max(heads_ok) - min(heads_ok) > 5:
        log.warning(
            "Сеть %s: расхождение источников по высоте блока %s — финальность не продвигается",
            network.code,
            heads_ok,
        )
        async with SessionFactory() as session:
            await send_alert(
                session, "warning",
                f"Сеть {network.code}: расхождение источников по высоте блока",
                {"heads": heads_ok},
            )
            await session.commit()
        return
    latest_block = min(heads_ok)  # консервативно: по отстающему источнику

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
            from_block, since = scan_window(
                wallet.backfill_from,
                wallet.last_scanned_block,
                wallet.last_scanned_at,
                network.finality_depth,
            )
            # Кросс-проверка наборов: опрашиваются все источники, берётся
            # объединение; транзакция, видимая минимум двум, — cross_checked.
            per_source = await asyncio.gather(
                *(
                    s.fetch_transfers(wallet.address, tokens, since=since, from_block=from_block)
                    for s in sources
                ),
                return_exceptions=True,
            )
            fetched = [r for r in per_source if isinstance(r, list)]
            if not fetched:
                log.error(
                    "Сеть %s, кошелёк %s: ни один источник не отдал данные",
                    network.code,
                    wallet.address,
                )
                continue
            if len(fetched) >= 2:
                sets = [{(t.tx_hash, t.log_index) for t in lst} for lst in fetched]
                if any(s != sets[0] for s in sets[1:]):
                    log.warning(
                        "Сеть %s, кошелёк %s: расхождение наборов транзакций между "
                        "источниками (%s) — взято объединение",
                        network.code,
                        wallet.address,
                        [len(s) for s in sets],
                    )
                    await send_alert(
                        session, "warning",
                        f"Сеть {network.code}: источники отдали разные наборы транзакций",
                        {"wallet": wallet.address, "counts": [len(s) for s in sets]},
                    )
            merged = merge_sources(fetched)
            created = await service.ingest_transfers(wallet, network, merged, latest_block)
            if created:
                log.info(
                    "Сеть %s, кошелёк %s: новых транзакций %d",
                    network.code,
                    wallet.address,
                    len(created),
                )
                # Комиссии исходящих — отдельная статья расходов (п. 6 ТЗ).
                await service.enrich_fees(created, sources[0])
            # Курсор двигается, только если ответили все источники: иначе
            # транзакции из недоступного источника выпадут из окна навсегда.
            if len(fetched) == len(sources):
                wallet.last_scanned_block = latest_block
                wallet.last_scanned_at = utcnow()

        reorged = await service.check_reorgs(network, sources[0])
        if reorged:
            log.warning(
                "Сеть %s: реорг затронул транзакций %d (orphaned/переехали)",
                network.code,
                len(reorged),
            )
            await send_alert(
                session, "warning",
                f"Сеть {network.code}: реорг затронул {len(reorged)} транзакций",
                {"hashes": [t.tx_hash for t in reorged][:20]},
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
    if settings.seed_defaults:
        async with SessionFactory() as session:
            await seed_defaults(session)
            await session.commit()
    adapters = build_adapters()
    if not adapters:
        log.error("Не настроен ни один источник данных (TRON_SOURCE_*/ETH_SOURCE_*)")
        return
    rate_service = build_rate_service()
    last_license_status: str | None = None
    while True:
        # Лицензия: grace и демо синхронизируют, expired/invalid — только чтение.
        license_state = license_module.current_state()
        if license_state.status != last_license_status:
            if not license_state.sync_allowed:
                log.error(
                    "Лицензия: %s — синхронизация остановлена, чтение доступно",
                    license_state.reason or license_state.status,
                )
                async with SessionFactory() as session:
                    await send_alert(
                        session, "error",
                        "Лицензия не действует — синхронизация остановлена",
                        {"status": license_state.status, "reason": license_state.reason},
                    )
                    await session.commit()
            elif license_state.status == "grace":
                log.warning(
                    "Лицензия истекла %s, льготный период до %s — продлите лицензию",
                    license_state.valid_until,
                    license_state.grace_until,
                )
            last_license_status = license_state.status
        if not license_state.sync_allowed:
            await asyncio.sleep(settings.indexer_poll_interval)
            continue

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
