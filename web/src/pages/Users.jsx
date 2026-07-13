import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const ROLE_LABEL = { admin: 'администратор', operator: 'оператор', auditor: 'аудитор (чтение)' }

export default function Users() {
  const [users, setUsers] = useState(null)
  const [form, setForm] = useState({ username: '', password: '', role: 'operator' })
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    api('/api/v1/users').then(setUsers).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  const run = async (fn) => {
    setError(null)
    try {
      await fn()
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  const create = (e) => {
    e.preventDefault()
    run(async () => {
      await api('/api/v1/users', { method: 'POST', body: form })
      setForm({ username: '', password: '', role: 'operator' })
    })
  }

  const patch = (id, body) => run(() => api(`/api/v1/users/${id}`, { method: 'PATCH', body }))

  const resetPassword = (id) => {
    const password = prompt('Новый пароль (минимум 8 символов):')
    if (password) patch(id, { password })
  }

  if (!users) return <div className="muted">Загрузка…</div>

  return (
    <>
      <h1>Пользователи</h1>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr><th>Логин</th><th>Роль</th><th>Статус</th><th>Действия</th></tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}</td>
              <td>
                <select value={u.role} onChange={(e) => patch(u.id, { role: e.target.value })}>
                  {Object.entries(ROLE_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </td>
              <td>
                <span className={`badge ${u.enabled ? 'ok' : 'err'}`}>
                  {u.enabled ? 'активен' : 'отключён'}
                </span>
              </td>
              <td className="row">
                <button className="small secondary" onClick={() => patch(u.id, { enabled: !u.enabled })}>
                  {u.enabled ? 'Отключить' : 'Включить'}
                </button>
                <button className="small secondary" onClick={() => resetPassword(u.id)}>
                  Сбросить пароль
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="panel" onSubmit={create}>
        <h3>Новый пользователь</h3>
        <div className="row wrap">
          <label>
            Логин
            <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </label>
          <label>
            Пароль (мин. 8)
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </label>
          <label>
            Роль
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {Object.entries(ROLE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
        </div>
        <button disabled={form.username.length < 3 || form.password.length < 8}>Создать</button>
      </form>
    </>
  )
}
