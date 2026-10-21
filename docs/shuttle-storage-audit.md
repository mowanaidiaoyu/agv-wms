# 666 四向车货位与取货映射审计

审计基线：拓扑 `topology-6ceb4920`，拓扑版本 `2.0.0`，内容修订
`2.0.0:6bb73d03cd63cbb4`。

## 已证明的事实

- 2496 个正式 `storage` 节点全部归属 CTU 库区。
- 31 个 Shuttle 区域通过 `slotAisles` 四位掩码描述 L1～L4 的可通行层。
- ShuttleGraph 只把掩码中置位的层生成轨道节点；轨道节点不是库存货位。
- 非通道层在 3D 中显示为货架。按这一几何规则可确定 4238 个货架层候选，
  其中 2204 个候选旁边存在同层正交 ShuttleGraph 节点。
- 拓扑没有为这些候选声明稳定的正式货位身份、取货面或已审核的 Access Track Node。
- 拓扑没有声明正式的 Shuttle 出库交接站。

因此所有派生记录均为 `draft / unverified`，`execution_allowed=false`。候选不能用于
SKU 绑定、库存补货、库存预留、取货路径执行或库存扣减。

## 数据契约

契约版本为 `shuttle-storage-contract/v1`。候选记录包含：

- `location_id`：由 zone、Shuttle 内部层、row、col 确定的稳定候选 ID；
- `topology_id` / `topology_revision`：保护拓扑版本一致性；
- `zone_id` / `floor` / `storage_level` / `row` / `col`：候选货架几何来源；
- `pick_face=unknown`：当前拓扑未提供；
- `access_track_node_id=null`：当前不存在已审核映射；
- `potential_access_track_node_ids`：同层正交邻接的真实轨道节点，仅供审核；
- `access_mapping_status=unverified` / `review_status=draft`；
- `data_source=topology_shuttle_slot_candidate`；
- `execution_allowed=false`。

审计接口：

- `GET /api/v1/shuttle/topologies/{topology_id}/storage-audit`
- `GET /api/v1/shuttle/topologies/{topology_id}/storage-candidates`

两个接口均为只读接口，不写入 SQLite，也不修改 `666.json`。

## 研究生 B 必须确认的空间语义

1. 哪些货架层候选是真实可存货位置，以及稳定的业务货位编号。
2. 每个真实货位允许从哪一侧取货。
3. 哪一个同层 ShuttleGraph 节点是合法停车/取货点。
4. 车辆停车取货时的包围盒和相邻轨道占用规则。
5. 不同层是否允许共用相同平面坐标的货位身份。
6. 正式出库交接站的位置、容量和停车条件。
7. 若采用离线演示交接点，需明确标记 `synthetic_demo`，不得称为正式 WMS 站台。

完成上述审核并形成版本化映射之前，C-SHUTTLE-PICK-1 的库存绑定、任务预留、
Pick 确认、库存扣减与运输动画均保持阻塞。
