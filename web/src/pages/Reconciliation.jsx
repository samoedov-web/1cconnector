import { useCallback, useEffect, useState } from 'react'
import { api, getAuth, openReport } from '../api'

const API = '/api/v1/custody/reconciliation'

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
  'missing_in_custody',
  'missing_on_chain',
  'amount_mismatch',
  'date_mismatch',
  'duplicate_suspect',
])

const badgeClass = (status) => {
  if (status === 'matched' || status === 'matched_aggregate') return 'ok'
  if (status === 'manual') return 'off'
  return 'err'
}

function localDate(offsetDays = 0) {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  return date.toISOString().slice(0, 10)
}

function RunDetail({ runId, onChanged }) {
  const [data, setData] = useState(null)
  const [resolving, setResolving] = useState(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState(null)

  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(async () => {
    setError(null)

    try {
      const result = await api(`${API}/runs/${runId}`)
      setData(result)
    } catch (err) {
      setError(err.message)
    }
  }, [runId])

  useEffect(() => {
    load()
  }, [load])

  const resolve = async (resultId) => {
    setError(null)

    try {
      await api(`${API}/results/${resultId}/resolve`, {
        method: 'POST',
        body: {
          note,
          ledger_tx_ids: [],
        },
      })

      setResolving(null)
      setNote('')

      await load()
      onChanged()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error) {
    return <div className="error">{error}</div>
  }

  if (!data) {
    return <div className="muted">Загрузка…</div>
  }

  return (
    <div className="panel">
      <div className="row space-between">
        <h3>
          Запуск №{data.run.id}{' '}
          {data.run.stale && (
            <span className="badge err">
              устарел (реорг)
            </span>
          )}
        </h3>

        <div className="row">
          <button
            className="secondary small"
            onClick={() =>
              openReport(
                `/api/v1/reports/custody-reconciliation/${runId}`,
                { format: 'html' },
              )
            }
          >
            Акт (печать)
          </button>
        </div>
      </div>

      <div className="row wrap">
        {data.summary.map((item) => (
          <span
            key={item.status}
            className={`badge ${badgeClass(item.status)}`}
          >
            {item.status_ru || STATUS_RU[item.status] || item.status}: {item.count}
          </span>
        ))}
      </div>

      <table style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>Статус</th>
            <th>Правило</th>
            <th>Блокчейн</th>
            <th>Выписка</th>
            <th>Пояснение</th>
            <th></th>
          </tr>
        </thead>

        <tbody>
          {data.rows.map((row) => (
            <tr key={row.result_id}>
              <td>
                <span className={`badge ${badgeClass(row.status)}`}>
                  {row.status_ru || STATUS_RU[row.status] || row.status}
                </span>
              </td>

              <td>{row.rule}</td>

              <td>
                {row.chain.map((item, index) => (
                  <div key={index}>
                    <code className="hash">{item.tx_hash || '—'}</code>{' '}
                    {item.amount} {item.asset}
                  </div>
                ))}

                {row.chain.length === 0 && '—'}
              </td>

              <td>
                {row.custody.map((item, index) => (
                  <div key={index}>
                    №{item.entry_id}: {item.amount} {item.asset}
                  </div>
                ))}

                {row.custody.length === 0 && '—'}
              </td>

              <td className="muted">
                {row.detail?.reason ||
                  (row.detail?.delta
                    ? `дельта ${row.detail.delta}`
                    : '')}
              </td>

              <td>
                {canEdit &&
                  DISCREPANCIES.has(row.status) &&
                  (resolving === row.result_id ? (
                    <div className="row">
                      <input
                        placeholder="Комментарий"
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                        style={{ minWidth: 120 }}
                      />

                      <button
                        className="small"
                        onClick={() => resolve(row.result_id)}
                      >
                        OK
                      </button>

                      <button
                        className="small secondary"
                        onClick={() => {
                          setResolving(null)
                          setNote('')
                        }}
                      >
                        ×
                      </button>
                    </div>
                  ) : (
                    <button
                      className="small secondary"
                      onClick={() => setResolving(row.result_id)}
                    >
                      Разобрать
                    </button>
                  ))}
              </td>
            </tr>
          ))}

          {data.rows.length === 0 && (
            <tr>
              <td colSpan="6" className="muted">
                Результатов нет
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default function Reconciliation() {
  const [sourceInfo, setSourceInfo] = useState(null)
  const [runs, setRuns] = useState([])
  const [openRun, setOpenRun] = useState(null)

  const [modeInfo, setModeInfo] = useState(null)

  const [form, setForm] = useState({
    date_from: localDate(-30),
    date_to: localDate(0),
    tolerance_abs: '0.01',
    tolerance_rel: '0',
    date_window_hours: 24,
    accept_aggregates: true,
  })

  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(async () => {
    try {
      const currentMode = await api(`${API}/mode`)
      setModeInfo(currentMode)

      if (currentMode.mode !== 'shadow') {
        setSourceInfo(null)
        setRuns([])
        return
      }

      const [source, history] = await Promise.all([
        api(`${API}/sources`),
        api(`${API}/runs`),
      ])

      setSourceInfo(source)
      setRuns(history)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const period = () => {
    if (!form.date_from || !form.date_to) {
      throw new Error('Укажите начало и конец периода')
    }

    if (form.date_from > form.date_to) {
      throw new Error('Дата начала периода не может быть позже даты окончания')
    }

    return {
      period_from: new Date(
        `${form.date_from}T00:00:00`,
      ).toISOString(),

      period_to: new Date(
        `${form.date_to}T23:59:59`,
      ).toISOString(),
    }
  }

  const fetchStatements = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)

    try {
      const result = await api(`${API}/fetch`, {
        method: 'POST',
        body: period(),
      })

      setNotice(
        `Загружено выписок: ${result.statements}`,
      )

      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const runReconciliation = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)

    try {
      const result = await api(`${API}/runs`, {
        method: 'POST',
        body: {
          source_id: sourceInfo?.active || undefined,
          ...period(),
          tolerance_abs: form.tolerance_abs || '0',
          tolerance_rel: form.tolerance_rel || '0',
          date_window_hours: Number(form.date_window_hours),
          accept_aggregates: form.accept_aggregates,
        },
      })

      setNotice(
        `Сверка №${result.id}: расхождений ${result.discrepancies ?? 0}`,
      )

      setOpenRun(result.id)

      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (modeInfo && modeInfo.mode !== 'shadow') {
    return (
      <>
        <h1>Сверка с депозитарием</h1>

        <div
          className="panel"
          style={{ marginTop: 0 }}
        >
          <span
            className={`badge ${
              modeInfo.mode === 'active'
                ? 'err'
                : 'off'
            }`}
          >
            custody_mode={modeInfo.mode}
          </span>

          <p>{modeInfo.message}</p>
        </div>
      </>
    )
  }

  return (
    <>
      <h1>Сверка с депозитарием</h1>

      {sourceInfo && (
        <p className="muted">
          Источник: {sourceInfo.active}
          {' · '}
          гранулярность {sourceInfo.capabilities.entry_granularity}
          {' · '}
          хэши: {sourceInfo.capabilities.has_tx_hash ? 'есть' : 'нет'}
          {' · '}
          состояние: {sourceInfo.health.status}
        </p>
      )}

      {error && (
        <div className="error">
          {error}
        </div>
      )}

      {notice && (
        <div
          className="panel"
          style={{ marginTop: 0 }}
        >
          {notice}
        </div>
      )}

      {canEdit && (
        <div
          className="panel"
          style={{ marginTop: 0 }}
        >
          <h3>Загрузка и запуск</h3>

          <div className="row wrap">
            <label>
              С даты
              <input
                type="date"
                value={form.date_from}
                onChange={(event) =>
                  setForm({
                    ...form,
                    date_from: event.target.value,
                  })
                }
              />
            </label>

            <label>
              По дату
              <input
                type="date"
                value={form.date_to}
                onChange={(event) =>
                  setForm({
                    ...form,
                    date_to: event.target.value,
                  })
                }
              />
            </label>

            <label>
              Допуск (абс.)
              <input
                value={form.tolerance_abs}
                onChange={(event) =>
                  setForm({
                    ...form,
                    tolerance_abs: event.target.value,
                  })
                }
              />
            </label>

            <label>
              Допуск (отн.)
              <input
                value={form.tolerance_rel}
                onChange={(event) =>
                  setForm({
                    ...form,
                    tolerance_rel: event.target.value,
                  })
                }
              />
            </label>

            <label>
              Окно, часов
              <input
                type="number"
                min="1"
                value={form.date_window_hours}
                onChange={(event) =>
                  setForm({
                    ...form,
                    date_window_hours: event.target.value,
                  })
                }
              />
            </label>

            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.accept_aggregates}
                onChange={(event) =>
                  setForm({
                    ...form,
                    accept_aggregates:
                      event.target.checked,
                  })
                }
              />

              Принимать агрегаты автоматически
            </label>
          </div>

          <div
            className="row"
            style={{ marginTop: 12 }}
          >
            <button
              className="secondary"
              disabled={busy}
              onClick={fetchStatements}
            >
              {busy
                ? 'Выполняется…'
                : 'Загрузить выписки'}
            </button>

            <button
              disabled={busy}
              onClick={runReconciliation}
            >
              {busy
                ? 'Выполняется…'
                : 'Запустить сверку'}
            </button>
          </div>

          {modeInfo?.mode === 'shadow' && (
            <p className="muted">
              {modeInfo.message}
            </p>
          )}
        </div>
      )}

      <div className="panel">
        <div className="row space-between">
          <h3>История запусков</h3>

          <button
            className="secondary small"
            onClick={load}
          >
            Обновить
          </button>
        </div>

        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Источник</th>
              <th>Период</th>
              <th>Статус</th>
              <th>Расхождения</th>
              <th></th>
            </tr>
          </thead>

          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.id}</td>

                <td>{run.source_id}</td>

                <td>
                  {new Date(
                    run.period_from,
                  ).toLocaleDateString()}
                  {' — '}
                  {new Date(
                    run.period_to,
                  ).toLocaleDateString()}
                </td>

                <td>
                  <span
                    className={`badge ${
                      run.stale ? 'err' : 'ok'
                    }`}
                  >
                    {run.stale
                      ? 'устарел'
                      : 'завершён'}
                  </span>
                </td>

                <td>
                  {run.discrepancies ?? '—'}
                </td>

                <td>
                  <button
                    className="small secondary"
                    onClick={() =>
                      setOpenRun(
                        openRun === run.id
                          ? null
                          : run.id,
                      )
                    }
                  >
                    {openRun === run.id
                      ? 'Скрыть'
                      : 'Открыть'}
                  </button>
                </td>
              </tr>
            ))}

            {runs.length === 0 && (
              <tr>
                <td
                  colSpan="6"
                  className="muted"
                >
                  Запусков не найдено
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {openRun && (
        <RunDetail
          runId={openRun}
          onChanged={load}
        />
      )}
    </>
  )
}
