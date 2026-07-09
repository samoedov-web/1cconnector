import { useCallback, useEffect, useState } from 'react'
import { api, getAuth } from '../api'

const INVOICE_LABEL = {
  open: 'открыт',
  partially_paid: 'частично оплачен',
  paid: 'оплачен',
  cancelled: 'отменён',
}

function Detail({ id, onChanged }) {
  const [detail, setDetail] = useState(null)
  const [address, setAddress] = useState({ network_code: 'tron', address: '' })
  const [error, setError] = useState(null)
  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(() => {
    api(`/api/v1/counterparties/${id}`).then(setDetail).catch((e) => setError(e.message))
  }, [id])
  useEffect(load, [load])

  const addAddress = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      await api(`/api/v1/counterparties/${id}/addresses`, { method: 'POST', body: address })
      setAddress({ ...address, address: '' })
      load()
      onChanged()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error) return <div className="error">{error}</div>
  if (!detail) return <div className="muted">Загрузка…</div>

  return (
    <div className="detail">
      <h4>Адреса {detail.onec_ref && <span className="muted">GUID 1С: {detail.onec_ref}</span>}</h4>
      {detail.addresses.length === 0 && <p className="muted">Адресов нет — автоматчинг по правилу №1 не сработает.</p>}
      <ul className="plain">
        {detail.addresses.map((a) => (
          <li key={a.id}>
            <code>{a.address}</code> · {a.network}
            <span className={`badge ${a.origin === 'learned' ? 'warn' : 'off'}`}>
              {a.origin === 'learned' ? 'запомнен при разборе' : 'вручную'}
            </span>
          </li>
        ))}
      </ul>
      {canEdit && (
        <form className="row wrap" onSubmit={addAddress}>
          <select
            value={address.network_code}
            onChange={(e) => setAddress({ ...address, network_code: e.target.value })}
          >
            <option value="tron">tron</option>
            <option value="ethereum">ethereum</option>
          </select>
          <input
            placeholder="Адрес кошелька контрагента"
            value={address.address}
            onChange={(e) => setAddress({ ...address, address: e.target.value })}
          />
          <button className="small" disabled={!address.address}>Добавить адрес</button>
        </form>
      )}

      <h4>Договоры</h4>
      {detail.contracts.length === 0 && <p className="muted">Договоров нет (появятся из синхронизации с 1С).</p>}
      {detail.contracts.map((c) => (
        <div className="contract" key={c.id}>
          <b>{c.number}</b> · {c.currency}
          {c.registration_number && <> · уч. № {c.registration_number}</>}
          {c.onec_ref && <span className="muted"> · GUID {c.onec_ref}</span>}
          {c.invoices.length > 0 && (
            <table className="inner">
              <thead>
                <tr><th>Инвойс</th><th>Сумма</th><th>Оплачено</th><th>Окно оплаты</th><th>Статус</th></tr>
              </thead>
              <tbody>
                {c.invoices.map((i) => (
                  <tr key={i.id}>
                    <td>{i.number}</td>
                    <td>{i.amount} {i.currency}</td>
                    <td>{i.paid_amount}</td>
                    <td>
                      {i.due_from ? new Date(i.due_from).toLocaleDateString('ru-RU') : '…'}
                      {' — '}
                      {i.due_to ? new Date(i.due_to).toLocaleDateString('ru-RU') : '…'}
                    </td>
                    <td><span className={`badge ${i.status === 'paid' ? 'ok' : 'off'}`}>{INVOICE_LABEL[i.status]}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  )
}

export default function Counterparties() {
  const [rows, setRows] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const canEdit = ['admin', 'operator'].includes(getAuth()?.role)

  const load = useCallback(() => {
    api('/api/v1/counterparties').then(setRows).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  const create = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      await api('/api/v1/counterparties', { method: 'POST', body: { name } })
      setName('')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (!rows) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Контрагенты</h1>
      <p className="muted">
        Основной источник — синхронизация из 1С; здесь можно завести контрагента
        и его адреса вручную до подключения обмена.
      </p>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr><th>Наименование</th><th>Адресов</th><th>Договоров</th><th>Связь с 1С</th><th></th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td>{c.addresses}</td>
              <td>{c.contracts}</td>
              <td>{c.onec_ref ? <span className="badge ok">синхронизирован</span> : <span className="badge off">локальный</span>}</td>
              <td>
                <button className="small secondary" onClick={() => setOpenId(openId === c.id ? null : c.id)}>
                  {openId === c.id ? 'Скрыть' : 'Открыть'}
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={5} className="muted">Контрагентов пока нет</td></tr>}
        </tbody>
      </table>
      {openId && <Detail id={openId} onChanged={load} />}

      {canEdit && (
        <form className="panel" onSubmit={create}>
          <h3>Новый контрагент</h3>
          <div className="row">
            <input
              placeholder="Наименование"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={{ flex: 1 }}
            />
            <button disabled={!name}>Создать</button>
          </div>
        </form>
      )}
    </>
  )
}
