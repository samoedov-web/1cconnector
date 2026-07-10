"""Реализации источников котировок.

Конкретный набор источников фиксируется в учётной политике клиента при
внедрении; здесь — базовые реализации для MVP:

- CbrRateSource: официальные курсы ЦБ РФ (фиат → RUB);
- StaticPegSource: котировка стейблкоина к валюте привязки 1:1 — используется
  только если так зафиксировано в учётной политике клиента; иначе подключается
  биржевой источник (реализация RateSource поверх API площадки из договора).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

import httpx

from connector.rates.base import Quote, RateSource

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


class CbrRateSource(RateSource):
    """Курсы ЦБ РФ на дату (XML_daily). Поддерживает пары <валюта>/RUB."""

    source_name = "cbr"

    def __init__(self, url: str = CBR_URL) -> None:
        self._url = url
        self._client = httpx.AsyncClient(timeout=30)

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        if quote.upper() != "RUB":
            raise ValueError(f"ЦБ РФ котирует только к RUB, запрошено {base}/{quote}")
        resp = await self._client.get(
            self._url, params={"date_req": as_of.strftime("%d/%m/%Y")}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        for valute in root.iter("Valute"):
            if valute.findtext("CharCode", "").upper() == base.upper():
                nominal = Decimal(valute.findtext("Nominal", "1"))
                value = Decimal(valute.findtext("Value", "0").replace(",", "."))
                return Quote(
                    base=base.upper(),
                    quote="RUB",
                    rate=value / nominal,
                    as_of=as_of,
                    source=self.source_name,
                    raw={"date": root.get("Date"), "nominal": str(nominal), "value": str(value)},
                )
        raise LookupError(f"Валюта {base} не найдена в выгрузке ЦБ РФ на {as_of:%d.%m.%Y}")

    async def aclose(self) -> None:
        await self._client.aclose()


class FallbackRateSource(RateSource):
    """Цепочка источников: котировка берётся у первого, кто её знает.

    LookupError (источник не знает пару) — пробуем следующий; сетевые ошибки
    тоже приводят к переходу дальше, но фиксируются в самой котировке нет —
    их видно в логах вызывающего кода.
    """

    source_name = "fallback-chain"

    def __init__(self, *sources: RateSource) -> None:
        self._sources = sources

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        last_error: Exception | None = None
        for source in self._sources:
            try:
                return await source.get_quote(base, quote, as_of)
            except Exception as exc:  # noqa: BLE001 — пробуем следующий источник
                last_error = exc
        raise last_error or LookupError(f"Нет источника для {base}/{quote}")


COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{id}/history"
COINGECKO_IDS = {"TRX": "tron", "ETH": "ethereum", "USDT": "tether", "USDC": "usd-coin"}


class CoinGeckoSource(RateSource):
    """Крипто → фиат по историческим данным CoinGecko (публичный API).

    Используется для оценки комиссий сети (TRX/ETH) и как биржевой источник,
    если он зафиксирован в учётной политике клиента. Сырой ответ сохраняется
    в снимке курса — выбор источника доказуем.
    """

    source_name = "coingecko"

    def __init__(self, url_template: str = COINGECKO_URL) -> None:
        self._url_template = url_template
        self._client = httpx.AsyncClient(timeout=30)

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        coin_id = COINGECKO_IDS.get(base.upper())
        if coin_id is None:
            raise LookupError(f"CoinGecko: неизвестный актив {base}")
        resp = await self._client.get(
            self._url_template.format(id=coin_id),
            params={"date": as_of.strftime("%d-%m-%Y"), "localization": "false"},
        )
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("market_data", {}).get("current_price", {})
        rate = prices.get(quote.lower())
        if rate is None:
            raise LookupError(f"CoinGecko: нет котировки {base}/{quote} на {as_of:%d.%m.%Y}")
        return Quote(
            base=base.upper(),
            quote=quote.upper(),
            rate=Decimal(str(rate)),
            as_of=as_of,
            source=self.source_name,
            raw={"date": as_of.strftime("%d-%m-%Y"), "price": str(rate)},
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class CompositeRateSource(RateSource):
    """Маршрутизация пар между источниками: <валюта>→RUB идёт в rub_source
    (ЦБ РФ), остальные пары (актив → валюта контракта) — в asset_source.

    Позволяет собрать «основной источник» из разных API, сохранив в снимке
    имя фактического источника каждой ноги пересчёта.
    """

    source_name = "composite"

    def __init__(self, asset_source: RateSource, rub_source: RateSource) -> None:
        self.asset_source = asset_source
        self.rub_source = rub_source

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        if quote.upper() == "RUB":
            return await self.rub_source.get_quote(base, quote, as_of)
        return await self.asset_source.get_quote(base, quote, as_of)


class StaticPegSource(RateSource):
    """Стейблкоин к валюте привязки по фиксированному курсу (по умолчанию 1:1).

    Применимо, только если такой порядок оценки зафиксирован в учётной
    политике клиента; фиксация в raw делает выбор источника доказуемым.
    """

    source_name = "static-peg"

    def __init__(self, pegs: dict[tuple[str, str], Decimal] | None = None) -> None:
        self._pegs = pegs or {("USDT", "USD"): Decimal(1), ("USDC", "USD"): Decimal(1)}

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        key = (base.upper(), quote.upper())
        if key not in self._pegs:
            raise LookupError(f"Нет фиксированной котировки для {base}/{quote}")
        return Quote(
            base=key[0],
            quote=key[1],
            rate=self._pegs[key],
            as_of=as_of,
            source=self.source_name,
            raw={"policy": "фиксированная привязка из учётной политики"},
        )
