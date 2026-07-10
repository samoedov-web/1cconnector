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

const LICENSE_BADGE = { valid: 'ok', grace: 'warn', demo: 'warn', expired: 'err', invalid: 'err' }
const LICENSE_LABEL = {
  valid: 'действует',
  grace: 'льготный период',
  demo: 'деморежим',
  expired: 'истекла',
  invalid: 'повреждена',
}

function limit(used, max) {
  return max === null ? `${used} / без ограничений` : `${used} / ${max}`
}

function LicenseBlock({ lic }) {
  if (!lic) return null
  return (
    <div className="panel" style={{ marginTop: 0, marginBottom: 16 }}>
      <div className="row space-between">
        <h3 style={{ margin: 0 }}>
          Лицензия: пакет «{lic.tier_title}»
          <span className={`badge ${LICENSE_BADGE[lic.status] || 'off'}`}>
            {LICENSE_LABEL[lic.status] || lic.status}
          </span>
        </h3>
        <span className="muted">
          {lic.issued_to && `${lic.issued_to} · `}
          {lic.valid_until
            ? `до ${new Date(lic.valid_until).toLocaleDateString('ru-RU')}`
            : 'без файла лицензии'}
        </span>
      </div>
      <div className="kv" style={{ marginBottom: 0 }}>
        <span>Юр. лица</span><b>{limit(lic.usage.organizations, lic.usage.max_organizations)}</b>
        <span>Кошельки</span><b>{limit(lic.usage.wallets, lic.usage.max_wallets)}</b>
        <span>Обновления форм отчётности</span>
        <b>{lic.report_updates ? 'включены' : 'не входят в пакет'}</b>
        {!lic.sync_allowed && (<>
          <span>Синхронизация</span>
          <b className="error" style={{ padding: '2px 8px' }}>
            остановлена{lic.reason ? `: ${lic.reason}` : ''} — данные доступны для чтения
          </b>
        </>)}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [lic, setLic] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const load = () => {
      api('/api/v1/dashboard').then(setData).catch((e) => setError(e.message))
      api('/api/v1/dashboard/license').then(setLic).catch(() => {})
    }
    load()
    const timer = setInterval(load, 30_000)
    return () => clearInterval(timer)
  }, [])

  if (error) return <div className="error">{error}</div>
  if (!data) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Мониторинг</h1>
      <LicenseBlock lic={lic} />
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

      {data.alerts?.length > 0 && (
        <>
          <h2>Алерты</h2>
          <table>
            <thead>
              <tr><th>Время</th><th>Уровень</th><th>Событие</th><th>Webhook</th></tr>
            </thead>
            <tbody>
              {data.alerts.map((a, i) => (
                <tr key={i}>
                  <td>{a.at ? new Date(a.at).toLocaleString('ru-RU') : '—'}</td>
                  <td>
                    <span className={`badge ${a.severity === 'error' ? 'err' : 'warn'}`}>
                      {a.severity}
                    </span>
                  </td>
                  <td>{a.title}</td>
                  <td>{a.sent ? 'доставлен' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

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
