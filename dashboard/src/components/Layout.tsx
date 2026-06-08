import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/',          label: 'The Chamber',  icon: '⬡' },
  { to: '/portfolio', label: 'Portfolio',    icon: '◯' },
  { to: '/trades',    label: 'Trades',       icon: '⇄' },
  { to: '/scenarios', label: 'Scenarios',    icon: '◈' },
  { to: '/recon',     label: 'Recon',        icon: '≡' },
  { to: '/config',    label: 'Config',       icon: '⊕' },
]

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-52 flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col">
        <div className="px-4 py-4 border-b border-slate-800 flex items-center gap-3">
          <img src="/logo.svg" alt="The Delphic Ledger" className="w-10 h-7 flex-shrink-0" />
          <div>
            <div className="text-xs font-mono text-slate-500 uppercase tracking-widest leading-none">The Delphic</div>
            <div className="text-sm font-semibold text-white leading-tight">Ledger</div>
          </div>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-slate-700 text-white font-medium'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                }`
              }
            >
              <span className="text-base w-5 text-center">{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-slate-800">
          <p className="text-xs text-slate-600">Personal use only.</p>
          <p className="text-xs text-slate-600">Not investment advice.</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-slate-950 p-6">
        {children}
      </main>
    </div>
  )
}
