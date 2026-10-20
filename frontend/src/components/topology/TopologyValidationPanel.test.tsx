import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import type { TopologyValidationIssue } from '../../types'
import { TopologyValidationPanel } from './TopologyValidationPanel'

const originalState = useTopologyEditorStore.getState()

afterEach(() => {
  act(() => useTopologyEditorStore.setState(originalState, true))
})

describe('TopologyValidationPanel', () => {
  it('groups, expands, paginates, and locates structured validation issues', () => {
    const issues: TopologyValidationIssue[] = Array.from({ length: 12 }, (_, index) => ({
      category: 'accessibility',
      severity: 'warning',
      code: 'ACCESS_UNCONFIGURED',
      message: `库位 storage-${index} 尚未配置合法停靠点。`,
      entity_kind: 'node',
      entity_id: `storage-${index}`,
    }))
    useTopologyEditorStore.setState({
      validating: false,
      validation: {
        valid: true,
        errors: [],
        warnings: issues.map((issue) => issue.message),
        categories: [
          { category: 'structure', label: '结构完整性', error_count: 0, warning_count: 0, issues: [] },
          { category: 'accessibility', label: '业务可达性', error_count: 0, warning_count: 12, issues },
          { category: 'runtime', label: '正式仿真就绪度', error_count: 0, warning_count: 0, issues: [] },
        ],
      },
    })

    render(<TopologyValidationPanel onValidate={vi.fn()} />)
    expect(screen.getByText('12 × ACCESS_UNCONFIGURED')).toBeInTheDocument()
    const heading = screen.getByRole('button', { name: /业务可达性/ })
    expect(heading).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(heading)
    expect(heading).toHaveAttribute('aria-expanded', 'true')
    const list = screen.getByRole('list')
    expect(within(list).getAllByTitle('定位并选中')).toHaveLength(10)
    expect(screen.getByText('1 / 2')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
    expect(within(screen.getByRole('list')).getAllByTitle('定位并选中')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'storage-11' }))
    expect(useTopologyEditorStore.getState().selection).toEqual({ kind: 'node', id: 'storage-11' })
  })
})
