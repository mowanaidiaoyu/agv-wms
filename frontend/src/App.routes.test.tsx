import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./pages/TopologyPage', () => ({
  TopologyPage: () => <div>topology-page</div>,
}))

vi.mock('./pages/SimulationPage', () => ({
  SimulationPage: () => <div>unified-simulation-page</div>,
}))

function renderRoute(path: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('simulation routes', () => {
  it.each(['/simulation', '/simulation/orders'])(
    'uses the unified simulation workbench for %s',
    (path) => {
      renderRoute(path)
      expect(screen.getByText('unified-simulation-page')).toBeInTheDocument()
    },
  )
})
