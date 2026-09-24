#!/usr/bin/env python3
"""Скрипт загрузки тестовых фикстур в БД (Stage 9).

Используется для ручного тестирования и демо.
"""
import asyncio
import sys
sys.path.insert(0, 'src')

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from connector.models import Base, Network, Asset, Wallet

async def load_fixtures(db_url: str):
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Создаем тестовую сеть
        net = Network(code="ethereum_test", name="Ethereum Testnet", finality_depth=12)
        session.add(net)
        await session.flush()
        
        # Создаем тестовый актив
        asset = Asset(network_id=net.id, symbol="USDT", contract_address="0x...", decimals=6)
        session.add(asset)
        await session.flush()
        
        # Создаем тестовый кошелек
        wallet = Wallet(network_id=net.id, address="0xWalletAddress", label="Test Wallet")
        session.add(wallet)
        
        await session.commit()
        print("Fixtures loaded successfully.")

if __name__ == "__main__":
    db_url = sys.argv[1] if len(sys.argv) > 1 else "postgresql+asyncpg://user:pass@localhost/connector"
    asyncio.run(load_fixtures(db_url))
