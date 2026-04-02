import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { get } from '../api/client'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/trades', label: 'Trades', end: false },
  { to: '/import', label: 'Import CSV', end: false },
]

interface IBKRStatus {
  enabled: boolean
  connected: boolean
}

export default function Layout() {
  const [ibkrStatus, setIbkrStatus] = useState<IBKRStatus | null>(null)
  const [logoError, setLogoError] = useState(false)

  useEffect(() => {
    get<IBKRStatus>('/ibkr/status').then(setIbkrStatus).catch(() => null)
  }, [])

  return (
    <div className="flex min-h-screen text-gray-100" style={{ backgroundColor: '#07080d' }}>
      {/* Sidebar */}
      <aside
        className="w-52 shrink-0 flex flex-col py-5 px-3"
        style={{ backgroundColor: '#0a0d14', borderRight: '1px solid #1a2030' }}
      >
        {/* Logo / Brand */}
        <div className="px-2 mb-8">
          {!logoError ? (
            <div className="bg-white rounded-lg p-2 mb-1">
              <img
                src="/logo.png"
                alt="DeKalb Capital"
                className="w-full object-contain"
                style={{ maxHeight: 44 }}
                onError={() => setLogoError(true)}
              />
            </div>
          ) : (
            <>
              <p className="text-xs font-bold tracking-widest uppercase mb-0.5" style={{ color: '#4f7dc8' }}>
                DeKalb Capital
              </p>
              <h1 className="text-base font-semibold text-white">Trade Tracker</h1>
            </>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'text-white font-medium'
                    : 'text-gray-400 hover:text-white hover:bg-white/5'
                }`
              }
              style={({ isActive }) =>
                isActive
                  ? { backgroundColor: 'rgba(59,130,246,0.15)', color: '#fff' }
                  : {}
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        {/* IBKR status */}
        {ibkrStatus?.enabled && (
          <div className="mt-6 px-3">
            <div
              className="flex items-center gap-1.5 text-xs"
              style={{ color: ibkrStatus.connected ? '#4ade80' : '#eab308' }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full inline-block"
                style={{ backgroundColor: ibkrStatus.connected ? '#4ade80' : '#eab308' }}
              />
              {ibkrStatus.connected ? 'IBKR Connected' : 'IBKR Connecting...'}
            </div>
          </div>
        )}

        <div className="mt-auto px-3">
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="text-xs transition-colors"
            style={{ color: '#374151' }}
            onMouseOver={e => (e.currentTarget.style.color = '#6b7280')}
            onMouseOut={e => (e.currentTarget.style.color = '#374151')}
          >
            API Docs →
          </a>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
