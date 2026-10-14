import { Routes, Route } from 'react-router-dom'
import { NavShell } from './components/NavShell'
import { DashboardPage } from './pages/DashboardPage'
import { TopologyPage } from './pages/TopologyPage'
import { ScenarioPage } from './pages/ScenarioPage'
import { SimulationPage } from './pages/SimulationPage'
import { LabPage } from './pages/LabPage'
import { ReportsPage } from './pages/ReportsPage'
import { PathDemoPage } from './pages/PathDemoPage'
import { MultiPathResultPage } from './pages/MultiPathResultPage'

export default function App() {
  return (
    <NavShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/scenario" element={<ScenarioPage />} />
        <Route path="/simulation" element={<SimulationPage />} />
        <Route path="/lab" element={<LabPage />} />
        <Route path="/lab/multi-path-result/:experimentId/:algorithm" element={<MultiPathResultPage />} />
        <Route path="/lab/multi-path-result/:algorithm" element={<MultiPathResultPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/multi-path/:experimentId" element={<ReportsPage />} />
        <Route path="/path-demo" element={<PathDemoPage />} />
      </Routes>
    </NavShell>
  )
}
