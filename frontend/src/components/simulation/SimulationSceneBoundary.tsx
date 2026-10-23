import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface SimulationSceneBoundaryProps {
  children: ReactNode
}

interface SimulationSceneBoundaryState {
  error: Error | null
}

/**
 * Keep WebGL/model failures local to the viewport. The surrounding order queue
 * and run controls must remain usable so operators can retry or leave safely.
 */
export class SimulationSceneBoundary extends Component<
  SimulationSceneBoundaryProps,
  SimulationSceneBoundaryState
> {
  state: SimulationSceneBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): SimulationSceneBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('3D 仿真场景初始化失败', error, info)
  }

  private retry = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div className="simulation-3d-loading" role="alert">
        <p className="form-error">3D 场景初始化失败，订单与运行控制仍可继续使用。</p>
        <p className="muted-copy">{this.state.error.message || '未知场景错误'}</p>
        <button className="ghost-button" type="button" onClick={this.retry}>
          重试 3D 场景
        </button>
      </div>
    )
  }
}
