import { useCallback, useEffect, useState } from 'react'
import { api, getAuth, openReport } from '../api'

export default function Accounting() {
  const [revaluations, setRevaluations] = useState([])
  const [revalDate, setRevalDate] = useState('')
  const [taxPeriod, setTaxPeriod] = useState({ date_from: '', date_to: '' })
  const [recon, setRecon] = useState({ counterparty_id: '', date_from: '', date_to: '' })
  const [counterparties, setCounterparties] = useState([])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const canRun = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(() => {
    api('/api/v1/accounting/revaluations').then(setRevaluations).catch((e) => setError(e.message))
    api('/api/v1/counterparties').then(setCounterparties).catch(() => {})
  }, [])
  useEffect(load, [load])

  const runRevaluation = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null); setNotice(null)
    try {
      const created = await api('/api/v1/accounting/revaluations', {
        method: 'POST',
        body: { as_of: new Date(revalDate + 'T23:59:59').toISOString() },
      })
      setNotice(created.length
        ? `Переоценка выполнена: активов — ${created.length}, документы поставлены в очередь обмена с 1С`
        : 'На эту дату переоценка уже проводилась либо остатки по партиям нулевые')
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const period = (p) => ({
    date_from: new Date(p.date_from).toISOString(),
    date_to: new Date(p.date_to + 'T23:59:59').toISOString(),
  })

  return (
    <>
      <h1>Учёт</h1>
      {error && <div className="error">{error}</div>}
      {notice && <div className="panel" style={{ marginTop: 0 }}>{notice}</div>}

      <div className="panel">
        <h3>Переоценка цифровой валюты на отчётную дату</h3>
        <p className="muted">
          Себестоимость партий ФИФО не меняется; формируется документ
          «Переоценка» с полной разницей и дельтой к предыдущей переоценке.
        </p>
        {canRun && (
          <form className="row wrap" onSubmit={runRevaluation}>
            <label>
              Отчётная дата
              <input type="date" value={revalDate} onChange={(e) => setRevalDate(e.target.value)} />
            </label>
            <button disabled={!revalDate || busy}>{busy ? 'Расчёт…' : 'Провести переоценку'}</button>
          </form>
        )}
        {revaluations.length > 0 && (
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Дата</th><th>Актив</th><th>Остаток</th>
                <th>Себестоимость, ₽</th><th>Оценка, ₽</th><th>Разница, ₽</th><th>Дельта, ₽</th>
              </tr>
            </thead>
            <tbody>
              {revaluations.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.as_of).toLocaleDateString('ru-RU')}</td>
                  <td>{r.asset}</td>
                  <td>{r.quantity}</td>
                  <td>{r.book_cost_rub}</td>
                  <td>{r.market_rub}</td>
                  <td>{r.difference_rub}</td>
                  <td>{r.delta_rub}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h3>Налоговый регистр операций с ЦВ</h3>
        <div className="row wrap">
          <label>С даты
            <input type="date" value={taxPeriod.date_from}
              onChange={(e) => setTaxPeriod({ ...taxPeriod, date_from: e.target.value })} />
          </label>
          <label>По дату
            <input type="date" value={taxPeriod.date_to}
              onChange={(e) => setTaxPeriod({ ...taxPeriod, date_to: e.target.value })} />
          </label>
        </div>
        <div className="row">
          <button className="secondary" type="button"
            disabled={!taxPeriod.date_from || !taxPeriod.date_to}
            onClick={() => openReport('/api/v1/reports/tax-register',
              { ...period(taxPeriod), format: 'html' }).catch((e) => setError(e.message))}>
            Открыть (печать)
          </button>
          <button className="secondary" type="button"
            disabled={!taxPeriod.date_from || !taxPeriod.date_to}
            onClick={() => openReport('/api/v1/reports/tax-register',
              { ...period(taxPeriod), format: 'xlsx' }, 'tax-register.xlsx').catch((e) => setError(e.message))}>
            Скачать XLSX
          </button>
        </div>
      </div>

      <div className="panel">
        <h3>Акт сверки с контрагентом (RU/EN)</h3>
        <div className="row wrap">
          <label>Контрагент
            <select value={recon.counterparty_id}
              onChange={(e) => setRecon({ ...recon, counterparty_id: e.target.value })}>
              <option value="">— выбрать —</option>
              {counterparties.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label>С даты
            <input type="date" value={recon.date_from}
              onChange={(e) => setRecon({ ...recon, date_from: e.target.value })} />
          </label>
          <label>По дату
            <input type="date" value={recon.date_to}
              onChange={(e) => setRecon({ ...recon, date_to: e.target.value })} />
          </label>
        </div>
        <div className="row">
          <button className="secondary" type="button"
            disabled={!recon.counterparty_id || !recon.date_from || !recon.date_to}
            onClick={() => openReport('/api/v1/reports/reconciliation',
              { counterparty_id: recon.counterparty_id, ...period(recon), format: 'html' })
              .catch((e) => setError(e.message))}>
            Открыть (печать)
          </button>
          <button className="secondary" type="button"
            disabled={!recon.counterparty_id || !recon.date_from || !recon.date_to}
            onClick={() => openReport('/api/v1/reports/reconciliation',
              { counterparty_id: recon.counterparty_id, ...period(recon), format: 'xlsx' },
              'reconciliation.xlsx').catch((e) => setError(e.message))}>
            Скачать XLSX
          </button>
        </div>
      </div>
    </>
  )
}
