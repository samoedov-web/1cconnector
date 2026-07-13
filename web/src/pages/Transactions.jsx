import { useCallback, useEffect, useState } from 'react'
import { api, openReport } from '../api'

const STATUS_BADGE = { seen: 'off', confirmed: 'warn', final: 'ok', orphaned: 'err' }
const PAGE = 50

function TxCard({ txId, onClose }) {
  const [card, setCard] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api(`/api/v1/transactions/${txId}`).then(setCard).catch((e) => setError(e.message))
  }, [txId])

  if (error) return <div className="panel"><div className="error">{error}</div></div>
  if (!card) return <div className="panel muted">Загрузка…</div>

  const p = card.payment
  const imm = card.immutability
  return (
    <div className="panel">
      <div className="row space-between">
        <h3>{p.direction === 'in' ? 'Поступление' : 'Выбытие'} {p.amount} {p.asset}</h3>
        <button className="secondary small" onClick={onClose}>Закрыть</button>
      </div>
      <div className="kv">
        <span>Отправитель</span><code>{p.from_address}</code>
        <span>Получатель</span><code>{p.to_address}</code>
        <span>Время блока</span><b>{new Date(p.block_time).toLocaleString('ru-RU')}</b>
        <span>Финальность</span>
        <b>{p.finalized_at ? `${new Date(p.finalized_at).toLocaleString('ru-RU')} (${p.confirmations} подтв.)` : '—'}</b>
        {card.rate && (<>
          <span>Сумма в рублях</span><b>{card.rate.amount_rub} ₽</b>
          <span>Курс / источник</span><b>{card.rate.asset_to_rub} ({card.rate.source})</b>
        </>)}
      </div>

      <h4>Привязки</h4>
      {card.allocations.length === 0 && <p className="muted">Нет привязок</p>}
      {card.allocations.map((a, i) => (
        <div className="kv" key={i}>
          <span>Контрагент</span><b>{a.counterparty || '—'}</b>
          <span>Контракт</span>
          <b>{a.contract_number || '—'}{a.contract_registration_number ? ` (уч. № ${a.contract_registration_number})` : ''}</b>
          <span>Инвойс / сумма</span><b>{a.invoice_number || '—'} / {a.amount}</b>
        </div>
      ))}

      <h4>Обмен с 1С</h4>
      {card.onec_documents.length === 0 && <p className="muted">Документы ещё не сформированы</p>}
      {card.onec_documents.map((d, i) => (
        <div className="kv" key={i}>
          <span>{d.doc_type}</span>
          <b>{d.status}{d.onec_ref ? ` · GUID ${d.onec_ref}` : ''}</b>
        </div>
      ))}

      <h4>Журнал неизменяемости</h4>
      <div className="kv">
        <span>Хэш</span><code>{imm.tx_hash}</code>
        <span>Блок / сеть</span><b>{imm.block_number} / {imm.network}</b>
        <span>Источник данных</span><b>{imm.source}</b>
        <span>Получено</span><b>{imm.received_at ? new Date(imm.received_at).toLocaleString('ru-RU') : '—'}</b>
        <span>SHA-256 ответа ноды</span><code className="hash">{imm.raw_response_sha256}</code>
      </div>

      <div className="row">
        <button
          className="secondary"
          onClick={() => openReport(`/api/v1/reports/payment-act/${card.id}`, { format: 'html' })}
        >
          Акт (печать)
        </button>
        <button
          className="secondary"
          onClick={() =>
            openReport(`/api/v1/reports/payment-act/${card.id}`, { format: 'xlsx' }, `payment-act-${card.id}.xlsx`)
          }
        >
          Акт (XLSX)
        </button>
        <button
          className="secondary"
          onClick={() => openReport(`/api/v1/reports/fns-notification/${card.id}`, { format: 'html' })}
        >
          Уведомление ФНС
        </button>
        <button
          className="secondary"
          onClick={() =>
            openReport(`/api/v1/reports/fns-notification/${card.id}`, { format: 'xml' }, `fns-notification-${card.id}.xml`)
          }
        >
          ФНС (XML)
        </button>
      </div>
    </div>
  )
}

export default function Transactions() {
  const [filters, setFilters] = useState({ network: '', status: '', direction: '', tx_hash: '' })
  const [page, setPage] = useState(0)
  const [data, setData] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    api('/api/v1/transactions', {
      params: { ...filters, limit: PAGE, offset: page * PAGE },
    })
      .then(setData)
      .catch((e) => setError(e.message))
  }, [filters, page])
  useEffect(load, [load])

  const setFilter = (key) => (e) => {
    setPage(0)
    setFilters({ ...filters, [key]: e.target.value })
  }

  return (
    <>
      <h1>Транзакции</h1>
      <div className="filters">
        <select value={filters.network} onChange={setFilter('network')}>
          <option value="">Все сети</option>
          <option value="tron">tron</option>
          <option value="ethereum">ethereum</option>
        </select>
        <select value={filters.status} onChange={setFilter('status')}>
          <option value="">Любой статус</option>
          <option value="seen">seen</option>
          <option value="confirmed">confirmed</option>
          <option value="final">final</option>
          <option value="orphaned">orphaned</option>
        </select>
        <select value={filters.direction} onChange={setFilter('direction')}>
          <option value="">Все операции</option>
          <option value="in">поступление</option>
          <option value="out">выбытие</option>
        </select>
        <input
          placeholder="Хэш (или начало)"
          value={filters.tx_hash}
          onChange={setFilter('tx_hash')}
        />
      </div>
      {error && <div className="error">{error}</div>}
      {data && (
        <>
          <table>
            <thead>
              <tr>
                <th>Время блока</th>
                <th>Сеть</th>
                <th>Операция</th>
                <th>Сумма</th>
                <th>Статус</th>
                <th>Хэш</th>
                <th>Сверка</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((t) => (
                <tr key={t.id} onClick={() => setOpenId(t.id)} className="clickable">
                  <td>{new Date(t.block_time).toLocaleString('ru-RU')}</td>
                  <td>{t.network}</td>
                  <td>{t.direction === 'in' ? 'поступление' : 'выбытие'}</td>
                  <td><b>{t.amount}</b> {t.asset}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[t.status]}`}>
                      {t.status}{t.status === 'confirmed' ? ` (${t.confirmations})` : ''}
                    </span>
                  </td>
                  <td><code className="hash">{t.tx_hash}</code></td>
                  <td>{t.cross_checked ? '✓✓' : '—'}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr><td colSpan={7} className="muted">Ничего не найдено</td></tr>
              )}
            </tbody>
          </table>
          <div className="row space-between">
            <span className="muted">Всего: {data.total}</span>
            <div className="row">
              <button className="secondary small" disabled={page === 0} onClick={() => setPage(page - 1)}>
                ← Назад
              </button>
              <button
                className="secondary small"
                disabled={(page + 1) * PAGE >= data.total}
                onClick={() => setPage(page + 1)}
              >
                Вперёд →
              </button>
            </div>
          </div>
        </>
      )}
      {openId && <TxCard txId={openId} onClose={() => setOpenId(null)} />}
    </>
  )
}
