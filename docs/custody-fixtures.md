# Фикстуры выписок депозитария (MockDepositoryAdapter)

Схемы файлов, которые читает `MockDepositoryAdapter`, и генератор фикстур
из реальных chain-данных. Контекст — `specs/depository-adapter.md`, п. 4.4.

## Формат JSON (один файл = одна выписка)

```json
{
  "statement_id": "ST-20260701-20260731",
  "period_from": "2026-07-01T00:00:00+00:00",
  "period_to": "2026-07-31T23:59:59+00:00",
  "issued_at": "2026-08-01T10:00:00+00:00",
  "granularity": "per_tx",
  "entries": [
    {
      "entry_id": "1",
      "occurred_at": "2026-07-06T14:23:51+00:00",
      "asset": "USDT",
      "network": "tron",
      "amount": "48500",
      "operation_type": "deposit",
      "counterparty_ref": "TN9RRaXk…",
      "external_tx_hash": "7d3f2a9c…"
    }
  ]
}
```

- `amount` — строка-Decimal со знаком: положительный — поступление,
  отрицательный — списание;
- `operation_type` — `deposit | withdrawal | trade | fee | transfer_internal | other`;
- `granularity` — `per_tx | aggregated | mixed` (по умолчанию `per_tx`);
- `network`, `counterparty_ref`, `external_tx_hash` — необязательные
  (эмуляция «бедной» выписки: adapter выведет соответствующие
  capabilities = false);
- `issued_at` — необязательное.

## Формат CSV (один файл может содержать несколько выписок)

Заголовок обязателен; колонки:

```
statement_id,period_from,period_to,issued_at,granularity,entry_id,occurred_at,asset,network,amount,operation_type,counterparty_ref,external_tx_hash
```

Метаданные выписки повторяются в каждой строке; строки группируются по
`statement_id` (метаданные берутся из первой строки группы). Пустая ячейка
необязательного поля = отсутствие значения.

## Конфигурация мок-адаптера

```python
create_custody_source(
    "mock-depo",
    fixtures_dir="fixtures/",       # каталог с *.json и *.csv
    latency_seconds=0.5,            # эмуляция медленного API (опционально)
    health_override="degraded",     # "" | degraded | down (опционально)
)
```

`health_override="down"` дополнительно роняет fetch-методы ConnectionError —
для тестов устойчивости загрузчика.

## Генератор фикстур

Производит выписку из финальных транзакций коннектора за период
(читает БД по `CONNECTOR_DATABASE_URL`) с искажениями:

| Флаг | Искажение | Сценарий раздела 6 спеки |
|---|---|---|
| `--drop N` | потерять N записей | недостача в выписке (6.2) |
| `--extra N` | N записей, которых нет в цепочке | лишняя запись (6.3) |
| `--amount-noise` | −0.1…0.5% суммы у ~половины строк | комиссия внутри суммы (6.4) |
| `--date-shift D` | ±D дней у ~20% строк | сдвиг через границу периода (6.5) |
| `--aggregate day\|counterparty` | одна строка = несколько tx, без хэшей | агрегаты (6.6, 6.7) |
| `--strip hashes,counterparty,network` | убрать поля | бедная выписка, проверка capabilities |
| `--seed N` | детерминированность | одинаковый seed → одинаковая фикстура |

Пример:

```bash
python scripts/custody_fixture_gen.py \
    --period-from 2026-07-01 --period-to 2026-07-31 \
    --out fixtures/july-poor.csv \
    --drop 2 --amount-noise --aggregate day --strip hashes,network --seed 42
```
