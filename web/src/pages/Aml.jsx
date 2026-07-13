import { useCallback, useEffect, useState } from 'react'
import { api, getAuth } from '../api'

const STATUS_RU = {
  pending_aml: 'ждёт проверки',
  aml_approved: 'одобрен к отправке',
  aml_review: 'на решении комплаенса',
  aml_rejected: 'отправка запрещена',
  sent: 'отправлен',
  matched: 'сопоставлен',
  expired: 'одобрение истекло',
}
const BADGE = {
  pending_aml: 'off', aml_approved: 'ok', aml_review: 'warn',
  aml_rejected: 'err', sent: 'ok', matched: 'ok', expired: 'warn',
}

function Score({ value }) {
  if (value === null || value === undefined) return '—'
  const cls = value <= 30 ? 'ok' : value <= 70 ? 'warn' : 'err'
  return <span className={`badge ${cls}`}>{value}</span>
}

function Row({ p, onDecide, canDecide }) {
  const [note, setNote] = useState('')
  const [deciding, setDeciding] = useState(false)
  return (
    <tr>
      <td>{p.invoice_number} <span className="muted">/ {p.contract_number}</span></td>
      <td>{p.counterparty}</td>
      <td><b>{p.amount}</b> {p.currency}</td>
      <td><code className="hash">{p.to_address}</code></td>
      <td><Score value={p.risk_score} />{p.categories.length > 0 && (
        <div className="muted" style={{ fontSize: 11 }}>{p.categories.join(', ')}</div>
      )}</td>
      <td><span className={`badge ${BADGE[p.status]}`}>{STATUS_RU[p.status]}</span>
        {p.decided_by && (
          <div className="muted" style={{ fontSize: 11 }}>
            {p.decided_by}{p.decision_note && `: ${p.decision_note}`}
          </div>
        )}
      </td>
      <td>
        {canDecide && p.status === 'aml_review' && (
          deciding ? (
            <div className="row">
              <input placeholder="Комментарий" value={note}
                onChange={(e) => setNote(e.target.value)} style={{ minWidth: 110 }} />
              <button className="small" onClick={() => onDecide(p.id, 'approve', note)}>
                Одобрить
              </button>
              <button className="small secondary"
                onClick={() => onDecide(p.id, 'reject', note)}>
                Отклонить
              </button>
              <button className="small secondary" onClick={() => setDeciding(false)}>×</button>
            </div>
          ) : (
            <button className="small" onClick={() => setDeciding(true)}>Решить</button>
          )
        )}
      </td>
    </tr>
  )
}

function Table({ title, rows, onDecide, canDecide, emptyText }) {
  return (
    <>
      <h2>{title}</h2>
      <table>
        <thead>
          <tr>
            <th>Инвойс / контракт</th><th>Контрагент</th><th>Сумма</th>
            <th>Адрес получателя</th><th>Риск</th><th>Статус</th><th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <Row key={p.id} p={p} onDecide={onDecide} canDecide={canDecide} />
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={7} className="muted">{emptyText}</td></tr>
          )}
        </tbody>
      </table>
    </>
  )
}

export default function Aml() {
  const [payments, setPayments] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const role = getAuth()?.role
  const canDecide = ['admin', 'compliance'].includes(role)

  const load = useCallback(() => {
    api('/api/v1/aml/payments').then(setPayments).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  const decide = async (id, action, note) => {
    setError(null); setNotice(null)
    try {
      const r = await api(`/api/v1/aml/payments/${id}/decide`, {
        method: 'POST', body: { action, note },
      })
      setNotice(`Решение записано: ${STATUS_RU[r.status]}`)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error && !payments) return <div className="error">{error}</div>
  if (!payments) return <div className="muted">Загрузка…</div>

  const review = payments.filter((p) => p.status === 'aml_review')
  const approved = payments.filter((p) => p.status === 'aml_approved')
  const rest = payments.filter(
    (p) => p.status !== 'aml_review' && p.status !== 'aml_approved',
  )

  return (
    <>
      <h1>AML-контроль</h1>
      <p className="muted">
        Проверка адресов получателей до отправки средств (шаги 3–5 регламента).
        Средний риск решает комплаенс-офицер; одобренные платежи казначей
        отправляет из корпоративного кошелька.
      </p>
      {error && <div className="error">{error}</div>}
      {notice && <div className="panel" style={{ marginTop: 0 }}>{notice}</div>}

      <Table title={`Очередь комплаенса (${review.length})`} rows={review}
        onDecide={decide} canDecide={canDecide}
        emptyText="Нет платежей, ожидающих решения" />
      <Table title={`Одобрено к отправке (${approved.length})`} rows={approved}
        onDecide={decide} canDecide={false}
        emptyText="Нет одобренных платежей" />
      <Table title="Остальные" rows={rest} onDecide={decide} canDecide={false}
        emptyText="Пусто" />
    </>
  )
}
