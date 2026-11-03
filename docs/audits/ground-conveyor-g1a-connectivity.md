# Gate G1-A：运输网络契约与真实连通性验证

## 1. 审计边界与结论

- 拓扑 ID：`topology-6ceb4920`
- 拓扑 Revision：`2.0.0:fe9fd41bf5cec86b`
- 数据源：`data/topology_666.json`；API 通过 SQLite 只读连接读取同一拓扑 payload。
- 结论：左侧与右侧完整出库路径均为 `BLOCKED`。这是业务路径缺失，不是诊断接口异常；API 返回 HTTP 200 和 `status=BLOCKED`。
- 本 Gate 没有修改拓扑 JSON 或 SQLite，没有实现订单、库存、调度、输送线运动或死锁恢复。

严格图只接受以下证据：显式 road cell、`ShuttleGraph` 轨道、已验证 Shuttle Lift、真实相邻且方向一致的 conveyor cell，以及状态为 `VALID` 的正式 handoff。空白网格、模型相邻、相同 row/col 和 `walkable=true` 都不能自动产生跨网络边。

## 2. 契约

`backend/app/schemas.py` 定义：

- `TransportNode`：节点 ID、节点类型、运输网络、拓扑 cell/轨道节点、楼层/高度、physical/simulated、source 和 provenance。
- `TransportEdge`：有向端点、边类型、方向、距离、来源、provenance 和 traversable。
- `HandoffPoint`：上下游网络、设备、输入/输出节点、容量、验证状态和证据。
- `NetworkIssue`：稳定 issue code、严重度、源/目标、原因和证据。
- `PathAudit` / `PathAuditSegment`：逐段状态、节点序列、边序列、有向可达性、合法交接和阻断原因。

`backend/app/services/transport_network.py` 从当前拓扑派生严格有向图。可选的 `topology.transportNetwork` 是 G1-B 可填写的声明式契约；当前 666 拓扑没有该字段。声明边不能跨网络；跨网络必须由 `HandoffPoint` 完成。合法 handoff 必须同时满足：

1. `validationStatus=VALID`；
2. `deviceId` 非空；
3. `capacity > 0`；
4. 输入、输出节点存在；
5. 节点网络分别匹配 `fromNetwork`、`toNetwork`。

## 3. 真实节点映射

| 业务对象 | 真实 ID | 坐标/层 | 证据与限制 |
| --- | --- | --- | --- |
| 左 Port 1 | `zone-port` | entry `cell-r73-c91` | entry 不是显式 cell；下方 `cell-r74-c91` 是 L2 架空 conveyor |
| 左 Port 2 | `zone-port-2` | entry `cell-r73-c83` | entry 不是显式 cell；下方 `cell-r74-c83` 是 L2 架空 conveyor |
| 左 Port 3 | `zone-port-3` | entry `cell-r73-c75` | entry 不是显式 cell；下方 `cell-r74-c75` 是 L2 架空 conveyor |
| 左 Port 4 | `zone-port-4` | entry `cell-r73-c67` | entry 不是显式 cell；下方 `cell-r74-c67` 是 L2 架空 conveyor |
| 人工拣选位 | `MISSING:pick_station` | 未知 | `pick_station` zone 数量为 0 |
| 拆零出口 | `MISSING:breakpack_exit` | 未知 | `drop_zone` zone 数量为 0 |
| Shuttle Dock | `dock-01` | 1F, r74,c17 | 同坐标有 `cell-r74-c17`，但没有 handoff |
| 地面线起点 | `cell-r74-c17` | L1, r74,c17 | `right`、`conveyorGround=true` |
| 地面线当前末端 | `cell-r74-c48` | L1, r74,c48 | `right`；下一格不存在 |
| 下一段 | `cell-r74-c52` | L2, r74,c52 | `right`、架空 conveyor；不能跨越 c49..c51 |
| 现有转向设备 | `turn_transfer-14` | 1F, r74,c43 | 位于地面线；没有连接架空线的边/交接 |
| 汇流 | `MISSING:conveyor-merge` | 未知 | 没有正式 merge 资源 |
| 最终出库 | `MISSING:outbound` | 未知 | 没有 outbound node/zone/acceptance handoff |
| Shuttle Lift A | `shuttle-lift-a` | r98,c21, L1-L4 | ShuttleGraph 内部已验证，尚未连接 dock |
| Shuttle Lift B | `shuttle-lift-b` | r98,c49, L1-L4 | ShuttleGraph 内部已验证，尚未连接 dock |

CTU 严格验证样例从真实 `storage-01`（1F,r0,c6, `zone-ctu-6`）开始。该库位长边相邻位置没有显式 `road` cell，因此不能将旧 CTU 规划器生成的空白网格路径视为物理停靠证据。

## 4. 左侧路径证据

| 分段 | 起点 → 终点 | 网络 | 状态 | 证据/阻断 |
| --- | --- | --- | --- | --- |
| 库位到 CTU 停靠 | `storage:storage-01` → `MISSING:explicit_ctu_stop` | CTU | BLOCKED | 相邻空白格不是显式 road；issue `CTU_EXPLICIT_STOP_MISSING` |
| CTU 到 Port | `MISSING:explicit_ctu_stop` → `facility:zone-port` | CTU→Port | BLOCKED | Port 存在，正式 CTU/Port 交接设备不存在 |
| Port 到人工拣选 | `facility:zone-port` → `MISSING:pick_station` | Port→人工 | BLOCKED | 无 `pick_station` 实体、边和 handoff |
| 人工拣选到拆零出口 | `MISSING:pick_station` → `MISSING:breakpack_exit` | 人工 | BLOCKED | 无 `drop_zone` 或拆零出口 |
| 拆零出口到输送带 | `MISSING:breakpack_exit` → `MISSING:left-conveyor-input` | 人工→架空输送带 | BLOCKED | 无设备交接 |
| 输送带到汇流 | `MISSING:left-conveyor-input` → `MISSING:conveyor-merge` | 架空输送带 | BLOCKED | 无正式 merge 节点与完整有向边 |
| 汇流到出库 | `MISSING:conveyor-merge` → `MISSING:outbound` | 输送带→出库 | BLOCKED | 无 outbound 与收货完成交接 |

## 5. 右侧路径证据

当前 Dock cursor 逻辑优先选择的两项来源为：

- `cell-r96-c27/L2`：现有运行时 `robotToSource=false`、`sourceToDock=false`；
- `cell-r96-c29/L2`：现有运行时 `robotToSource=false`、`sourceToDock=false`。

严格图不为货位与轨道自动构建边；需要“相邻停车轨道 + 货叉取箱”动作契约。

| 分段 | 起点 → 终点 | 网络 | 状态 | 证据/阻断 |
| --- | --- | --- | --- | --- |
| 货位到 Shuttle 停靠轨道 | `shuttle-storage:cell-r96-c27:L2` → `MISSING:shuttle-pickup-track` | Shuttle | BLOCKED | 无停车轨道/货叉交接边 |
| 轨道经 Lift 到 Dock | `MISSING:shuttle-pickup-track` → `facility:dock-01` | Shuttle→Dock | PARTIAL | `shuttle-lift-a/b` 仅证明 ShuttleGraph 内部跨层；没有 Dock 边 |
| Dock 到地面线 | `facility:dock-01` → `conveyor-cell:cell-r74-c17` | Dock→地面输送带 | BLOCKED | 坐标重合不等于合法 handoff |
| 地面线有向前缀 | `cell-r74-c17` → `cell-r74-c48` | 地面输送带 | PASS | c17..c48 真实相邻且全部向右；API 返回完整 nodeIds/edgeIds |
| 地面线到架空线 | `cell-r74-c48` → `cell-r74-c52` | 地面→架空 | BLOCKED | c49..c51 缺失，且 c48=L1、c52=L2 |
| 架空线到汇流 | `cell-r74-c52` → `MISSING:conveyor-merge` | 架空输送带 | BLOCKED | 无批准的 merge 节点 |
| 汇流到出库 | `MISSING:conveyor-merge` → `MISSING:outbound` | 输送带→出库 | BLOCKED | 无 outbound 与验收边 |

旧 `shortest_path_shuttle` 允许 Shuttle 在车库外普通网格上移动，不能作为 Dock/Conveyor 的正式网络契约；G1-A 仅用它复核现有默认源点的失败事实，不用它构建严格图。

## 6. c48→c52 断链方案比较

| 方案 | 最小数据变化 | 优点 | 风险/确认项 |
| --- | --- | --- | --- |
| A. 补输送段 | 新增 r74,c49..c51 conveyor、方向和高度；若高度变化需再声明升降/移载设备 | 路径直观、容易做有向验证 | c48 为 L1、c52 为 L2，单纯补 cell 仍不能合法跨层；需物理勘测坡道或升降机构 |
| B. 验证已有移载设备 | 若现场确有设备，新增 device、输入/输出节点、容量与 VALID handoff | 不改变已有主线几何 | 当前拓扑 c49..c51 没有对应节点；不得仅凭 3D 外观认定存在 |
| C. 重规划线路 | 在已存在且同层的 conveyor/transfer 节点间声明新线路 | 可避开三格缺口和高度冲突 | 需重新验证方向、占位、与 `turn_transfer-14` 等设备的实际关系 |

推荐决策顺序：先现场确认 B；不存在设备则比较 A 的物理施工与 C 的线路调整。G1-A 不修改拓扑。

## 7. proposed 候选布局

所有候选均为 `physicalStatus=proposed`，不能当作已建成设施。

- 四个 CTU/Port handoff：候选坐标分别 r73,c91 / c83 / c75 / c67。依据是 Port zone 的真实入口几何；冲突是入口并非显式 cell，且下方输送带在 L2。需确认 CTU 停靠 road、升降/移载方式和设备容量。
- Pick station：只限定在四个 Port 周边的人工勘测区域，不给出伪精确坐标。需确认操作面、CTU 停靠侧、人员安全区和架空线接口。
- Breakpack exit：应与已批准的 Pick station 工作单元联合设计；当前无可靠坐标。
- Shuttle/Dock handoff：候选为 r74,c17，因为 `dock-01` 与地面线首格同坐标。仍需补 ShuttleGraph approach track、Dock 输入和 Dock→Conveyor 输出动作。
- Conveyor bridge/transfer：候选区域 r74,c49..c51；必须解决 L1→L2 高差。
- Merge：`turn_transfer-14`（r74,c43）可作为现场调查候选，因为它是真实 transfer 节点；但目前只有地面线经过，架空线不在此处，不能认定为汇流点。
- Outbound：只能限定为“批准后的 merge 下游有向 sink 区域”；在 merge 方案确定前没有可靠坐标。

## 8. G1-B 待实施与人工确认

G1-B 开始前需要人工给出或批准：

1. 每个 CTU 库位/Port 的显式 road 范围及通行方向，是否允许将哪些空白格正式建模为 road。
2. 四个 Port 的 CTU 停靠 cell、设备 ID、输入/输出层、容量和交接完成信号。
3. Pick station 与 breakpack exit 的准确 row/col/level、工位容量及两侧设备。
4. 右侧每类货位对应的 Shuttle 停车轨道和货叉取放动作；尤其修复默认前两个 L2 来源。
5. ShuttleGraph 到 `dock-01` 的真实 approach track、Dock 设备动作和完成信号。
6. r74,c49..c51 采用补线、已有移载设备还是重规划；如跨 L1/L2，提供物理设备证据。
7. Merge 的准确设备 ID、入口边、优先/容量参数和输出边。
8. Outbound 的准确节点、接收容量和“货物已交付”事件定义。

不可擅自假定：空白格可行驶、同坐标即交接、相邻模型即连通、`walkable=true` 允许车辆进入 conveyor、Shuttle 内部 Lift 自动连接 Dock、前端动画代表后端货物状态。

## 9. API 验收

接口：`GET /api/v1/topologies/{topology_id}/transport-network/audit`

- 有效拓扑但路径缺失：HTTP 200，顶层 `status=BLOCKED`，每个阻断提供 issue code 和证据。
- 拓扑不存在：HTTP 404。
- 读取使用 SQLite `mode=ro`；不创建 Run、不写事件、不修改库存或拓扑。

用户运行服务后可执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/topologies/topology-6ceb4920/transport-network/audit | ConvertTo-Json -Depth 20
```
