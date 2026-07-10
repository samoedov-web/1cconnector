import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { getAuth, setAuth } from './api'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Queue from './pages/Queue'
import Transactions from './pages/Transactions'
import Wallets from './pages/Wallets'
import Counterparties from './pages/Counterparties'
import Accounting from './pages/Accounting'
import Users from './pages/Users'

const NAV = [
  { to: '/', label: 'Мониторинг', end: true },
  { to: '/queue', label: 'Очередь разбора' },
  { to: '/transactions', label: 'Транзакции' },
  { to: '/wallets', label: 'Кошельки' },
  { to: '/counterparties', label: 'Контрагенты' },
  { to: '/accounting', label: 'Учёт' },
  { to: '/users', label: 'Пользователи', adminOnly: true },
]

function Layout({ children }) {
  const auth = getAuth()
  const navigate = useNavigate()
  const logout = () => {
    setAuth(null)
    navigate('/login')
  }
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Коннектор
          <span>Блокчейн → 1С</span>
        </div>
        <nav>
          {NAV.filter((item) => !item.adminOnly || auth.role === 'admin').map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            {auth.username} · {auth.role}
          </div>
          <button className="link" onClick={logout}>
            Выйти
          </button>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  )
}

function Protected({ children }) {
  return getAuth() ? <Layout>{children}</Layout> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Protected><Dashboard /></Protected>} />
      <Route path="/queue" element={<Protected><Queue /></Protected>} />
      <Route path="/transactions" element={<Protected><Transactions /></Protected>} />
      <Route path="/wallets" element={<Protected><Wallets /></Protected>} />
      <Route path="/counterparties" element={<Protected><Counterparties /></Protected>} />
      <Route path="/accounting" element={<Protected><Accounting /></Protected>} />
      <Route path="/users" element={<Protected><Users /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
