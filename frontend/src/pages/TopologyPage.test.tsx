import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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
    validateTopology: vi.fn(async () => ({ valid: true, errors: [], warnings: [] })),
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
      <TopologyPage />
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
    // 右侧检查器选项卡
    expect(screen.getByRole('tab', { name: '属性' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '校验' })).toBeInTheDocument()

    // 左侧对象调色板分组标题
    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('区域（拉矩形）')).toBeInTheDocument()

    // 右侧属性面板默认空态
    expect(screen.getByText('未选择对象')).toBeInTheDocument()

    // 不再渲染旧 iframe
    expect(container.querySelector('iframe')).toBeNull()
    expect(screen.queryByTitle('Legacy topology editor')).not.toBeInTheDocument()

    // 画布是 3D 场景，而非 2D 棋盘（无「适配 / 100%」控件）
    expect(await screen.findByTestId('topology-3d')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /适配全图|适配/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /100%/ })).not.toBeInTheDocument()
  })
})
