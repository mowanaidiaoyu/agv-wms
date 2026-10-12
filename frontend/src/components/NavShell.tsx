import { clsx } from 'clsx'
import { Activity, Bot, BrainCircuit, LayoutDashboard, Map, PlayCircle, ScrollText } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const items = [
  { to: '/', label: '总控驾驶舱', icon: LayoutDashboard },
  { to: '/topology', label: '拓扑编辑器', icon: Map },
  { to: '/scenario', label: '场景配置台', icon: Bot },
  { to: '/simulation', label: '仿真工作台', icon: PlayCircle },
  { to: '/lab', label: '算法实验室', icon: BrainCircuit },
  { to: '/reports', label: '回放与报告', icon: ScrollText },
]

export function NavShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell notranslate" translate="no">
      <aside className="sidebar">
        <div className="brand-card">
          <div className="brand-mark">
            <Activity size={22} />
          </div>
          <div>
            <p className="eyebrow">Industrial Control Deck</p>
            <h1>AGV 调度仿真优化平台</h1>
          </div>
        </div>
        <nav className="nav-links">
          {items.map((item) => {
            const Icon = item.icon
            return (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => clsx('nav-link', isActive && 'nav-link-active')}>
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            )
          })}
        </nav>
      </aside>
      <main className="content-area">{children}</main>
    </div>
  )
}
