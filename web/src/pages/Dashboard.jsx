import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

const TX_LABELS = {
  seen: 'Замечены',
  confirmed: 'Подтверждаются',
  final: 'Финальные',
  orphaned: 'Выпали (реорг)',
}
const DOC_LABELS = {
  draft: 'Ждут выгрузки',
  exported: 'Забраны 1С',
  acked: 'Подтверждены 1С',
}

function age(iso) {
  if (!iso) return '—'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'только что'
  if (minutes < 60) return `${minutes} мин назад`
  return `${Math.round(minutes / 60)} ч назад`
}

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api('/api/v1/dashboard').then(setData).catch((e) => setError(e.message))
    const timer = setInterval(
      () => api('/api/v1/dashboard').then(setData).catch(() => {}),
      30_000,
    )
    return () => clearInterval(timer)
  }, [])

  if (error) return <div className="error">{error}</div>
  if (!data) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Мониторинг</h1>
      <div className="cards">
        {Object.entries(TX_LABELS).map(([key, label]) => (
          <div className="card" key={key}>
            <div className="card-value">{data.transactions[key] ?? 0}</div>
            <div className="card-label">{label}</div>
          </div>
        ))}
        <Link to="/queue" className="card accent">
          <div className="card-value">{data.pending_matches}</div>
          <div className="card-label">В очереди разбора</div>
        </Link>
      </div>

      <h2>Обмен с 1С</h2>
      <div className="cards">
        {Object.entries(DOC_LABELS).map(([key, label]) => (
          <div className="card" key={key}>
            <div className="card-value">{data.onec_documents[key] ?? 0}</div>
            <div className="card-label">{label}</div>
          </div>
        ))}
      </div>

      <h2>Сети</h2>
      <table>
        <thead>
          <tr>
            <th>Сеть</th>
            <th>Порог финальности</th>
            <th>Кошельков</th>
            <th>Последний скан</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {data.networks.map((n) => (
            <tr key={n.code}>
              <td>{n.name} ({n.code})</td>
              <td>{n.finality_depth}</td>
              <td>{n.wallets}</td>
              <td>{age(n.last_scanned_at)}</td>
              <td>
                <span className={`badge ${n.enabled ? 'ok' : 'off'}`}>
                  {n.enabled ? 'активна' : 'выключена'}
                </span>
              </td>
            </tr>
          ))}
          {data.networks.length === 0 && (
            <tr><td colSpan={5} className="muted">Сети не настроены</td></tr>
          )}
        </tbody>
      </table>
    </>
  )
}
