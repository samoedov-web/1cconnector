"""Справочник контрагентов и договоров для веб-панели.

Основной источник справочников — синхронизация из 1С (onec/sync.py);
панель даёт просмотр и ручное создание (до внедрения обмена или для
контрагентов, которых ещё нет в 1С). Данные ручного создания получат
GUID при следующей синхронизации по совпадению — или останутся локальными.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.db import get_session
from connector.models import (
    AuditLog,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Invoice,
    Network,
)
from connector.security import CurrentUser, require_operator, require_reader

router = APIRouter(prefix="/api/v1/counterparties", tags=["directory"])


class CounterpartyRow(BaseModel):
    id: int
    name: str
    onec_ref: str
    addresses: int
    contracts: int


@router.get("", response_model=list[CounterpartyRow])
async def list_counterparties(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[CounterpartyRow]:
    addr_counts = dict(
        (
            await session.execute(
                select(CounterpartyAddress.counterparty_id, func.count())
                .group_by(CounterpartyAddress.counterparty_id)
            )
        ).all()
    )
    contract_counts = dict(
        (
            await session.execute(
                select(Contract.counterparty_id, func.count()).group_by(
                    Contract.counterparty_id
                )
            )
        ).all()
    )
    rows = (
        (await session.execute(select(Counterparty).order_by(Counterparty.name)))
        .scalars()
        .all()
    )
    return [
        CounterpartyRow(
            id=c.id,
            name=c.name,
            onec_ref=c.onec_ref,
            addresses=addr_counts.get(c.id, 0),
            contracts=contract_counts.get(c.id, 0),
        )
        for c in rows
    ]


@router.get("/{counterparty_id}")
async def counterparty_detail(
    counterparty_id: int,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> dict:
    """Карточка: адреса, договоры и инвойсы по ним — то, из чего оператор
    выбирает при ручном разборе."""
    counterparty = await session.scalar(
        select(Counterparty)
        .options(selectinload(Counterparty.addresses))
        .where(Counterparty.id == counterparty_id)
    )
    if counterparty is None:
        raise HTTPException(status_code=404, detail="Контрагент не найден")

    networks = dict(
        (await session.execute(select(Network.id, Network.code))).all()
    )
    contracts = (
        (
            await session.execute(
                select(Contract).where(Contract.counterparty_id == counterparty_id)
            )
        )
        .scalars()
        .all()
    )
    contract_ids = [c.id for c in contracts]
    invoices_by_contract: dict[int, list[Invoice]] = {}
    if contract_ids:
        for inv in (
            (
                await session.execute(
                    select(Invoice).where(Invoice.contract_id.in_(contract_ids))
                )
            )
            .scalars()
            .all()
        ):
            invoices_by_contract.setdefault(inv.contract_id, []).append(inv)

    return {
        "id": counterparty.id,
        "name": counterparty.name,
        "onec_ref": counterparty.onec_ref,
        "addresses": [
            {
                "id": a.id,
                "network": networks.get(a.network_id, "?"),
                "address": a.address,
                "origin": a.origin,
            }
            for a in counterparty.addresses
        ],
        "contracts": [
            {
                "id": c.id,
                "number": c.number,
                "registration_number": c.registration_number,
                "currency": c.currency,
                "onec_ref": c.onec_ref,
                "invoices": [
                    {
                        "id": i.id,
                        "number": i.number,
                        "amount": str(i.amount),
                        "paid_amount": str(i.paid_amount),
                        "currency": i.currency,
                        "status": i.status.value,
                        "due_from": i.due_from.isoformat() if i.due_from else None,
                        "due_to": i.due_to.isoformat() if i.due_to else None,
                    }
                    for i in invoices_by_contract.get(c.id, [])
                ],
            }
            for c in contracts
        ],
    }


class CounterpartyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)


@router.post("", status_code=201)
async def create_counterparty(
    data: CounterpartyCreateIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    counterparty = Counterparty(name=data.name)
    session.add(counterparty)
    await session.flush()
    session.add(
        AuditLog(
            actor=user.username,
            action="counterparty_created",
            entity="counterparty",
            entity_id=str(counterparty.id),
            details={"name": data.name},
        )
    )
    await session.commit()
    return {"id": counterparty.id, "name": counterparty.name}


class AddressCreateIn(BaseModel):
    network_code: str
    address: str = Field(min_length=1, max_length=128)


@router.post("/{counterparty_id}/addresses", status_code=201)
async def add_address(
    counterparty_id: int,
    data: AddressCreateIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    if await session.get(Counterparty, counterparty_id) is None:
        raise HTTPException(status_code=404, detail="Контрагент не найден")
    network = await session.scalar(select(Network).where(Network.code == data.network_code))
    if network is None:
        raise HTTPException(status_code=404, detail=f"Сеть {data.network_code} не настроена")
    taken = await session.scalar(
        select(CounterpartyAddress.id).where(
            CounterpartyAddress.network_id == network.id,
            CounterpartyAddress.address == data.address,
        )
    )
    if taken is not None:
        raise HTTPException(status_code=409, detail="Адрес уже привязан к контрагенту")
    address = CounterpartyAddress(
        counterparty_id=counterparty_id,
        network_id=network.id,
        address=data.address,
        origin="manual",
    )
    session.add(address)
    await session.flush()
    session.add(
        AuditLog(
            actor=user.username,
            action="counterparty_address_added",
            entity="counterparty_address",
            entity_id=str(address.id),
            details={"counterparty_id": counterparty_id, "address": data.address},
        )
    )
    await session.commit()
    return {"id": address.id}
