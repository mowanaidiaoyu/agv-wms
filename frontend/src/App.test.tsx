import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'

test('renders platform navigation shell', () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByText('AGV 调度仿真优化平台')).toBeInTheDocument()
  expect(screen.getByText('拓扑编辑器')).toBeInTheDocument()
})
