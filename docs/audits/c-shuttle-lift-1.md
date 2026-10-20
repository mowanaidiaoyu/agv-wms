# Gate C-SHUTTLE-LIFT-1 审计与实现说明

## 审计结论

- 拓扑：`topology-6ceb4920`（666），原始版本 `2.0.0`。
- 本 Gate 绑定的内容修订：`2.0.0:6bb73d03cd63cbb4`。
- 四向车区域：31 个 `type=shuttle` 的 Zone，范围为 row 96–201、col 3–58。
- 蓝色结构的真实来源：`Zone.slotAisles["row,col"]` 位掩码。当前渲染代码 `ZONE_LEVELS.shuttle=4`，数据也包含 bit3，因此代码层面实际建模了 4 个可着色货架层，而不是只有 3 层。本 Gate 按用户范围只把 bit0/bit1/bit2 建成 L1/L2/L3 可计算轨道；L4 保留原样但不参与本 Gate 规划。轨道层不是建筑楼层，也不是 CTU 库位层。
- 旧的地面 `road Cell` 只表达“任一层存在巷道”后的二维兼容通行性，不能单独表达分层轨道。
- 原后端 A*/Dijkstra 只读取 Cell，没有读取 `slotAisles`，因此原路径规划不能合法表达四向车跨层。
- 原始 666 中没有 `lift` Resource、`lift` Node 或四向车提升机配置。

## ShuttleGraph

ShuttleGraph 从与前端 3D 渲染相同的 `slotAisles` 语义派生，使用 2×2 槽位中心作为轨道节点：

| 轨道层 | 节点数 | 连通分量 | 最大分量 |
| --- | ---: | ---: | ---: |
| L1 | 350 | 1 | 350 |
| L2 | 348 | 1 | 348 |
| L3 | 348 | 1 | 348 |

节点 ID 格式为 `L{level}-r{row}-c{col}`。相同 row/col 在不同层是不同节点；只有已验证提升机位置才创建跨层边。轨道图不读取 CTU Zone、输送线或无归属 road 作为四向车轨道。

## 提升机候选扫描与固定配置

确定性候选扫描要求：

1. 同一 2×2 槽位在 L1–L3 均有 `slotAisles` 通行位。
2. 槽位的 4 个底层 Cell 均为同一 shuttle Zone 内的 walkable road。
3. 不与既有 Topology Node 重叠。
4. 三层入口节点均至少有两个同层轨道邻接。
5. 候选扫描仅输出审计结果，不修改 666。

当前共有 332 个程序可验证候选。固定配置保存在 `data/shuttle_configs/topology-6ceb4920.shuttle-v1.json`：

| 提升机 | Zone | row,col | 三层入口/出口 | 选择依据 |
| --- | --- | --- | --- | --- |
| Lift-A | `zone-shuttle-3` | `100,19` | `L1/L2/L3-r100-c19` | 北部轨道区域；三层 degree=3；2×2 road；无节点占用 |
| Lift-B | `zone-shuttle-30` | `188,39` | `L1/L2/L3-r188-c39` | 南部轨道区域；三层 degree=4；2×2 road；无节点占用 |

两处位置相互分离，覆盖四向车区域的北部和南部。其状态为 `program_verified_pending_human`：可用于本项目的离线演示规划，但仍需研究生 B 对实际设备空间和施工语义进行人工复核。

## 路径成本与动画契约

- 同层速度：2 格/秒（`synthetic_demo`）。
- 换向时间：0.25 秒。
- 驶入提升机：1 秒。
- 提升机每跨一层：3 秒。
- 驶出提升机：1 秒。
- 所有成本统一为秒；不得将“格”和“秒”直接相加。
- A* 使用不高估真实成本的水平曼哈顿时间下界与最小垂直时间下界；Dijkstra 使用相同边成本。
- 轨迹记录 `enter_lift`、`lift_started`、`arrive_level`、`exit_lift`，前端只在 `motion_start_seconds` 到 `motion_end_seconds` 之间连续插值垂直坐标，驶入/驶出阶段保持合法端点位置，不产生瞬移。

## 数据隔离与限制

- 未修改 `docs/topologies/666.json`。
- 独立配置携带拓扑内容修订；拓扑变化后旧配置返回 `topology_version_mismatch` 并禁止规划。
- `unverified` / `rejected` 草案禁止进入规划。
- 本 Gate 是单台四向车离线轨迹规划与 3D 回放，不控制真实设备。
- 两台提升机均声明容量 1，但尚未实现多车时间预约、互斥等待与死锁处理，不能据此宣称已经支持多车提升机调度。
- CTU road、库存、取货映射和原多车预约规则均未被 ShuttleGraph 使用或修改。
