# PYC / C 统一仿真工作台审计与适配记录

日期：2026-10-22  
基线：`origin/pyc` (`409d641`)  
工作分支：`integration/pyc-c-unified`

## 审计结论

| 能力 | 结论 | 依据 / 处理 |
| --- | --- | --- |
| PYC 订单队列、运行控制、3D 场景 | 可直接复用 | `SimulationPage`、`Simulation3DScene`、`usePlatformStore`、`SimulationService` 已形成单一入口、单一 WebSocket 状态和单一后端 tick。 |
| 蓝色 CTU 仓库订单流程 | 可直接复用 | 保留订单生成、库存槽分配、CTU 多箱取货、输送口交付及 17 层/5 箱模型。 |
| 橙色仓库建模 | 可直接复用 | PYC 的 `topology_666.json` 和 `buildSceneSpec` 已在同一 Canvas 中渲染 shuttle zone，无需复制 C 的完整 3D 页面。 |
| C ShuttleGraph 与 A*/Dijkstra | 可直接复用 | 复用 C 的 `shuttle_topology.py` 和 `shuttle_pathfinding.py`，规划输入和输出保持轨道节点、边、升降机转换与 synthetic timing 契约。 |
| Lift-A / Lift-B 配置 | 需要适配 | C 配置绑定 C formal topology 修订；PYC 666 是相同 ID/网格但不同内容修订。新增配置绑定 PYC 修订并重新执行 footprint、slotAisles、连通性和重叠检查。 |
| C 独立回放工作台 | 不直接复用 | 原实现使用独立 Canvas 与独立 elapsed-seconds advance API，会形成第二时钟；统一工作台改用播放适配层把 C path edge 转为 PYC tick 帧。 |
| C demo inventory | 不直接复用 | 原服务明确与 CTU/WMS 库存分离。统一实现改为 run-isolated inventory snapshot，并把 reserve/pick/drop/cancel 事件放进 PYC runtime state。 |
| 真实设备执行反馈 | 缺失 | C 后端只提供规划结果与 synthetic timing；当前实现是确定性的仿真播放适配，不宣称接入 PLC/WCS 反馈。 |
| 物理货位到轨道接驳映射 | 部分缺失 | 当前最小链路使用所选真实 ShuttleGraph 节点派生 `SIM-BIN-*` 仿真库位；坐标来自拓扑，不是屏幕硬编码，但物理取放面仍待人工/现场确认。 |

## 666 拓扑关系

- 两边 ID 均为 `topology-6ceb4920`，版本 `2.0.0`，网格 `216 x 129`，坐标原点一致。
- PYC 当前拓扑修订为 `2.0.0:7fdde35b3ef6f522`；C 旧 Lift 配置绑定 `2.0.0:6bb73d03cd63cbb4`，不能直接冒充同一修订。
- 在 PYC 修订上重新验证后，Lift-A=`zone-shuttle-3 / (100,19)`、Lift-B=`zone-shuttle-30 / (188,39)` 均满足四层 bit mask、2×2 road footprint、同区和无节点重叠约束。
- 当前 PYC topology 还带有 `zone-shuttle-2` 的两个 `slotLifts`。统一跨层任务只使用版本化 C Lift 配置返回的边，不把两套竖向连接同时加入同一条规划结果。

## 统一数据流

1. 前端从 `/shuttle/topologies/{id}/contract` 读取 L1-L4 真实轨道节点和 Lift 配置。
2. `/shuttle/plan` 调用 C ShuttleGraph 的 A*/Dijkstra，返回 `path_nodes`、`path_edges`、`lift_transitions` 和 synthetic timing。
3. 启动请求把订单与可选 `transfers` 一次性交给 PYC `SimulationService`。
4. `unified_transfer.build_playback_route` 将 C 的结果转换为 `track / enter_lift / lift_moving / exit_lift` tick 帧。
5. 唯一的 `SimulationService._loop` 推进订单、CTU、四向车、任务阶段和库存事件；没有第二个业务定时器。
6. WebSocket tick 同时更新任务、车辆、`transferInventory` 与 `transferEvents`；侧栏和 3D 库存标记读取同一个 runtime state。

## 库存规则

- 创建：校验 `available >= quantity`，只增加预留，不减少 on-hand。
- 取货：幂等 `task:pick` 事件使源 on-hand 减少、预留释放、在途增加。
- 放货：幂等 `task:drop` 事件使目标 on-hand 增加、在途减少。
- 取消（取货前）：释放预留；取消（取货后）：通过补偿事件退回源库位。
- 失败：冻结当前 on-hand / reserved / in-transit 状态；重试从当前阶段继续，不重复 pick。
- 重置：恢复运行创建时的深拷贝快照和初始事件账，不把回放写回全局库存。
- 入库、库间转移和真正出库没有混用：本次新增接口只表达 `shuttle_transfer` 库间转移；PYC 原订单交付仍走原出库规则。

## 明确边界

- Lift 行程时间仍为 `synthetic_demo`，不是设备规格。
- 本轮按要求没有启动前后端服务、没有做浏览器验收、没有运行完整测试套件。
- 物理货位/取放面映射和真实 WMS 持久库存仍需后续接入，因此在浏览器与现场验收前结果应标记为 `PARTIAL`。
