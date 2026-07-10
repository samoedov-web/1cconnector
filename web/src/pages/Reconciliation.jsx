import { useCallback, useEffect, useState } from 'react'
import { api, getAuth, openReport } from '../api'

const STATUS_RU = {
  matched: 'совпало',
  matched_aggregate: 'совпало (агрегат)',
  missing_in_custody: 'нет в выписке',
  missing_on_chain: 'нет в цепочке',
  amount_mismatch: 'расхождение суммы',
  date_mismatch: 'расхождение даты',
  duplicate_suspect: 'подозрение на дубль',
  manual: 'разобрано вручную',
}
const DISCREPANCIES = new Set([
  'missing_in_custody', 'missing_on_chain', 'amount_mismatch',
  'date_mismatch', 'duplicate_suspect',
])
const BADGE = (s) =>
  s === 'matched' || s === 'matched_aggregate' ? 'ok' : s === 'manual' ? 'off' : 'err'

function RunDetail({ runId, onChanged }) {
  const [data, setData] = useState(null)
  const [resolving, setResolving] = useState(null) // result_id
  const [note, setNote] = useState('')
  const [error, setError] = useState(null)
  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(() => {
    api(`/api/v1/reconciliation/runs/${runId}`).then(setData).catch((e) => setError(e.message))
  }, [runId])
  useEffect(load, [load])

  const resolve = async (resultId) => {
    setError(null)
    try {
      await api(`/api/v1/reconciliation/results/${resultId}/resolve`, {
        method: 'POST',
        body: { note },
      })
      setResolving(null)
      setNote('')
      load()
      onChanged()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error) return <div className="error">{error}</div>
  if (!data) return <div className="muted">Загрузка…</div>

  return (
    <div className="panel">
      <div className="row space-between">
        <h3>
          Запуск №{data.run.id}
          {data.run.stale && <span className="badge err">устарел (реорг)</span>}
        </h3>
        <div className="row">
          <button className="secondary small"
            onClick={() => openReport(`/api/v1/reports/custody-reconciliation/${runId}`, { format: 'html' })}>
            Акт (печать)
          </button>
          <button className="secondary small"
            onClick={() => openReport(`/api/v1/reports/custody-reconciliation/${runId}`,
              { format: 'xlsx' }, `custody-reconciliation-${runId}.xlsx`)}>
            Акт (XLSX)
          </button>
        </div>
      </div>
      <div className="row wrap">
        {data.summary.map((s) => (
          <span key={s.status} className={`badge ${BADGE(s.status)}`}>
            {s.status_ru}: {s.count}
          </span>
        ))}
      </div>
      <table style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>Статус</th><th>Правило</th><th>Блокчейн</th><th>Выписка</th><th>Пояснение</th><th></th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.result_id}>
              <td><span className={`badge ${BADGE(row.status)}`}>{row.status_ru}</span></td>
              <td>{row.rule}</td>
              <td>
                {row.chain.map((c, i) => (
                  <div key={i}><code className="hash">{c.tx_hash}</code> {c.amount} {c.asset}</div>
                ))}
                {row.chain.length === 0 && '—'}
              </td>
              <td>
                {row.custody.map((c, i) => (
                  <div key={i}>№{c.entry_id}: {c.amount} {c.asset}</div>
                ))}
                {row.custody.length === 0 && '—'}
              </td>
              <td className="muted">{row.detail.reason || (row.detail.delta && `дельта ${row.detail.delta}`) || ''}</td>
              <td>
                {canEdit && DISCREPANCIES.has(row.status) && (
                  resolving === row.result_id ? (
                    <div className="row">
                      <input placeholder="Комментарий" value={note}
                        onChange={(e) => setNote(e.target.value)} style={{ minWidth: 120 }} />
                      <button className="small" onClick={() => resolve(row.result_id)}>OK</button>
                      <button className="small secondary" onClick={() => setResolving(null)}>×</button>
                    </div>
                  ) : (
                    <button className="small secondary" onClick={() => setResolving(row.result_id)}>
                      Разобрать
                    </button>
                  )
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Reconciliation() {
  const [mode, setMode] = useState(null)
  const [sourceInfo, setSourceInfo] = useState(null)
  const [runs, setRuns] = useState([])
  const [openRun, setOpenRun] = useState(null)
  const [form, setForm] = useState({
    date_from: '', date_to: '', tolerance_abs: '0', date_window_hours: 24, accept_aggregates: true,
  })
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(() => {
    api('/api/v1/reconciliation/mode').then((m) => {
      setMode(m)
      if (m.mode !== 'shadow') return
      api('/api/v1/reconciliation/sources').then(setSourceInfo).catch((e) => setError(e.message))
      api('/api/v1/reconciliation/runs').then(setRuns).catch((e) => setError(e.message))
    }).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  const period = () => ({
    period_from: new Date(form.date_from).toISOString(),
    period_to: new Date(form.date_to + 'T23:59:59').toISOString(),
  })

  const fetchStatements = async () => {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api('/api/v1/reconciliation/fetch', { method: 'POST', body: period() })
      setNotice(`Загружено выписок: ${r.statements}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const runReconciliation = async () => {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api('/api/v1/reconciliation/runs', {
        method: 'POST',
        body: {
          ...period(),
          tolerance_abs: form.tolerance_abs || '0',
          date_window_hours: Number(form.date_window_hours),
          accept_aggregates: form.accept_aggregates,
        },
      })
      setNotice(`Сверка №${r.id}: расхождений ${r.discrepancies}`)
      setOpenRun(r.id)
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (mode && mode.mode !== 'shadow') {
    return (
      <>
        <h1>Сверка с депозитарием</h1>
        <div className="panel" style={{ marginTop: 0 }}>
          <span className={`badge ${mode.mode === 'active' ? 'err' : 'off'}`}>
            custody_mode={mode.mode}
          </span>
          <p>{mode.message}</p>
        </div>
      </>
    )
  }

  return (
    <>
      <h1>Сверка с депозитарием</h1>
      {sourceInfo && (
        <p className="muted">
          Источник: {sourceInfo.active} · гранулярность {sourceInfo.capabilities.entry_granularity} ·
          хэши: {sourceInfo.capabilities.has_tx_hash ? 'есть' : 'нет'} ·
          состояние: {sourceInfo.health.status}
        </p>
      )}
      {error && <div className="error">{error}</div>}
      {notice && <div className="panel" style={{ marginTop: 0 }}>{notice}</div>}

      {canEdit && (
        <div className="panel" style={{ marginTop: 0 }}>
          <h3>Загрузка и запуск</h3>
          <div className="row wrap">
            <label>С даты
              <input type="date" value={form.date_from}
                onChange={(e) => setForm({ ...form, date_from: e.target.value })} />
            </label>
            <label>По дату
              <input type="date" value={form.date_to}
                onChange={(e) => setForm({ ...form, date_to: e.target.value })} />
            </label>
            <label>Допуск (абс.)
              <input value={form.tolerance_abs}
                onChange={(e) => setForm({ ...form, tolerance_abs: e.target.value })} />
            </label>
            <label>Окно, часов
              <input type="number" min="1" value={form.date_window_hours}
                onChange={(e) => setForm({ ...form, date_window_hours: e.target.value })} />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={form.accept_aggregates}
                onChange={(e) => setForm({ ...form, accept_aggregates: e.target.checked })} />
              Принимать агрегаты автоматически
            </label>
          </div>
          <div className="row">
            <button className="secondary" disabled={busy || !form.date_from || !form.date_to}
              onClick={fetchStatements}>
              1. Загрузить выписки
            </button>
            <button disabled={busy || !form.date_from || !form.date_to} onClick={runReconciliation}>
              2. Запустить сверку
            </button>
          </div>
        </div>
      )}

      <h2>Запуски</h2>
      <table>
        <thead>
          <tr><th>№</th><th>Период</th><th>Запущен</th><th>Расхождений</th><th>Статус</th><th></th></tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>{run.id}</td>
              <td>{run.period_from.slice(0, 10)} — {run.period_to.slice(0, 10)}</td>
              <td>{run.started_at ? new Date(run.started_at).toLocaleString('ru-RU') : '—'}</td>
              <td>
                <span className={`badge ${run.discrepancies ? 'err' : 'ok'}`}>
                  {run.discrepancies}
                </span>
              </td>
              <td>{run.stale ? <span className="badge err">устарел (реорг)</span> : <span className="badge ok">актуален</span>}</td>
              <td>
                <button className="small secondary"
                  onClick={() => setOpenRun(openRun === run.id ? null : run.id)}>
                  {openRun === run.id ? 'Скрыть' : 'Открыть'}
                </button>
              </td>
            </tr>
          ))}
          {runs.length === 0 && <tr><td colSpan={6} className="muted">Сверки ещё не запускались</td></tr>}
        </tbody>
      </table>
      {openRun && <RunDetail runId={openRun} onChanged={load} />}
    </>
  )
}
