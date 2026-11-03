import { Navigate, Route, Routes } from 'react-router-dom'
import { NavShell } from './components/NavShell'
import { TopologyPage } from './pages/TopologyPage'
import { SimulationPage } from './pages/SimulationPage'

export default function App() {
  return (
    <NavShell>
      <Routes>
        <Route path="/" element={<Navigate to="/topology" replace />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/simulation" element={<SimulationPage />} />
        <Route path="/simulation/orders" element={<SimulationPage />} />
        <Route path="*" element={<Navigate to="/topology" replace />} />
      </Routes>
    </NavShell>
  )
}
