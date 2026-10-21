import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { TopologyPage } from './TopologyPage'

vi.mock('../../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => []),
    getTopology: vi.fn(async () => {
      throw new Error('unused')
    }),
    saveTopology: vi.fn(async () => ({})),
    deleteTopology: vi.fn(async () => ({})),
  },
}))

// jsdom 无 WebGL，three/r3f 无法渲染，测试用桩替代 3D 场景
vi.mock('../components/topology/Topology3DScene', () => ({
  default: () => <div data-testid="topology-3d" />,
}))

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TopologyPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('TopologyPage', () => {
  it('renders the v1 3D editor shell (no legacy iframe, no 2D board)', async () => {
    const { container } = renderPage()

    // 工具栏与画布骨架
    expect(screen.getByLabelText('拓扑名称')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存拓扑/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /新建/ })).toBeInTheDocument()

    // 左侧工具坞默认收起，仍保留短标签，并可按需展开
    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('区域（拉矩形）')).toBeInTheDocument()
    const paletteToggle = screen.getByRole('button', { name: '展开编辑工具' })
    expect(screen.getAllByText('选择')).toHaveLength(2)
    fireEvent.click(paletteToggle)
    expect(screen.getByRole('button', { name: '收起编辑工具' })).toBeInTheDocument()

    // 属性面板默认收起，点击画布上的按钮后以抽屉显示
    expect(screen.getByText('未选择对象')).toBeInTheDocument()
    const inspectorTrigger = screen.getByRole('button', { name: '属性' })
    expect(inspectorTrigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(inspectorTrigger)
    expect(inspectorTrigger).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: '关闭属性面板' }))
    expect(inspectorTrigger).toHaveAttribute('aria-expanded', 'false')

    // 不再渲染旧 iframe
    expect(container.querySelector('iframe')).toBeNull()
    expect(screen.queryByTitle('Legacy topology editor')).not.toBeInTheDocument()

    // 画布是 3D 场景，而非 2D 棋盘（无「适配 / 100%」控件）
    expect(await screen.findByTestId('topology-3d')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /适配全图|适配/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /100%/ })).not.toBeInTheDocument()
  })
})
