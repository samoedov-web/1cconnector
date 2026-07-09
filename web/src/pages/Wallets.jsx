import { useCallback, useEffect, useState } from 'react'
import { api, getAuth, openReport } from '../api'

export default function Wallets() {
  const [wallets, setWallets] = useState(null)
  const [form, setForm] = useState({ network_code: 'tron', address: '', label: '', backfill_from: '' })
  const [journal, setJournal] = useState({ wallet_id: '', date_from: '', date_to: '' })
  const [error, setError] = useState(null)
  const isAdmin = getAuth()?.role === 'admin'

  const load = useCallback(() => {
    api('/api/v1/wallets').then(setWallets).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  const add = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      await api('/api/v1/wallets', {
        method: 'POST',
        body: {
          network_code: form.network_code,
          address: form.address,
          label: form.label,
          backfill_from: form.backfill_from ? new Date(form.backfill_from).toISOString() : null,
        },
      })
      setForm({ ...form, address: '', label: '', backfill_from: '' })
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  const printJournal = (format) => {
    const filename = format === 'xlsx' ? `journal-${journal.wallet_id}.xlsx` : undefined
    openReport('/api/v1/reports/journal', {
      wallet_id: journal.wallet_id,
      date_from: new Date(journal.date_from).toISOString(),
      date_to: new Date(journal.date_to + 'T23:59:59').toISOString(),
      format,
    }, filename).catch((e) => setError(e.message))
  }

  if (!wallets) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Кошельки</h1>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>Сеть</th><th>Адрес</th><th>Метка</th><th>Последний скан</th><th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {wallets.map((w) => (
            <tr key={w.id}>
              <td>{w.network_code}</td>
              <td><code>{w.address}</code></td>
              <td>{w.label}</td>
              <td>{w.last_scanned_at ? new Date(w.last_scanned_at).toLocaleString('ru-RU') : 'ещё не сканировался'}</td>
              <td><span className={`badge ${w.enabled ? 'ok' : 'off'}`}>{w.enabled ? 'активен' : 'выключен'}</span></td>
            </tr>
          ))}
          {wallets.length === 0 && <tr><td colSpan={5} className="muted">Кошельки не добавлены</td></tr>}
        </tbody>
      </table>

      {isAdmin && (
        <form className="panel" onSubmit={add}>
          <h3>Добавить кошелёк</h3>
          <div className="row wrap">
            <label>
              Сеть
              <select
                value={form.network_code}
                onChange={(e) => setForm({ ...form, network_code: e.target.value })}
              >
                <option value="tron">tron</option>
                <option value="ethereum">ethereum</option>
              </select>
            </label>
            <label>
              Адрес *
              <input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} required />
            </label>
            <label>
              Метка
              <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </label>
            <label>
              Бэкфилл с даты
              <input
                type="date"
                value={form.backfill_from}
                onChange={(e) => setForm({ ...form, backfill_from: e.target.value })}
              />
            </label>
          </div>
          <button disabled={!form.address}>Добавить</button>
        </form>
      )}

      <div className="panel">
        <h3>Журнал операций по адресу (печатная форма)</h3>
        <div className="row wrap">
          <label>
            Кошелёк
            <select value={journal.wallet_id} onChange={(e) => setJournal({ ...journal, wallet_id: e.target.value })}>
              <option value="">— выбрать —</option>
              {wallets.map((w) => (
                <option key={w.id} value={w.id}>{w.network_code}: {w.label || w.address}</option>
              ))}
            </select>
          </label>
          <label>
            С даты
            <input type="date" value={journal.date_from} onChange={(e) => setJournal({ ...journal, date_from: e.target.value })} />
          </label>
          <label>
            По дату
            <input type="date" value={journal.date_to} onChange={(e) => setJournal({ ...journal, date_to: e.target.value })} />
          </label>
        </div>
        <div className="row">
          <button
            className="secondary"
            disabled={!journal.wallet_id || !journal.date_from || !journal.date_to}
            onClick={() => printJournal('html')}
            type="button"
          >
            Открыть (печать)
          </button>
          <button
            className="secondary"
            disabled={!journal.wallet_id || !journal.date_from || !journal.date_to}
            onClick={() => printJournal('xlsx')}
            type="button"
          >
            Скачать XLSX
          </button>
        </div>
      </div>
    </>
  )
}
