#!/usr/bin/env python3
"""Интеграционный прогон против живых внешних API (read-only).

Проверяет каждую точку интеграции коннектора на реальных данных:
  1. TRON: высота блока, трансферы TRC-20 USDT по адресу, блок/комиссия
     транзакции (TronGrid; API-ключ повышает лимиты, но не обязателен);
  2. Ethereum: высота блока, логи ERC-20 (нужен RPC-URL);
  3. Курсы: ЦБ РФ (USD/RUB), CoinGecko (TRX/USD).

Ничего не пишет в БД и никуда не отправляет — только чтение публичных
данных. Запуск:

  python scripts/integration_check.py --tron-address TXmVpi... \
      [--tron-api-key KEY] [--eth-rpc https://...] [--eth-address 0x...]

Вывод — отчёт по каждой проверке; код возврата 1, если что-то упало.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from connector.indexer.ethereum import EthereumAdapter  # noqa: E402
from connector.indexer.tron import TronAdapter  # noqa: E402
from connector.rates.sources import CbrRateSource, CoinGeckoSource  # noqa: E402

USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_ERC20 = "0xdac17f958d2ee523a2206206994597c13d831ec7"

PASS, FAIL, SKIP = "✅", "❌", "⏭️"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"{status} {name}: {detail}")


async def check(name: str, coro):
    try:
        detail = await coro
        record(PASS, name, detail)
        return True
    except Exception as exc:  # noqa: BLE001 — отчёт, не логика
        record(FAIL, name, f"{type(exc).__name__}: {exc}")
        return False


async def run_tron(address: str, api_key: str) -> None:
    tron = TronAdapter("https://api.trongrid.io", api_key=api_key, source_name="check")

    async def head():
        block = await tron.latest_block()
        assert block > 60_000_000, f"подозрительная высота {block}"
        return f"высота блока {block}"

    ok = await check("TRON latest_block", head())
    if not ok:
        return

    transfers: list = []

    async def fetch():
        nonlocal transfers
        since = datetime.now(timezone.utc) - timedelta(days=30)
        transfers = await tron.fetch_transfers(address, [USDT_TRC20], since=since)
        sample = transfers[0].tx_hash[:16] + "…" if transfers else "нет за 30 дней"
        return f"трансферов USDT: {len(transfers)} (пример: {sample})"

    await check("TRON fetch_transfers", fetch())

    if transfers:
        tx = transfers[0]

        async def block_of():
            block = await tron.get_transaction_block(tx.tx_hash)
            assert block == tx.block_number, f"блок {block} != {tx.block_number} из выдачи"
            return f"блок транзакции подтверждён: {block}"

        async def fee_of():
            fee, asset = await tron.get_transaction_fee(tx.tx_hash)
            return f"комиссия {fee} {asset}"

        await check("TRON get_transaction_block", block_of())
        await check("TRON get_transaction_fee", fee_of())

    async def reorg_probe():
        gone = await tron.get_transaction_block("0" * 64)
        assert gone is None, "несуществующий хэш должен давать None"
        return "несуществующая транзакция корректно отдаёт None"

    await check("TRON reorg-проверка (негатив)", reorg_probe())
    await tron.aclose()


async def run_eth(rpc_url: str, address: str | None) -> None:
    eth = EthereumAdapter(rpc_url, source_name="check")

    async def head():
        block = await eth.latest_block()
        assert block > 20_000_000, f"подозрительная высота {block}"
        return f"высота блока {block}"

    ok = await check("ETH latest_block", head())
    if ok and address:

        async def fetch():
            latest = await eth.latest_block()
            transfers = await eth.fetch_transfers(
                address, [USDT_ERC20], from_block=latest - 50_000
            )
            return f"трансферов USDT за ~50k блоков: {len(transfers)}"

        await check("ETH fetch_transfers", fetch())
    elif ok:
        record(SKIP, "ETH fetch_transfers", "адрес не задан (--eth-address)")
    await eth.aclose()


async def run_rates() -> None:
    now = datetime.now(timezone.utc)

    async def cbr():
        quote = await CbrRateSource().get_quote("USD", "RUB", now)
        assert 30 < quote.rate < 500, f"неправдоподобный курс {quote.rate}"
        return f"USD/RUB = {quote.rate}"

    async def coingecko():
        quote = await CoinGeckoSource().get_quote("TRX", "USD", now)
        assert 0 < quote.rate < 100, f"неправдоподобный курс {quote.rate}"
        return f"TRX/USD = {quote.rate}"

    await check("ЦБ РФ (курс к рублю)", cbr())
    await check("CoinGecko (нативные монеты)", coingecko())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tron-address", help="адрес TRON для чтения трансферов")
    parser.add_argument("--tron-api-key", default="")
    parser.add_argument("--eth-rpc", default="", help="URL JSON-RPC Ethereum")
    parser.add_argument("--eth-address", default="")
    args = parser.parse_args()

    print("Интеграционный прогон (read-only)\n" + "=" * 50)
    if args.tron_address:
        await run_tron(args.tron_address, args.tron_api_key)
    else:
        record(SKIP, "TRON", "адрес не задан (--tron-address)")
    if args.eth_rpc:
        await run_eth(args.eth_rpc, args.eth_address or None)
    else:
        record(SKIP, "Ethereum", "RPC не задан (--eth-rpc)")
    await run_rates()

    print("=" * 50)
    failed = [r for r in results if r[0] == FAIL]
    print(f"Итого: {len(results)} проверок, ошибок: {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
