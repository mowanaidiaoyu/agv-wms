import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { conveyorDecksOf, paintCell, placeNode } from '../../lib/topology'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import { TopologyPropertyPanel } from './TopologyPropertyPanel'

describe('TopologyPropertyPanel manual conveyor editor', () => {
  beforeEach(() => {
    useTopologyEditorStore.getState().beginNew()
    let topology = useTopologyEditorStore.getState().draft
    topology = paintCell(topology, 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    useTopologyEditorStore.setState({ draft: topology, activeTool: 'select' })
  })

  it('selects one 2x2 block and changes only its explicitly chosen sides', () => {
    const store = useTopologyEditorStore.getState()
    const selectedCell = store.draft.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    store.entityPointerDown('cell', selectedCell.id, '1F')

    expect(useTopologyEditorStore.getState().selection).toEqual({ kind: 'cell', id: selectedCell.id })
    render(<TopologyPropertyPanel />)

    expect(screen.getByText('单个输送模块')).toBeInTheDocument()
    expect(screen.queryByText('自动生成整线方向')).not.toBeInTheDocument()

    const inputPicker = screen.getByText('入口方向').parentElement!
    fireEvent.click(within(inputPicker).getByRole('button', { name: '↓ 南侧' }))
    const outputPicker = screen.getByText('出口方向').parentElement!
    fireEvent.click(within(outputPicker).getByRole('button', { name: '→ 东侧' }))

    const next = useTopologyEditorStore.getState().draft
    const selectedAnchor = next.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    const untouchedAnchor = next.cells.find((cell) => cell.row === 0 && cell.col === 2)!
    expect(selectedAnchor.conveyorDecks?.lower).toMatchObject({ inputSide: 'south', outputSide: 'east' })
    expect(untouchedAnchor.conveyorDecks).toBeUndefined()
  })

  it('requires the upper layer to be edited separately', () => {
    const store = useTopologyEditorStore.getState()
    const selectedCell = store.draft.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    store.entityPointerDown('cell', selectedCell.id, '1F')
    render(<TopologyPropertyPanel />)

    fireEvent.change(screen.getByLabelText('模块层数'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '上层' }))
    const inputPicker = screen.getByText('入口方向').parentElement!
    fireEvent.click(within(inputPicker).getByRole('button', { name: '↑ 北侧' }))
    const outputPicker = screen.getByText('出口方向').parentElement!
    fireEvent.click(within(outputPicker).getByRole('button', { name: '← 西侧' }))

    const anchor = useTopologyEditorStore.getState().draft.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    expect(anchor.conveyorDecks?.upper).toMatchObject({ inputSide: 'north', outputSide: 'west' })
  })

  it('configures a turn transfer on only one selected deck', () => {
    let topology = useTopologyEditorStore.getState().draft
    topology = {
      ...topology,
      cells: topology.cells.map((cell) => cell.row < 2 && cell.col < 2 ? { ...cell, levels: 2 } : cell),
    }
    topology = placeNode(topology, 'turn_transfer', 0, 0, '1F')!.topology
    const transfer = topology.nodes.find((node) => node.type === 'turn_transfer')!
    useTopologyEditorStore.setState({
      draft: topology,
      selection: { kind: 'node', id: transfer.id },
      activeTool: 'select',
    })

    render(<TopologyPropertyPanel />)

    expect(screen.getByText('单层转向设备')).toBeInTheDocument()
    expect(screen.getByLabelText('安装层级')).toHaveValue('upper')
    const outputPicker = screen.getByText('出口方向').parentElement!
    fireEvent.click(within(outputPicker).getByRole('button', { name: '↓ 南侧' }))

    const next = useTopologyEditorStore.getState().draft
    const updatedTransfer = next.nodes.find((node) => node.id === transfer.id)!
    const anchor = next.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    const decks = conveyorDecksOf(next, anchor)
    expect(updatedTransfer).toMatchObject({
      transferDeck: 'upper',
      transferInputSide: 'east',
      transferOutputSide: 'south',
    })
    expect(decks.lower).toMatchObject({ inputSide: 'west', outputSide: 'east' })
    expect(decks.upper).toMatchObject({ inputSide: 'east', outputSide: 'south' })
  })
})
