import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'

test('renders the topology-first workflow shell', () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByText('AGV 调度仿真优化平台')).toBeInTheDocument()
  expect(screen.getByText('拓扑编辑器')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /保存并仿真|进入仿真/ })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '仿真工作台' })).not.toBeInTheDocument()
  expect(screen.queryByText('总控驾驶舱')).not.toBeInTheDocument()
  expect(screen.queryByText('场景配置台')).not.toBeInTheDocument()
  expect(screen.queryByText('算法实验室')).not.toBeInTheDocument()
  expect(screen.queryByText('回放与报告')).not.toBeInTheDocument()
})
