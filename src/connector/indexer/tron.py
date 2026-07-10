"""Адаптер TRON (TRC-20) поверх TronGrid-совместимого API.

Источник конфигурируем: собственная нода с плагином событий или внешний
провайдер (TronGrid, TronScan API). Минимум два источника на сеть с
кросс-проверкой — второй экземпляр адаптера с другим base_url.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from connector.indexer.base import ChainAdapter, RawTransfer


class TronAdapter(ChainAdapter):
    network_code = "tron"

    def __init__(self, base_url: str, api_key: str = "", source_name: str = "trongrid") -> None:
        self.base_url = base_url.rstrip("/")
        self.source_name = source_name
        headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30)

    async def latest_block(self) -> int:
        resp = await self._client.get("/wallet/getnowblock")
        resp.raise_for_status()
        return int(resp.json()["block_header"]["raw_data"]["number"])

    async def fetch_transfers(
        self,
        address: str,
        token_contracts: list[str],
        since: datetime | None = None,
        from_block: int | None = None,
    ) -> list[RawTransfer]:
        wanted = {c.lower() for c in token_contracts}
        transfers: list[RawTransfer] = []
        params: dict = {"limit": 200, "only_confirmed": "false"}
        if since is not None:
            params["min_timestamp"] = int(since.timestamp() * 1000)

        url = f"/v1/accounts/{address}/transactions/trc20"
        while url:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
            for item in body.get("data", []):
                token = item.get("token_info", {})
                if token.get("address", "").lower() not in wanted:
                    continue
                decimals = int(token.get("decimals", 6))
                block_ts = datetime.fromtimestamp(
                    item["block_timestamp"] / 1000, tz=timezone.utc
                )
                transfers.append(
                    RawTransfer(
                        tx_hash=item["transaction_id"],
                        log_index=0,  # TRC-20 API отдаёт по одному событию на запись
                        block_number=int(item.get("block", 0)),
                        block_time=block_ts,
                        token_contract=token.get("address", ""),
                        from_address=item["from"],
                        to_address=item["to"],
                        amount=Decimal(item["value"]) / Decimal(10**decimals),
                        fee_amount=Decimal(0),  # комиссия догружается по хэшу отдельно
                        fee_asset="TRX",
                        raw=item,
                        source=self.source_name,
                    )
                )
            # пагинация TronGrid: meta.links.next — полный URL следующей страницы
            url = body.get("meta", {}).get("links", {}).get("next")
            params = {}
        return transfers

    async def get_transaction_block(self, tx_hash: str) -> int | None:
        info = await self._transaction_info(tx_hash)
        if not info or "blockNumber" not in info:
            return None
        return int(info["blockNumber"])

    async def get_transaction_fee(self, tx_hash: str) -> tuple[Decimal, str]:
        info = await self._transaction_info(tx_hash)
        # fee — суммарная комиссия в sun (1 TRX = 1e6 sun): сожжённые
        # bandwidth/energy; при полном покрытии ресурсами аккаунта fee = 0.
        fee_sun = int(info.get("fee", 0)) if info else 0
        return Decimal(fee_sun) / Decimal(10**6), "TRX"

    async def _transaction_info(self, tx_hash: str) -> dict:
        resp = await self._client.post(
            "/wallet/gettransactioninfobyid", json={"value": tx_hash}
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
