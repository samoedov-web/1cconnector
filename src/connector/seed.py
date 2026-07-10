"""Начальное заполнение справочников сетей и активов.

Без него база после первого `docker compose up` пуста: индексеру нечего
опрашивать, а добавить сеть можно только SQL-ом. Сид выполняется при старте
api и воркера, только если таблицы пусты (существующие настройки клиента
никогда не перезаписываются). Отключается CONNECTOR_SEED_DEFAULTS=false.

Пороги финальности — стартовые значения из ТЗ (та же таблица, что в
договорной оговорке Продукта 1); клиент меняет их под свою политику.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import Asset, Network

log = logging.getLogger("connector.seed")

DEFAULT_NETWORKS = [
    {"code": "tron", "name": "TRON", "finality_depth": 19},
    {"code": "ethereum", "name": "Ethereum", "finality_depth": 12},
]

# Боевые адреса контрактов стейблкоинов.
DEFAULT_ASSETS = [
    {"network": "tron", "symbol": "USDT",
     "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "decimals": 6},
    {"network": "tron", "symbol": "USDC",
     "contract_address": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", "decimals": 6},
    {"network": "ethereum", "symbol": "USDT",
     "contract_address": "0xdac17f958d2ee523a2206206994597c13d831ec7", "decimals": 6},
    {"network": "ethereum", "symbol": "USDC",
     "contract_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "decimals": 6},
]


async def seed_defaults(session: AsyncSession) -> bool:
    """Создать сети и активы по умолчанию, если справочники пусты.

    Возвращает True, если что-то было создано.
    """
    networks_count = await session.scalar(select(func.count(Network.id)))
    assets_count = await session.scalar(select(func.count(Asset.id)))
    if networks_count or assets_count:
        return False

    by_code: dict[str, Network] = {}
    for net in DEFAULT_NETWORKS:
        network = Network(**net)
        session.add(network)
        by_code[network.code] = network
    await session.flush()
    for asset in DEFAULT_ASSETS:
        session.add(
            Asset(
                network_id=by_code[asset["network"]].id,
                symbol=asset["symbol"],
                contract_address=asset["contract_address"],
                decimals=asset["decimals"],
            )
        )
    await session.flush()
    log.info(
        "Справочники заполнены по умолчанию: %d сетей, %d активов",
        len(DEFAULT_NETWORKS),
        len(DEFAULT_ASSETS),
    )
    return True
