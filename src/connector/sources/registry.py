"""Реестр адаптеров источников (specs/depository-adapter.md, п. 4.3).

Единый механизм регистрации для обоих семейств: chain-адаптеры
регистрируются по коду сети, custody-адаптеры (фаза 3) — по id
депозитария. Регистрация — декоратором на классе; создание — фабрикой
from_config, чтобы вызывающий код (worker, будущий загрузчик выписок)
не знал конкретных классов и их конструкторов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from connector.indexer.base import ChainAdapter

_chain_sources: dict[str, type["ChainAdapter"]] = {}


def register_chain_source(network_code: str):
    """Зарегистрировать адаптер сети под кодом (совпадает с Network.code)."""

    def decorator(cls):
        _chain_sources[network_code] = cls
        return cls

    return decorator


def chain_source_codes() -> list[str]:
    return sorted(_chain_sources)


def create_chain_source(
    network_code: str, url: str, api_key: str = "", source_name: str = ""
) -> "ChainAdapter":
    """Создать адаптер сети по коду; LookupError для незарегистрированной сети."""
    try:
        cls = _chain_sources[network_code]
    except KeyError:
        raise LookupError(
            f"Нет зарегистрированного адаптера для сети «{network_code}»; "
            f"доступны: {', '.join(chain_source_codes()) or '—'}"
        ) from None
    return cls.from_config(url, api_key=api_key, source_name=source_name)
