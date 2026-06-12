import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  ArrowUpDown,
  BarChart2,
  Upload,
  Settings,
  Bell,
  LogOut,
  User,
} from 'lucide-react'
import { get } from '../api/client'

interface IBKRStatus {
  enabled: boolean
  connected: boolean
}

const NAV_MAIN = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/trades', label: 'Trades', icon: ArrowUpDown, end: false },
  { to: '/import', label: 'Import', icon: Upload, end: false },
]

const NAV_BOTTOM = [
  { label: 'Settings', icon: Settings },
  { label: 'Notifications', icon: Bell },
]

export default function Layout() {
  const [ibkrStatus, setIbkrStatus] = useState<IBKRStatus | null>(null)
  const [logoError, setLogoError] = useState(false)

  useEffect(() => {
    get<IBKRStatus>('/ibkr/status').then(setIbkrStatus).catch(() => null)
  }, [])

  return (
    <div className="flex flex-col min-h-screen" style={{ backgroundColor: '#e8edf5' }}>
      {/* Top header bar */}
      <header
        className="flex items-center justify-between px-6 shrink-0"
        style={{
          backgroundColor: '#ffffff',
          borderBottom: '1px solid #e2e8f0',
          height: 64,
          zIndex: 10,
        }}
      >
        {/* Logo */}
        <div className="flex items-center">
          {!logoError ? (
            <img
              src="/logo.png"
              alt="DeKalb Capital"
              style={{ height: 36, maxWidth: 180, objectFit: 'contain' }}
              onError={() => setLogoError(true)}
            />
          ) : (
            <div className="flex items-center gap-2.5">
              <div
                className="flex items-center justify-center rounded font-bold text-sm"
                style={{
                  width: 36,
                  height: 36,
                  backgroundColor: '#1a2744',
                  color: '#ffffff',
                }}
              >
                DC
              </div>
              <div>
                <p className="font-bold text-sm leading-tight" style={{ color: '#1a2744' }}>
                  DeKalb Capital
                </p>
                <p className="text-xs tracking-wider uppercase" style={{ color: '#6b7a99', fontSize: 9 }}>
                  Management LLC
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Right: IBKR status + Account */}
        <div className="flex items-center gap-4">
          {ibkrStatus?.enabled && (
            <div
              className="flex items-center gap-1.5 text-xs font-medium"
              style={{ color: ibkrStatus.connected ? '#16a34a' : '#d97706' }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full inline-block"
                style={{ backgroundColor: ibkrStatus.connected ? '#16a34a' : '#d97706' }}
              />
              {ibkrStatus.connected ? 'IBKR Connected' : 'IBKR Connecting...'}
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium" style={{ color: '#374151' }}>Account</span>
            <div
              className="flex items-center justify-center rounded-full"
              style={{ width: 36, height: 36, backgroundColor: '#d1dce8' }}
            >
              <User size={18} color="#6b7a99" />
            </div>
          </div>
        </div>
      </header>

      {/* Body: sidebar + main */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside
          className="flex flex-col shrink-0 py-4"
          style={{
            width: 200,
            backgroundColor: '#ffffff',
            borderRight: '1px solid #e2e8f0',
          }}
        >
          {/* Main nav */}
          <nav className="flex flex-col gap-0.5 px-3 flex-1">
            {NAV_MAIN.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    isActive ? 'font-medium' : 'hover:bg-gray-50'
                  }`
                }
                style={({ isActive }) => ({
                  color: isActive ? '#2563eb' : '#374151',
                  backgroundColor: isActive ? 'rgba(37,99,235,0.07)' : undefined,
                })}
              >
                {({ isActive }) => (
                  <>
                    <Icon size={17} color={isActive ? '#2563eb' : '#9ca3af'} strokeWidth={1.8} />
                    {label}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* Bottom nav */}
          <div className="px-3 pt-3 flex flex-col gap-0.5" style={{ borderTop: '1px solid #f1f5f9' }}>
            {NAV_BOTTOM.map(({ label, icon: Icon }) => (
              <button
                key={label}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm w-full text-left hover:bg-gray-50 transition-colors"
                style={{ color: '#374151' }}
              >
                <Icon size={17} color="#9ca3af" strokeWidth={1.8} />
                {label}
              </button>
            ))}
            <button
              className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm w-full text-left hover:bg-gray-50 transition-colors"
              style={{ color: '#374151' }}
            >
              <LogOut size={17} color="#9ca3af" strokeWidth={1.8} />
              Sign out
            </button>
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
