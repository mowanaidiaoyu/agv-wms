import { Routes, Route } from 'react-router-dom'
import { NavShell } from './components/NavShell'
import { DashboardPage } from './pages/DashboardPage'
import { TopologyPage } from './pages/TopologyPage'
import { ScenarioPage } from './pages/ScenarioPage'
import { SimulationPage } from './pages/SimulationPage'
import { LabPage } from './pages/LabPage'
import { ReportsPage } from './pages/ReportsPage'
import { PathDemoPage } from './pages/PathDemoPage'

export default function App() {
  return (
    <NavShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/scenario" element={<ScenarioPage />} />
        <Route path="/simulation" element={<SimulationPage />} />
        <Route path="/lab" element={<LabPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/path-demo" element={<PathDemoPage />} />
      </Routes>
    </NavShell>
  )
}
