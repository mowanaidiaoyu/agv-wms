# Gate I0 — 性能分析平台集成前置审计

> 审计日期：2026-11-09（Asia/Shanghai）  
> 性能分析来源：`D:\agv-wms-analytics-phase1-source\agv-wms-multiout-return`  
> 集成基线：`D:\agv-wms-performance-integration`  
> 目标仓库：`https://github.com/mowanaidiaoyu/agv-wms.git`  
> 目标分支：`dev/c-multiout-return-20261104`  
> 审计 HEAD：`4d3141a67b144f6c56b363d48109e4a5f0068c17`

## 1. 结论

**RESULT：REVIEW REQUIRED。**

可以按 I1–I4 分阶段定向集成，但不能整文件覆盖。主要前置事项如下：

1. 目标分支的数据库三表结构、`get_read_conn()`、任务分配事件及跨层规划字段能够支撑五个只读指标 API。
2. 目标分支当前不持久化 `conveyorGraphNodes`。输送节点占用率在该分支新 Run 上只能返回 `null / CONVEYOR_GRAPH_NODES_MISSING`；不得补 0，也不得为了指标修改仿真逻辑。
3. 目标后端导入 `runtime.py` 时执行 `seed_defaults(repo)`，应用 lifespan 又执行 `init_db()`。任何 I1 测试必须先把 `DB_PATH` 指向临时数据库，再导入应用运行时。
4. 目标 `simulation.py`、`schemas.py`、`types.ts` 和 `SimulationPage.tsx` 均包含来源性能副本没有的新业务字段或行为，严禁覆盖。
5. 三份模拟结果原件与来源前端 public 副本逐字节一致，可按原字节新增到目标 public 目录。
6. 本地 P1-2 已实现后端只读接口、契约和隔离测试，但尚未接入 Analytics 前端；它不是本次“五个指标 API”的组成部分，应另行审批后再决定是否集成。

本 Gate 未运行后端或前端测试：目标后端现有测试导入应用会触发数据库初始化/默认拓扑写入；目标克隆又没有 `node_modules`。为遵守“仅新增本审计文档”，未安装依赖、未生成数据库、未生成构建产物。

## 2. 目录、Git 与版本状态

| 项目 | 结果 |
|---|---|
| 性能分析来源 | `D:\agv-wms-analytics-phase1-source\agv-wms-multiout-return` |
| 来源 Git 状态 | 不存在 `.git`；独立工作副本，未初始化 Git |
| 集成工作目录 | `D:\agv-wms-performance-integration` |
| 目标分支 | `dev/c-multiout-return-20261104` |
| 目标 HEAD | `4d3141a67b144f6c56b363d48109e4a5f0068c17` |
| HEAD 提交 | `2026-11-09T20:35:38+08:00 feat: 新增侧向出货口理货机械臂与放置区码箱` |
| 上一审计所见 HEAD | `2aa51a291ff5748993900ab647193b508b26f8b4`；目标分支已经更新，本报告未沿用旧版结论 |
| origin fetch/push | `https://github.com/mowanaidiaoyu/agv-wms.git` |
| 克隆后初始状态 | 干净；`dev/c-multiout-return-20261104...origin/dev/c-multiout-return-20261104` |
| Git 写操作 | 仅执行了用户允许的单分支 clone；未 commit、push、merge、rebase、reset、clean、checkout 或创建 PR |

报告交付后预期 Git 状态只增加：

```text
?? docs/integration/analytics-integration-audit.md
```

## 3. 来源功能边界

### 3.1 已存在并可作为集成候选

- 指标契约：`docs/analytics/metric-contract-v1.md`。
- 五个只读指标 API：tasks、history/quality、ctu-contention、path-planning、conveyor-coordination。
- 旧兼容历史 API：`GET /metrics/history`，它不是“五个新增 API”之一，但来源 `api.ts` 暴露了客户端方法，后端回归测试也覆盖它。
- `AnalyticsPage.tsx`、对应 CSS 与测试。
- 动态 ABC、波次优化、ETA 三份 SYNTHETIC 实验结果及前端 public 副本。
- P1-1 只读可行性审计文档。

### 3.2 P1-2 的真实状态

P1-2 **已经在本地完成后端实现**，证据为：

- `backend/app/core/repository.py:467`：`Repository.get_blocked_presence_metrics`；
- `backend/app/api/routes.py:399`：`GET /simulations/{run_id}/metrics/blocked-presence`；
- `backend/tests/test_metric_blocked_presence.py`；
- `docs/analytics/p1-2-blocked-presence-contract.md`。

但 `AnalyticsPage.tsx` 和 `frontend/src/lib/api.ts` 尚未引用 blocked-presence 接口。因此它不得描述成“已经完成前端集成”，也不应无审核地混入 I1 的五接口范围。

## 4. 后端 Schema、启动行为与核心差异

### 4.1 数据库兼容性

来源和目标都使用下列三张表，关键列一致：

| 表 | 关键列 |
|---|---|
| `topologies` | `id, name, payload, created_at, updated_at` |
| `simulation_runs` | `id, status, topology_id, payload, metrics, created_at, updated_at` |
| `simulation_events` | `id, run_id, tick, event_type, payload, created_at` |

目标 `backend/app/core/db.py:92` 已有 SQLite URI `mode=ro` 的 `get_read_conn()`，所以 I1 不需要修改 `db.py`。来源 `db.py` 比目标多一个 `idx_sim_events_run_type_tick` 索引；该索引不是正确性依赖。为了避免隐含 Schema 变更，I1 应保留目标 `db.py` 不动。代价是大事件表的历史查询可能较慢，需在 I4 压测后另行决定是否建立独立数据库优化 Gate。

### 4.2 启动会写数据库

- `backend/app/main.py:15` 的 lifespan 调用 `init_db()`。
- `backend/app/services/runtime.py:9` 在模块导入时调用 `seed_defaults(repo)`。
- `seed_defaults` 通过 `repo.get_topology()`；该方法使用 `get_conn()`，会先调用 `init_db()`，并可能插入默认拓扑。

因此“启动后端以验证 GET”本身不是只读动作。I1/I4 必须使用临时数据库路径，并在导入 `app.main` 或 `app.services.runtime` 前完成 monkeypatch；不得将来源 `agv_platform.db` 复制到目标业务目录。

### 4.3 业务模型差异

| 文件/字段 | 差异 | 集成含义 |
|---|---|---|
| `services/simulation.py` | 来源 4505 行，目标 5769 行，SHA 不同；目标新增侧向机械臂、放置区、单箱子任务和降频状态持久化等逻辑 | 严禁覆盖；指标只能读取目标现有状态 |
| `schemas.py` | 目标订单行支持 `qtyMode=pieces/boxes`，数量上限与来源不同 | 严禁用来源覆盖 |
| 任务模型 | 目标新增/扩展 `taskKind`、`parentId`、`pendingTransport`、件数/箱数字段 | 父子任务分类助手可兼容；保留未知字段 |
| 状态持久化 | 目标常规 tick 每 10 Tick 落一次全量 run payload，结束/控制操作仍即时落库 | 运行中读取 task payload 可能最多滞后约 9 Tick；历史 `metrics_snapshot` 仍每 Tick持久化 |
| `conveyorGraphNodes` | 来源写入初始 metrics 并在刷新时更新；目标不写该字段 | 当前节点占用率必须降级为 null；队列三个当前值仍可读 |
| 路径 plan | 两边 `shuttle_pathfinding.py` SHA 相同，均有 `algorithm/result_status/planning_time_ms/horizontal_distance_cells/visited_states` | 路径指标可移植，但仍是成功跨层初始规划样本 |
| 事件 | 目标仍产生 `task_state_changed`、`robot_yield`、`deadlock_detected`、`metrics_snapshot`，已核对 payload 结构 | CTU/争用和历史质量统计可移植；完整性仍标 DEGRADED/UNCONFIRMED |

核心文件对比还显示 `assignment.py`、`pathfinding.py`、`shuttle_pathfinding.py`、`unified_transfer.py`、`conveyor.py` 在两个目录中 SHA 相同；这不构成允许覆盖它们的理由。

## 5. 五个指标 API 可移植性

| API | 来源方法 | 目标数据支持 | 结论/降级 |
|---|---|---|---|
| `/metrics/tasks` | `get_task_metric_summary` | run payload 的 tasks/metrics；`task_state_changed` 与 `transfer_stage` | 可移植。按 `parentId` 分类、task.id 去重；运行中 payload 可能有降频持久化延迟 |
| `/metrics/history/quality` | `get_metrics_history_quality` | `simulation_events` 的 `metrics_snapshot`，含 id/tick/payload | 可移植。按 run 隔离、同 Tick 取最大 id、全量统计后抽样；目标无索引时需关注耗时 |
| `/metrics/ctu-contention` | `get_assignment_contention_metrics` | CTU 父任务、robots、`task_state_changed`、`robot_yield`、`deadlock_detected` | 可移植。事件完整性无法证明时只能输出已观察数量并标 DEGRADED；疑似告警不是真实死锁 |
| `/metrics/path-planning` | `get_path_planning_metrics` | `mode=shuttle_transfer` 和成功 plan 字段 | 可移植。A*/Dijkstra 分组；版本字段缺失时 DEGRADED；重规划成功率和优化率保持 UNAVAILABLE |
| `/metrics/conveyor-coordination` | `get_conveyor_coordination_metrics` | run metrics 中三个队列值和 `conveyorUtilization` | 部分可移植。三个队列代理值可读；因缺 `conveyorGraphNodes`，节点占用率为 null；正式物理利用率等保持 UNAVAILABLE |

所有方法只能加入目标现有 `Repository`，且只调用 `get_read_conn()` 和参数化 SQL。不能调用 `simulation_service`，不能修改 `create_run/update_run/add_event`。路由没有同名冲突，目标目前只有原 `/metrics`，没有上述五条或 `/metrics/history`。

## 6. 前端兼容性与冲突

### 6.1 依赖

来源与目标的 `package.json`、`package-lock.json`、`vite.config.ts`、`tsconfig.json`、`main.tsx` SHA 完全一致。兼容版本包括 React 19.2.7、React Router 7.8.2、TanStack Query 5.85.5、TypeScript 6.0.2、Vite 8.1.1。无需新增依赖或改 lockfile。

`API_BASE` 两边均为 `http://127.0.0.1:8001/api/v1`。模拟 JSON 使用站点根路径 `/analytics-synthetic-v1/*.json`，不经过 API_BASE。

### 6.2 可新增与必须合并部分

- 目标不存在 `AnalyticsPage.tsx/.css/.test.tsx`，可新增；CSS 选择器均使用 `.analytics-*` 或 `.synthetic-*` 前缀，污染风险低。
- `App.tsx` 只需新增 Analytics import 和 `/simulation/analytics` route，不能覆盖。
- `api.ts` 的性能部分是 GET-only，可局部追加；静态 JSON 也显式使用 GET。
- `types.ts` 必须只追加指标/实验类型和必要的可选 plan 字段。来源类型文件缺少目标最新订单/子任务/机械臂相关字段，整文件覆盖会造成真实业务回归。
- `usePlatformStore.ts` 两边 SHA 一致，不需要修改。URL query 是刷新恢复条件的权威来源，store 只作为当前会话回退。

Analytics 页面仅在真实 Run 模式且 runId 非空时启用五个查询；切到 SYNTHETIC 后才懒加载三份 JSON。查询返回的 `runId` 必须与当前 runId 相同才进入展示，从而避免 Run 切换后的旧缓存串页。页面没有 POST/PUT/PATCH/DELETE，也不在前端重算后端正式指标。

## 7. 库存详情入口位置与导航方案

目标位置：`frontend/src/pages/SimulationPage.tsx:593-629`。

- `WorkspaceTab` 定义在第 53 行，当前含 `orders | transfer | inventory`。
- “库存详情”按钮在第 597 行，设置 `workspaceTab='inventory'`。
- `TransferInventoryPanel` 导入在第 31 行，渲染分支在第 628 行。

I3 的最小改动应只把第 597 行按钮改为导航按钮，不删除库存类型、Schema、API、组件或 3D 库存逻辑，也不以来源 `SimulationPage.tsx` 覆盖目标文件。建议构造：

```ts
const params = new URLSearchParams()
if (resolvedTopologyId) params.set('topologyId', resolvedTopologyId)
if (selectedRunId) params.set('runId', selectedRunId)
navigate(`/simulation/analytics${params.size ? `?${params}` : ''}`)
```

原则：

- 只携带真实存在的 topologyId/runId；无 runId 时不写空值或虚构默认 Run。
- Analytics 返回地址保持 `/simulation?topologyId=<id>`；返回不需要带 runId，因为当前 store 会在同一会话保留它，刷新后则不应假装可恢复内存 Run。
- 如要求刷新后也恢复 runId，必须在后续单独设计 SimulationPage 对 `runId` query 的安全恢复；当前页面只读取 `topologyId`，本 Gate 不扩大范围。
- `TransferInventoryPanel` 暂时保留源文件和业务方法。入口替换后是否需要另一个库存入口，应另开 UX 审计，不在 I3 删除它。

## 8. 模拟实验资源核查

| 文件 | 大小（byte） | SHA256 | 元数据/质量 |
|---|---:|---|---|
| `abc_heat_seed42.json` | 765,532 | `3638D1FEEE9E36286952592010FEFBD7AB384571746F3DC88642BE05F5A37EBB` | SYNTHETIC；analytics-synthetic-v1；seed 42；`PASS` |
| `wave_optimization_seed42.json` | 58,586 | `F51D3FAFCF0AF9AEF5FC878706438DC37EBC9EB705EB78F1791FF95034BB5D45` | SYNTHETIC；analytics-synthetic-v1；seed 42；`PASS` |
| `eta_prediction_seed42.json` | 43,458 | `59722BD6EC84F5971D336C2EE7D76A2BB261400F8F5C5CE6E4EC9FF4A26A3CF5` | SYNTHETIC；analytics-synthetic-v1；seed 42；`PASS_WITH_STRICT_TARGET_AVAILABILITY_CUTOFF` |

三份 `experiments/.../output` 原件与来源 `frontend/public/analytics-synthetic-v1` 副本 SHA 和字节数分别一致。目标分支目前没有 `frontend/public/analytics-synthetic-v1`。I2 应从来源 public 目录逐字节新增并复核 SHA；不得修改 JSON、写入 SQLite 或绑定为真实 runId。

## 9. 精确移植清单

### A. 可以直接新增的独立文件

| 来源 | 目标 | 方式 | 依赖/测试 | 风险 |
|---|---|---|---|---|
| `frontend/src/pages/AnalyticsPage.tsx` | 同路径 | 新增，导入后编译核对 | 依赖 api/types/store；AnalyticsPage 测试 | 中 |
| `frontend/src/pages/AnalyticsPage.css` | 同路径 | 新增 | 响应式人工验收 | 低 |
| `frontend/src/pages/AnalyticsPage.test.tsx` | 同路径 | 新增后适配目标 mocks | Vitest | 低 |
| 来源 public 三份 JSON | `frontend/public/analytics-synthetic-v1/` | 字节复制并比 SHA | 静态 GET/404/元数据测试 | 低 |
| 五个指标测试及 `test_analytics_metrics_history.py` | `backend/tests/` 同名 | 新增，全部保持 tmp_path 隔离 | 预期五接口既有基线 26 项 | 低至中 |
| `docs/analytics/metric-contract-v1.md` | 同路径 | 可新增文档 | 人工审阅 | 低 |

### B. 必须人工审计后局部合并

| 来源 | 目标 | 允许合并范围 | 冲突/依赖 | 风险 |
|---|---|---|---|---|
| `backend/app/core/repository.py` | 同路径 | 三个纯助手、history、history-quality、五个指标只读方法 | 加 `math`；保留全部原写方法；目标事件表无索引 | 高 |
| `backend/app/api/routes.py` | 同路径 | 六个只读 GET（五接口 + 兼容 history） | 仅使用现有 `repo`；不改仿真路由 | 中 |
| `frontend/src/App.tsx` | 同路径 | Analytics import 和单一路由 | App route 测试 | 低 |
| `frontend/src/lib/api.ts` | 同路径 | 指标 GET、静态 GET、元数据校验 | 保留目标所有业务请求 | 中 |
| `frontend/src/types.ts` | 同路径 | 追加指标/实验类型；可选 metrics/plan 字段 | 目标有较新订单/子任务类型，绝不能替换 | 高 |
| `frontend/src/App.routes.test.tsx` | 同路径 | 加 analytics route 用例 | 保留现有 route 用例 | 低 |
| `frontend/src/pages/SimulationPage.tsx` | 同路径 | I3 仅改第 597 行附近入口 | 目标业务显著更新；不可采用来源版本 | 高 |
| `frontend/src/pages/SimulationPage.test.tsx` | 同路径 | I3 仅加导航、参数和无 runId 用例 | 保留目标现有测试 | 中 |

### C. 严禁覆盖或移植业务实现

- `backend/app/core/db.py`：目标已有 read-only 连接；不得用来源覆盖或顺带建索引。
- `backend/app/main.py`、`backend/app/services/runtime.py`、`seeder.py`。
- `simulation.py`、`assignment.py`、`pathfinding.py`、`shuttle_pathfinding.py`、`unified_transfer.py`、`conveyor.py`。
- `backend/app/schemas.py`。
- 整个 `frontend/src/pages/SimulationPage.tsx`、`types.ts`、`api.ts`、`App.tsx` 文件覆盖。
- 订单、库存、机械臂、3D、Shuttle/Lift/Conveyor 组件和测试。
- 任何 `.db`、`-wal`、`-shm` 文件以及来源历史 Run。

### D. 暂不纳入本轮集成

- S1/S2 数据生成和模型训练 Python 脚本：运行时页面只需要经过验证的静态结果；算法源码可另做实验归档 Gate。
- P1-2 blocked-presence API、测试和契约：本地已实现，但不属于已审批的五接口前端集成，建议 I1 后单独决定。
- P1-1/P1-2 诊断页面：当前 Analytics 未展示，不应借 I2 扩展功能。
- 来源业务数据库与参考 Run：不得迁移。

## 10. 目标分支测试基线审计

静态盘点结果：

- 后端：7 个测试文件，约 124 个顶层 `test_*` 函数。
- 前端：18 个测试文件，约 111 个 `it/test` 声明。
- 3D/运输相关测试已包含 `SimulationPage`、`TopologyPage`、`topology3d.coordinate`、`topologyInstanceBatches`、`transportLayout`、三项 picking-arm 测试、`TransferInventoryPanel`、`CrossLevelTransferPanel` 和后端 unified-transfer/conveyor 生命周期测试。
- 目标没有 CI workflow；测试入口来自 `pytest` 和 package scripts `test/build/lint`。

本 Gate 未宣称这些测试通过：

1. 后端现有 `test_backend.py` 等在模块顶层导入 `app.main`，而测试套件没有统一 `tmp_path/DB_PATH` fixture；运行会在目标 `backend/app_data` 创建数据库并 seed，违反本 Gate 文件白名单。
2. 目标前端没有 `node_modules`；安装会修改集成目录，build 还会生成 `dist`。

I1 应先新增专用隔离 fixture 或仅运行来源的独立指标测试；I4 再在一致的临时数据库与安装完成的受控环境中建立真实基线。不得把“发现测试文件”写成“测试通过”。

## 11. I1–I4 分阶段执行计划

### I1 — 后端只读指标

**白名单**

- `backend/app/core/repository.py`：仅增加 `math`、三个纯任务助手、`get_metrics_history`、五个指标方法。
- `backend/app/api/routes.py`：仅增加六个 GET 路由（五指标 + 兼容 history）。
- `backend/tests/test_metric_contract_tasks.py`
- `backend/tests/test_metric_history_quality.py`
- `backend/tests/test_metric_assignment_contention.py`
- `backend/tests/test_metric_path_planning.py`
- `backend/tests/test_metric_conveyor_coordination.py`
- `backend/tests/test_analytics_metrics_history.py`

**禁止区域**：`get_conn/init_db`、Repository 现有写方法、所有仿真/事件产生函数、Schema、main/runtime、数据库文件。P1-2 不自动加入。

**自动化测试**：语法检查；六个测试文件全部先 monkeypatch 临时 DB；验证 404、run 隔离、重复查询不写库、原 `/metrics` 行为；故意写入敏感性；确认目标业务 DB 路径未出现。

**人工验收**：在 SQLite 安全备份副本上 GET 已有 Run；确认 conveyor occupancy 的预期 null 降级。

**回滚**：撤销 repository/routes 两处独立 hunk并删除新增测试；无 Schema/数据回滚。

**停止条件**：必须改仿真事件才能出值、任何 GET 调用写路径、临时 DB 隔离无法证明、目标分支 HEAD 改变未重新审计、返回结构与契约不一致。

### I2 — Analytics 页面与模拟资源

**白名单**

- 新增 `AnalyticsPage.tsx/.css/.test.tsx`；
- 局部修改 `App.tsx`、`App.routes.test.tsx`、`lib/api.ts`、`types.ts`；
- 新增 public 三份 JSON。

**禁止区域**：SimulationPage、store、package/lock/config、其他页面、后端、实验原件。

**自动化测试**：Analytics 隔离测试；App routes；全前端 test/build/lint；Network mock 断言真实模式只调用五 GET、synthetic 模式只 GET 静态 JSON、无写请求；JSON SHA 复核。

**人工验收**：无 runId 可切换 synthetic；真实/模拟模式不串数据；null 不变 0；1920×1080 与 1366×768。

**回滚**：删除新增页面/public 文件，撤销 App/api/types 的局部追加。

**停止条件**：JSON hash/元数据不符、需要改后端业务接口、类型合并要求删除目标字段、构建或旧页面回归失败。

### I3 — “库存详情”替换为“性能分析”入口

**白名单**

- `frontend/src/pages/SimulationPage.tsx`：只改工作台按钮及 URL 参数构造。
- `frontend/src/pages/SimulationPage.test.tsx`：只增导航用例。

**禁止区域**：库存 Schema/API/组件、3D 库存渲染、订单/跨层运输/机械臂逻辑、Analytics 指标实现。

**自动化测试**：有/无 runId、有 topologyId、URL 编码、返回工作台、原订单与跨层 tab、TransferInventoryPanel 文件仍存在；全前端回归。

**人工验收**：从正在运行、暂停、无 Run 三种状态进入；返回保留 topologyId；不触发任何写请求。

**回滚**：只恢复单个按钮 hunk；库存代码从未删除。

**停止条件**：需要修改 store 持久化或运行状态机、入口导致 Run 重置、原订单/跨层 tab 回归、业务写请求出现。

### I4 — 全量回归与 PR 集成

**白名单**：原则上无功能代码；只允许经人工批准的测试/文档修正。Git commit/push/PR 必须再次获得明确授权。

**自动化测试**：I1 指标测试、目标全部后端测试（统一临时 DB）、全部前端测试、build、lint；静态 JSON hash；核心文件 hash；GET 写入敏感性；浏览器 Network/Console。

**人工验收**：3D 仓库、订单拆分、CTU/Shuttle/Lift、机械臂、库存、真实 Run 指标、synthetic 6.1/6.2/6.5、入口往返、双分辨率。

**回滚**：按独立 Gate 提交逆序 revert；不得 reset/clean；无数据库迁移可回滚。

**停止条件**：核心仿真 hash 出现未批准变化、实际数据库写入、参考 Run 被推进、五 GET 以外出现业务行为变化、浏览器存在阻断错误、远端 HEAD 变化未 rebase-free 重新审计。

## 12. 风险与待确认事项

1. **高：目标业务代码领先来源性能副本。** 任何整文件复制都可能删除按件/按箱、子任务和机械臂放置区功能。
2. **中：输送节点占用率降级。** 目标没有 `conveyorGraphNodes`；正式集成后页面应显示缺失原因而不是 0%。
3. **中：运行中任务指标存在持久化延迟。** 目标全量状态按 10 Tick 落库；最终 Run 不受该延迟影响。
4. **中：事件表缺少来源索引。** 数据量大时历史/质量查询可能慢；I1 不允许用 Schema 变更解决。
5. **中：启动与测试副作用。** runtime import 即 seed；测试隔离顺序错误会创建或修改目标 DB。
6. **中：库存入口可达性。** 按需求替换后 TransferInventoryPanel 可能暂时没有 UI 入口，但库存业务和 3D 逻辑必须保留；需产品确认是否另设入口。
7. **低：Analytics 的返回只恢复 topologyId。** 同会话 runId 在 store 中保留；浏览器刷新后不恢复，除非后续明确设计 URL 恢复。
8. **低：静态资源体积。** 三份约 0.87 MB，synthetic 模式懒加载可接受；不得在真实 Run 模式预取。
9. **范围：P1-2。** 后端已实现但前端未集成，若要迁移必须另行审批，不可在 I1/I2 顺带加入。

## 13. 本 Gate 安全声明

- 未修改任何业务代码、前端源码、配置、依赖或数据库。
- 未访问或复制来源真实 SQLite 数据库内容；未启动后端或真实仿真。
- 未创建、恢复、推进、重置或删除 Run。
- 未执行 Git commit、push、merge、rebase、reset、clean、checkout 或 PR 操作。
- 唯一项目内容变更为本审计文档。
- 本 Gate 到此停止，不进入 I1。
