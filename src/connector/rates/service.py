"""Сервис курсов: снимок на момент финальности, цепочка пересчёта.

Пересчёт: актив → валюта контракта → рубль, с сохранением всех
промежуточных значений и источника. Резервный источник подхватывается
при недоступности основного; факт фолбэка пишется в source_details.
"""

from __future__ import annotations

from datetime import datetime

from connector.models import RateSnapshot
from connector.rates.base import Quote, RateSource


class RateService:
    def __init__(self, primary: RateSource, fallback: RateSource | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    async def _quote(self, base: str, quote: str, as_of: datetime) -> tuple[Quote, bool]:
        try:
            return await self.primary.get_quote(base, quote, as_of), False
        except Exception:
            if self.fallback is None:
                raise
            return await self.fallback.get_quote(base, quote, as_of), True

    async def snapshot(
        self,
        asset_symbol: str,
        contract_currency: str,
        as_of: datetime,
        purpose: str = "finality",
        transaction_id: int | None = None,
    ) -> RateSnapshot:
        """Снимок курса: asset→contract_currency и contract_currency→RUB."""
        a2c, a2c_fb = await self._quote(asset_symbol, contract_currency, as_of)
        c2r, c2r_fb = await self._quote(contract_currency, "RUB", as_of)
        return RateSnapshot(
            transaction_id=transaction_id,
            as_of=as_of,
            purpose=purpose,
            asset_symbol=asset_symbol,
            contract_currency=contract_currency,
            asset_to_contract=a2c.rate,
            contract_to_rub=c2r.rate,
            asset_to_rub=a2c.rate * c2r.rate,
            source_primary=a2c.source,
            source_details={
                "asset_to_contract": {
                    "source": a2c.source,
                    "fallback_used": a2c_fb,
                    "raw": a2c.raw,
                },
                "contract_to_rub": {
                    "source": c2r.source,
                    "fallback_used": c2r_fb,
                    "raw": c2r.raw,
                },
            },
        )
