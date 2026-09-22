import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

// Форма привязки: контрагент → договор → инвойс (каскад из справочника).
function ResolveForm({ row, onDone, onCancel }) {
  const [counterparties, setCounterparties] = useState([])
  const [detail, setDetail] = useState(null) // карточка выбранного контрагента
  const [form, setForm] = useState({
    counterparty_id: '',
    contract_id: '',
    invoice_id: '',
    remember_address: true,
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/v1/counterparties').then(setCounterparties).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    setDetail(null)
    setForm((f) => ({ ...f, contract_id: '', invoice_id: '' }))
    if (form.counterparty_id) {
      api(`/api/v1/counterparties/${form.counterparty_id}`)
        .then(setDetail)
        .catch((e) => setError(e.message))
    }
  }, [form.counterparty_id])

  const contracts = detail?.contracts ?? []
  const invoices =
    contracts.find((c) => String(c.id) === String(form.contract_id))?.invoices ?? []

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api(`/api/v1/matching/${row.match_id}/resolve`, {
        method: 'POST',
        body: {
          counterparty_id: Number(form.counterparty_id),
          contract_id: form.contract_id ? Number(form.contract_id) : null,
          invoice_id: form.invoice_id ? Number(form.invoice_id) : null,
          remember_address: form.remember_address,
        },
      })
      onDone()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h3>Привязка транзакции</h3>
      <div className="kv">
        <span>Хэш</span><code>{row.tx_hash}</code>
        <span>Адрес второй стороны</span><code>{row.counterparty_address}</code>
        <span>Сумма к привязке</span>
        <b>{row.amount} {row.asset}</b>
      </div>
      <label>
        Контрагент *
        <select
          value={form.counterparty_id}
          onChange={(e) => setForm({ ...form, counterparty_id: e.target.value })}
          required
        >
          <option value="">— выбрать —</option>
          {counterparties.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </label>
      <label>
        Договор
        <select
          value={form.contract_id}
          onChange={(e) => setForm({ ...form, contract_id: e.target.value, invoice_id: '' })}
          disabled={!contracts.length}
        >
          <option value="">— не указывать —</option>
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.number} ({c.currency}{c.registration_number ? `, уч. № ${c.registration_number}` : ''})
            </option>
          ))}
        </select>
      </label>
      <label>
        Инвойс
        <select
          value={form.invoice_id}
          onChange={(e) => setForm({ ...form, invoice_id: e.target.value })}
          disabled={!invoices.length}
        >
          <option value="">— не указывать —</option>
          {invoices.map((i) => (
            <option key={i.id} value={i.id}>
              {i.number}: {i.amount} {i.currency} (оплачено {i.paid_amount}, {i.status})
            </option>
          ))}
        </select>
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.remember_address}
          onChange={(e) => setForm({ ...form, remember_address: e.target.checked })}
        />
        Запомнить адрес как правило автоматчинга
      </label>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <button disabled={busy || !form.counterparty_id}>
          {busy ? 'Сохранение…' : 'Привязать'}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>Отмена</button>
      </div>
    </form>
  )
}

export default function Queue() {
  const [rows, setRows] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    api('/api/v1/matching/pending').then(setRows).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  if (error) return <div className="error">{error}</div>
  if (!rows) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Очередь ручного разбора</h1>
      {rows.length === 0 && <p className="muted">Очередь пуста — всё сматчилось автоматически.</p>}
      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Время блока</th>
              <th>Сеть</th>
              <th>Операция</th>
              <th>Сумма</th>
              <th>Адрес второй стороны</th>
              <th>Хэш</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.match_id} className={selected?.match_id === r.match_id ? 'active' : ''}>
                <td>{new Date(r.block_time).toLocaleString('ru-RU')}</td>
                <td>{r.network}</td>
                <td>{r.direction === 'in' ? 'поступление' : 'выбытие'}</td>
                <td><b>{r.amount}</b> {r.asset}{r.amount !== r.tx_amount && (
                  <span className="muted"> из {r.tx_amount}</span>
                )}</td>
                <td><code>{r.counterparty_address}</code></td>
                <td><code className="hash">{r.tx_hash}</code></td>
                <td>
                  <button className="small" onClick={() => setSelected(r)}>Разобрать</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && (
        <ResolveForm
          row={selected}
          onDone={() => { setSelected(null); load() }}
          onCancel={() => setSelected(null)}
        />
      )}
    </>
  )
}
