# 修改清单：ShuttleGraph 跨层运输读取性能优化

- 日期：2026-10-26
- 主题：修复跨层运输面板读取 ShuttleGraph（`GET /shuttle/topologies/{id}/contract`）耗时约 5.4 秒的问题；顺带修复既有的轨道合同 revision 戳失配导致的 4 项测试失败与线上 409。

---

## 背景

用户反馈跨层运输面板读取 ShuttleGraph 很慢。基准实测（真实拓扑 `topology-6ceb4920`，2474 cells / 2536 nodes / 182 zones / 1402 轨道节点）：

| 阶段 | 修复前 | 修复后 |
| --- | --- | --- |
| `build_shuttle_track_graph` | ~35 ms | ~38 ms（不变） |
| `scan_lift_candidates`（334 个候选） | ~2650 ms | ~35 ms |
| `validate_shuttle_configuration`（内部再扫一遍） | ~2650 ms | ~85 ms |
| `shuttle_contract` 端到端（冷） | **~5400 ms** | **~230 ms** |
| `shuttle_contract` 端到端（缓存命中） | ~5400 ms（无缓存） | **~50 ms** |

## 根因

1. **电梯候选扫描的复杂度爆炸**：`scan_lift_candidates` 对 L1 层全部 334 个候选坐标逐个调用 `_lift_footprint_is_clear`，而该函数内部每次调用都重建两遍全图 `_cell_lookup`（O(2474) 字典构建 ×2）并线性扫描全部 2536 个 nodes 查重叠 —— 合计约 334 × (2×2474 + 2536) 次操作，Python 下约 2.6 秒。
2. **同一次请求扫描两遍**：`validate_shuttle_configuration` 内部又调一次 `scan_lift_candidates`，成本翻倍。
3. **无缓存**：`shuttle_context()` 每次调用都全量重建图 + 验证，contract 接口、每次路径预览（`plan_transfer`）、运行时每条跨层任务（`build_transfer_runtime`）都重复付费。

## 改动

### `backend/app/services/shuttle_topology.py`

- 新增 `_node_coords(topology)`：把 `topology["nodes"]` 的 (row, col) 预建成 set，O(1) 查重叠。
- `_lift_footprint_is_clear` 新增可选参数 `cells` / `node_coords`：调用方传入复用；不再每次重建两遍 `_cell_lookup`、不再逐节点线性扫描（语义等价：原逻辑 `node.get("row") in {row, row+1}` 与 2×2 足迹逐格 set 成员判断一致）。
- `scan_lift_candidates`：进入循环前构建一次 `_cell_lookup` + `_node_coords`，传给每个候选的足迹检查。
- `validate_shuttle_configuration`：同样构建一次并传给 `scan_lift_candidates` 及自身的足迹复核。

### `backend/app/services/unified_transfer.py`

- 新增 `_context_cache`：以 `(topology_id, topology_revision, config_revision)` 为键缓存 `(graph, validation)`，拓扑编辑（revision 变）或配置文件变更（config_revision 变）自动失效；上限 16 条防无限增长；验证失败（planning_enabled=False）的路径不缓存、保持抛错。
- `shuttle_context` 先查缓存再重建；`shuttle_contract` / `plan_transfer` / `build_transfer_runtime` 全部自动受益（已确认 `plan_shuttle_path` 只读取 graph、不改写节点，缓存共享安全）。

### 数据修复（遵循"验证电梯落点有效后更新戳，不删配置"的既有约定）

- 实测两台电梯在 DB 与测试夹具拓扑上足迹均有效（2×2 全通行 road 格、L1–L4 入口节点存在、同层度数 ≥3）。
- `data/topology_666.json`：同步为当前 DB 载荷（差异仅为用户后来在编辑器里新增/修改的 4 个 port 区域，cells/nodes 完全一致）。
- `data/shuttle_configs/topology-6ceb4920.shuttle-v2.json`：`source_topology_revision` 更新为 `2.0.0:8eae0faf1dd8b148`（与 DB 及同步后的夹具一致），线上 contract 接口恢复 `planningEnabled=True`。
- `backend/tests/test_unified_transfer.py`：revision 断言同步更新。

> 说明：戳失配是改动前就存在的问题（stash 验证：改前同样 4 项失败、改前套件耗时 15.5s vs 改后 2.9s）。该失配同时导致线上接口 5.4 秒后才返回 409，是"读取很久"的直接体验来源之一。

## 验证

- 基准脚本分段计时：scan 2650ms → 35ms；validate 2650ms → 85ms；冷调用 5400ms → 230ms；缓存命中 ~50ms（残余成本为 900KB 拓扑 JSON 的 revision 哈希与 tracks 排序）。
- 后端全量 `pytest -q`：**69 passed**（含此前失败的 4 项 `test_unified_transfer.py`）。
- 端到端 `shuttle_contract(DB 载荷)` 连续 4 次调用：229 / 54 / 42 / 52 ms，`planningEnabled=True`，Lift-A/Lift-B 正常返回。

## 风险与后续

- 缓存以内容哈希为键，拓扑保存或配置文件更新自动失效，无脏读风险；缓存命中时仍有 ~50ms 的 revision 哈希开销（900KB JSON dumps+sha256），如需再降可将 revision 元信息随拓扑保存时预计算落库，当前对面板加载已足够。
- 响应体仍全量返回 1402 个轨道节点（约数百 KB JSON）；如后续拓扑继续增大，可考虑按需精简字段或分页。
