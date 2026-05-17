import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/trades', label: 'Trades', end: false },
  { to: '/import', label: 'Import CSV', end: false },
]

export default function Layout() {
  const { user, signOut, config } = useAuth()
  const authEnabled = config?.auth_enabled ?? false

  return (
    <div className="flex min-h-screen bg-gray-950 text-gray-100">
      <aside className="w-52 shrink-0 border-r border-gray-800 flex flex-col py-6 px-3">
        <div className="px-3 mb-8">
          <p className="text-xs font-bold tracking-widest text-blue-400 uppercase mb-0.5">DeKalb Capital</p>
          <h1 className="text-base font-semibold text-white">Trade Tracker</h1>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) => `px-3 py-2 rounded-md text-sm transition-colors ${isActive ? 'bg-blue-600/20 text-blue-300 font-medium border border-blue-600/30' : 'text-gray-400 hover:text-white hover:bg-gray-800'}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-3 flex flex-col gap-3">
          {authEnabled && user && (
            <div className="flex items-center gap-2">
              {user.picture && <img src={user.picture} alt={user.name} className="w-7 h-7 rounded-full shrink-0" referrerPolicy="no-referrer" />}
              <div className="min-w-0">
                <p className="text-xs font-medium text-gray-200 truncate">{user.name}</p>
                <p className="text-xs text-gray-500 truncate">{user.email}</p>
              </div>
            </div>
          )}
          {authEnabled && user && <button onClick={signOut} className="text-xs text-gray-600 hover:text-red-400 transition-colors text-left">Sign out</button>}
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer" className="text-xs text-gray-600 hover:text-gray-400 transition-colors">API Docs →</a>
        </div>
      </aside>
      <main className="flex-1 overflow-auto"><Outlet /></main>
    </div>
  )
}
