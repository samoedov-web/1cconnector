"""Адаптер Ethereum (ERC-20) поверх стандартного JSON-RPC.

Работает с любой нодой/провайдером, поддерживающим eth_getLogs
(собственная лёгкая нода, Infura, Alchemy, публичные RPC). Кросс-проверка —
второй экземпляр с другим rpc_url.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from connector.indexer.base import ChainAdapter, RawTransfer

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Публичные RPC ограничивают диапазон/размер ответа eth_getLogs —
# история читается кусками. Значение перекрывается конструктором.
DEFAULT_BLOCK_CHUNK = 20_000


def block_ranges(start: int, end: int, chunk: int) -> list[tuple[int, int]]:
    """Разбить [start, end] на включительные поддиапазоны размером chunk."""
    if end < start:
        return []
    ranges = []
    left = start
    while left <= end:
        right = min(left + chunk - 1, end)
        ranges.append((left, right))
        left = right + 1
    return ranges


def _addr_topic(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:]


class EthereumAdapter(ChainAdapter):
    network_code = "ethereum"

    def __init__(
        self,
        rpc_url: str,
        source_name: str = "eth-rpc",
        block_chunk: int = DEFAULT_BLOCK_CHUNK,
    ) -> None:
        self.source_name = source_name
        self._client = httpx.AsyncClient(timeout=30)
        self._rpc_url = rpc_url
        self._block_chunk = block_chunk
        self._id = 0
        self._block_time_cache: dict[int, datetime] = {}
        self._decimals_cache: dict[str, int] = {}

    async def _rpc(self, method: str, params: list) -> dict | str | list:
        self._id += 1
        resp = await self._client.post(
            self._rpc_url,
            json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"RPC error {method}: {body['error']}")
        return body["result"]

    async def latest_block(self) -> int:
        return int(await self._rpc("eth_blockNumber", []), 16)

    async def _block_time(self, block_number: int) -> datetime:
        if block_number not in self._block_time_cache:
            block = await self._rpc("eth_getBlockByNumber", [hex(block_number), False])
            self._block_time_cache[block_number] = datetime.fromtimestamp(
                int(block["timestamp"], 16), tz=timezone.utc
            )
        return self._block_time_cache[block_number]

    async def _decimals(self, token: str) -> int:
        if token not in self._decimals_cache:
            # decimals() -> selector 0x313ce567
            result = await self._rpc(
                "eth_call", [{"to": token, "data": "0x313ce567"}, "latest"]
            )
            self._decimals_cache[token] = int(result, 16)
        return self._decimals_cache[token]

    async def fetch_transfers(
        self,
        address: str,
        token_contracts: list[str],
        since: datetime | None = None,
        from_block: int | None = None,
    ) -> list[RawTransfer]:
        start = from_block if from_block is not None else 0
        latest = await self.latest_block()
        transfers: list[RawTransfer] = []
        # По кускам блоков; в каждом — два запроса: адрес как получатель
        # (topic2) и как отправитель (topic1).
        for range_from, range_to in block_ranges(start, latest, self._block_chunk):
            for topics in (
                [TRANSFER_TOPIC, None, _addr_topic(address)],
                [TRANSFER_TOPIC, _addr_topic(address)],
            ):
                logs = await self._rpc(
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(range_from),
                            "toBlock": hex(range_to),
                            "address": token_contracts,
                            "topics": topics,
                        }
                    ],
                )
                transfers.extend(await self._parse_logs(logs, since))
        # self-transfer попадёт в оба запроса — дедупликация по (hash, log_index)
        unique = {(t.tx_hash, t.log_index): t for t in transfers}
        return list(unique.values())

    async def _parse_logs(self, logs: list, since: datetime | None) -> list[RawTransfer]:
        transfers: list[RawTransfer] = []
        for log in logs:
            block_number = int(log["blockNumber"], 16)
            block_time = await self._block_time(block_number)
            if since is not None and block_time < since:
                continue
            token = log["address"].lower()
            decimals = await self._decimals(token)
            transfers.append(
                RawTransfer(
                    tx_hash=log["transactionHash"],
                    log_index=int(log["logIndex"], 16),
                    block_number=block_number,
                    block_time=block_time,
                    token_contract=token,
                    from_address=_topic_addr(log["topics"][1]),
                    to_address=_topic_addr(log["topics"][2]),
                    amount=Decimal(int(log["data"], 16)) / Decimal(10**decimals),
                    fee_amount=Decimal(0),  # газ догружается по receipt отдельно
                    fee_asset="ETH",
                    raw=log,
                    source=self.source_name,
                )
            )
        return transfers

    async def get_transaction_block(self, tx_hash: str) -> int | None:
        receipt = await self._rpc("eth_getTransactionReceipt", [tx_hash])
        if not receipt or receipt.get("blockNumber") is None:
            return None
        return int(receipt["blockNumber"], 16)

    async def aclose(self) -> None:
        await self._client.aclose()
