#!/usr/bin/env python3
"""CLI-генератор фикстур «выписки депозитария» (п. 4.4 спеки).

Берёт финальные транзакции коннектора за период (CONNECTOR_DATABASE_URL)
и производит файл-выписку для MockDepositoryAdapter с искажениями.

Примеры:
  # идеальная выписка за июль
  python scripts/custody_fixture_gen.py \
      --period-from 2026-07-01 --period-to 2026-07-31 --out fixtures/july.json

  # недостача 2 записей + лишняя запись + комиссия внутри суммы
  python scripts/custody_fixture_gen.py ... --drop 2 --extra 1 --amount-noise

  # агрегированная бедная выписка без хэшей
  python scripts/custody_fixture_gen.py ... --aggregate day --strip hashes,network
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from connector.config import settings  # noqa: E402
from connector.custody.fixtures import (  # noqa: E402
    DistortionOptions,
    chain_entries_for_period,
    distort,
    write_fixture,
)


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period-from", required=True, type=parse_date)
    parser.add_argument("--period-to", required=True, type=parse_date)
    parser.add_argument("--out", required=True, help="файл .json или .csv")
    parser.add_argument("--statement-id", default="")
    parser.add_argument("--drop", type=int, default=0)
    parser.add_argument("--extra", type=int, default=0)
    parser.add_argument("--amount-noise", action="store_true")
    parser.add_argument("--date-shift", type=int, default=0, metavar="DAYS")
    parser.add_argument("--aggregate", choices=["day", "counterparty"])
    parser.add_argument("--strip", default="",
                        help="через запятую: hashes,counterparty,network")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        entries = await chain_entries_for_period(
            session, args.period_from, args.period_to
        )
    await engine.dispose()

    options = DistortionOptions(
        drop=args.drop,
        extra=args.extra,
        amount_noise=args.amount_noise,
        date_shift_days=args.date_shift,
        aggregate=args.aggregate,
        strip=tuple(s for s in args.strip.split(",") if s),
    )
    distorted = distort(entries, options, seed=args.seed)

    statement_id = args.statement_id or (
        f"ST-{args.period_from:%Y%m%d}-{args.period_to:%Y%m%d}"
    )
    path = write_fixture(
        args.out,
        statement_id=statement_id,
        period_from=args.period_from,
        period_to=args.period_to,
        entries=distorted,
        issued_at=datetime.now(timezone.utc),
        granularity="aggregated" if args.aggregate else "per_tx",
    )
    print(
        f"Фикстура {statement_id}: chain-записей {len(entries)}, "
        f"в выписке {len(distorted)} → {path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
