# Gate G1-B1：全仓接驳设施布局设计与 3D 预览

## 1. 方案身份与边界

- `planId`：`g1b1-topology-6ceb4920-handoff-layout-v1`
- `topologyId`：`topology-6ceb4920`
- `topologyRevision`：`2.0.0:fe9fd41bf5cec86b`
- 推荐选项：`scheme-a-height-transfer`
- 总体状态：`PARTIAL`
- 独立数据文件：`data/transport_layout_plans/topology-6ceb4920.g1b1.json`

本方案只生成 proposed 设施、连接和 3D 预览，不写 `topology_666.json`、SQLite、订单或库存。只读 API 会重新计算当前拓扑 Revision；不一致时返回 HTTP 409，拒绝加载预览。

## 2. 左侧 CTU / 人工拆零布局

当前 Port 北侧 r64..r73 没有货架、固定节点或 blocked cell，但也没有正式 road。因此几何占位允许提出候选，物理连通仍不能判定 PASS。

### 2.1 Port 工作单元

| Port | 入口 | CTU 道路接点 | CTU 停靠 | 输入 Buffer | Pick station | 输出 Buffer | L1→L2 移载 | 现有 Conveyor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `zone-port` | r73,c91 | r67,c91 | r69,c91 | r70,c91 | r71,c91 | r72,c91 | r73,c91 | `cell-r74-c91`, L2 |
| `zone-port-2` | r73,c83 | r67,c83 | r69,c83 | r70,c83 | r71,c83 | r72,c83 | r73,c83 | `cell-r74-c83`, L2 |
| `zone-port-3` | r73,c75 | r67,c75 | r69,c75 | r70,c75 | r71,c75 | r72,c75 | r73,c75 | `cell-r74-c75`, L2 |
| `zone-port-4` | r73,c67 | r67,c67 | r69,c67 | r70,c67 | r71,c67 | r72,c67 | r73,c67 | `cell-r74-c67`, L2 |

所有新增设施均为 `proposed`。输出 Buffer 与垂直移载使用已有 Port footprint，采用 `co_located_with_reference`，不声称已有设备具备该能力。

### 2.2 最小道路建议

- 东西向候选走廊：r67,c67..c91，双向 CTU road。
- 四条 Port 支路：各自从 r67 沿同列到 r69 CTU stop。
- 不覆盖现有货架、车辆节点或 blocked cell。
- 走廊到 CTU 库内货架巷道仍为 `BLOCKED`：当前库区依赖旧规划器的空白网格，G1-B2 必须现场确认正式 road 范围后才能接入。

### 2.3 工位动作

1. CTU 在 r69 停靠并将料箱交给 r70 输入 Buffer。
2. 操作员从输入 Buffer 接受料箱，在 r71 拣选。
3. 拣选确认后进入 r72 输出 Buffer。
4. r73 垂直移载请求 L2 conveyor 空位并提升货物。
5. 收到 conveyor acceptance 后进入 r74 对应真实 conveyor cell。

## 3. 右侧 Shuttle / Dock 布局

### 3.1 两个不可达 L2 来源

| 来源 | 修复候选 | 证据 | 状态 |
| --- | --- | --- | --- |
| `cell-r96-c27/L2` | 新增 `proposed-L2-r96-c25` 停靠短轨，连接真实 `L2-r96-c23`，从西侧向东伸叉 | 96,25 是现有 removed slot，无 cell、货架或节点；不需要过滤来源 | PARTIAL，需批准 L2 轨道结构 |
| `cell-r96-c29/L2` | 复用真实 `L2-r96-c31`，从东侧向西伸叉 | `L2-r96-c31` 在 ShuttleGraph 中存在 | 静态连接 PASS，交接动作仍 proposed |

修复方案保留两个来源，不通过过滤不可达货位伪造整体可达。

### 3.2 ShuttleGraph 到 Dock

- ShuttleGraph 出口参考：`L2-r96-c19`。
- proposed L2 架空接近轨：r96,c19 → r94,c19 → … → r76,c19 → r76,c17。
- proposed Dock 下降移载：r76,c17/L2 → `dock-01` r74,c17/L1。
- proposed Dock handoff：`dock-01` → `cell-r74-c17`，容量 1。
- Shuttle 始终停留在轨道和接近轨上，不驶入 Conveyor。

高度：Shuttle L1 轨面和地面 belt 均为 0.6；本方案使用 L2 接近轨，再由封闭式货箱下降移载交付到 L1 Dock。接近轨在 r92,c19 上方跨越现有地面 conveyor，净空和承重尚未证明，因此右侧完整路线为 `PARTIAL`。

`shuttle-lift-a`（r98,c21）与 `shuttle-lift-b`（r98,c49）继续作为 ShuttleGraph 内部 L1-L4 真实 Lift；非 L2 来源可先使用真实 Lift 到 L2，再进入 proposed 接近轨。

## 4. 中间输送线方案

### 4.1 方案 A：补线与专用高度转换（推荐）

起点：`cell-r74-c48`，L1，right。

终点：`cell-r74-c52`，L2，right。

新增高度转换：`proposed-height-transfer-a-r74-c49-c51`：

- c49/L1：输入缓冲；
- c50/L1：升降/斜坡设备主体；
- c51/L2：输出缓冲；
- c52/L2：向现有架空线释放；
- 容量建议：1；
- 动作：c48 接受 → 高度转换完成 → 确认 c52 空位 → 释放。

L2 需要补充的 row 74 cell：

- c54..c55；
- c58..c59；
- c62..c65；
- c68..c73；
- c76..c81；
- c84..c89；
- c92..c96。

方向全部为 `right`。这些 cell 当前为空白，不会被审计器自动视为 Conveyor；仅在 G1-B2 批准后才可加入正式拓扑。

静态有向连接可以形成，但高度转换设备、空白区域结构和外部出库设备尚未现场确认，因此选项状态为 `PARTIAL`，不是正式拓扑 PASS。

### 4.2 方案 B：复用 `turn_transfer-14`（BLOCKED）

- 候选 Merge：`turn_transfer-14`，r74,c43/L1。
- 右侧输入：地面线 c17→c43，当前方向正确。
- 左侧输入：需要从 c67/L2 向西到 c43/L1。
- 候选 Outbound：c48/L1。

阻断：

1. c67→c43 的现有 elevated conveyor 全部向右，不存在西向路径；
2. r74,c43 没有 L2→L1 设备；
3. c48 后仍受 c49..c51 断链影响，c48 不能成为有合法下游的最终 Outbound。

因此不能把 `turn_transfer-14` 直接认定为 Merge。

## 5. Merge 与 Outbound

推荐 Merge：`proposed-merge-a-r74-c67`，r74,c67/L2。

- 右侧输入：c52→c66→c67，right；
- 左侧输入：`zone-port-4` 垂直移载从北侧进入 c67；
- 输出：c68→…→c96，right；
- 处理容量：1；
- 两侧输入 Buffer 建议总容量：2；
- 当前 c67 只是真实 conveyor cell，Merge 设备本身为 proposed。

推荐 Outbound：`proposed-outbound-a-r74-c96`，r74,c96/L2。

- 输入：c95→c96，right；
- 接收容量建议：2；
- 必须配置物理接收传感器；只有收到 acceptance 才允许后续 Gate 把订单标记为已交付；
- c96 之后的外部出库设备不在当前拓扑内，仍需人工确认。

## 6. 3D 预览

仿真页面左上楼层工具条新增“接驳布局”开关。预览复用 `Simulation3DScene` 现有 Canvas，没有创建第二个 Canvas。

- 蓝色：左侧 CTU/人工路径；
- 橙色：右侧 Shuttle 路径；
- 绿色：真实引用节点或已验证 ShuttleGraph 连接；
- 黄色：proposed 设施或补线；
- 红色：BLOCKED 连接或冲突；
- 紫色：Merge / Outbound。

操作：

1. 打开“接驳布局”；
2. 在方案 A/B 间切换；
3. 点击 3D 中设施、连接或绿色真实节点；
4. 右侧面板显示 ID、row/col/level、用途、容量和冲突。

开关与选中状态是页面本地预览状态，不写 Zustand 仿真状态、不修改 topology prop。虚线/彩线只是设计意图，不改变 G1-A `BLOCKED` 结论。

## 7. G1-B2 最小正式拓扑变更

需用户确认推荐方案 A 后才实施：

1. 批准 r67,c67..c91 CTU corridor 和四条 Port 支路，并给出与库内正式 road 的连接点。
2. 新增四组 CTU stop、输入 Buffer、pick_station、输出 Buffer 与 Port 垂直移载节点。
3. 为 `cell-r96-c27/L2` 新增 r96,c25/L2 停靠短轨与 fork handoff；为 `cell-r96-c29/L2` 声明 c31 停靠交接。
4. 批准 L2-r96,c19→r76,c17 接近轨净空及 r76,c17 Dock 下降移载。
5. 新增 Dock→ground conveyor 正式 handoff。
6. 在 c49..c51 新增有设备 ID、容量和信号的 L1→L2 converter。
7. 补齐方案 A 列出的 row74/L2 conveyor cells 和 right edges。
8. 在 c67 新增双输入 Merge 资源，在 c96 新增 Outbound acceptance 资源。
9. 将所有新设备写入正式 `transportNetwork` 契约，再重新运行 G1-A/G1-B 连通性验证。

未确认前不得修改正式拓扑。
