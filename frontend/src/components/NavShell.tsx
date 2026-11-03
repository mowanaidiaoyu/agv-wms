import { Activity } from 'lucide-react'

export function NavShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell notranslate" translate="no">
      <header className="app-header">
        <div className="app-header-brand">
          <div className="app-header-mark">
            <Activity size={19} />
          </div>
          <div>
            <p className="eyebrow">Industrial Control Deck</p>
            <strong>AGV 调度仿真优化平台</strong>
          </div>
        </div>
        <span className="app-header-flow">拓扑设计 → 仿真验证</span>
      </header>
      <main className="content-area">{children}</main>
    </div>
  )
}
