# C-INV-1D：666 CTU 通道语义审计与最小试验区草案

## 审计对象

- 仓库名称：666
- `topology_id`：`topology-6ceb4920`
- `topology_revision`：`2.0.0`
- 审计性质：只读拓扑审计；不修改 `docs/topologies/666.json`
- 结论：`UNVERIFIED`

## 3D 颜色的真实来源

`frontend/src/lib/topology3d.ts` 从同一份拓扑数据生成 3D 几何，但颜色不代表业务授权：

- 普通 `road` Cell：`#2e3a52`；
- 四向车库中的 `road` Cell：`#3a4f6e`；
- CTU Zone 的 17 层货架几何：`#0e7490`；
- Shuttle Zone 的货架几何：`#b45309`；
- `conveyor`：`#2f6f9f`；
- `blocked`：`#8b2f2f`。

因此，用户看到的蓝色/青色条状结构可能来自普通 road、四向车巷道或 CTU 货架 Zone。
不存在“看到蓝色就自动创建 CTU road”的规则。

## 真实 Road Cell 审计

当前 666 包含 1437 个 `road` Cell：

| 分类 | 数量 | 连通情况 | CTU 正式权限 |
| --- | ---: | --- | --- |
| CTU Zone road | 1 | 1 个孤立点 | 否 |
| Shuttle Zone road | 1416 | 1 个大连通分量 | 否 |
| 未归属 road | 20 | 最大分量 8 格，其余为 3/2/1 格小分量 | 否 |
| 其他区域 road | 0 | — | 否 |

唯一 CTU road 为 `cell-r37-c85`。它与 Storage Node 占位重叠，且没有连接到第二个 CTU road，
因此不能作为起点、通道或 Access Cell。

原 path-demo 可以规划，是因为演示服务从全部可通行 Cell 构建连通图，不按 `vehicle_type` 或 Zone 类型过滤。
其最大 road 连通分量实际来自四向车区域。库存取货规划则只接受经审核的 CTU road，所以 Access Audit 为 0。

## Storage Node 空间语义

- Storage Node 总数：2496；
- CTU Storage Node：2496；
- 位于其引用 CTU Zone 矩形范围内：2496；
- 拓扑显式声明 Pick Face：0；
- 拓扑显式声明 CTU 车体长度、宽度或安全占用：否；
- 可验证 Access Cell：0。

Storage Node 与 CTU Zone 的行列关系稳定，可用于定位货架和库存，但不足以证明车辆从哪一侧取货。
Zone 只有矩形边界，没有 `pick_face`、操作距离、通道宽度或车辆占用字段。

## CTU Road / Pick Face / Access Cell 契约

建议采用 `ctu-road-pick-contract/v1`：

```json
{
  "topology_id": "topology-6ceb4920",
  "topology_revision": "2.0.0",
  "zone_id": "zone-ctu-*",
  "road_cell_id": "cell-r*-c*",
  "vehicle_type": "ctu",
  "floor": "1F",
  "traversability": "unverified | walkable | blocked",
  "road_connectivity": {
    "component_id": "ctu-component-*",
    "cell_count": 0
  },
  "storage_node_id": "storage-*",
  "pick_face": "left | right | front | back | unknown",
  "access_cell_id": null,
  "mapping_source": "topology_audit_draft | researcher_b_verified",
  "review_status": "draft | verified | rejected"
}
```

- CTU Road：CTU 实际可行驶位置；
- Storage Node：商品所在物理库位；
- Pick Face：货架允许取货的一侧；
- Access Cell：车辆在完成取货操作时的合法停靠 Cell。

四者不能互相替代。只有与确切拓扑版本绑定的 `verified` 数据才能写入正式
`storage_access_mappings` 并进入取货路径规划。

## 最小试验区草案

现有真实数据只能构造以下审核草案，不能构造正式取货试验区：

- 状态：`UNVERIFIED / draft`；
- 真实道路：未归属 road 最大连通分量；
- Road Cell：`cell-r68-c8`～`cell-r68-c11`、`cell-r69-c8`～`cell-r69-c11`；
- 建议车辆草案起点：`cell-r68-c8`；
- 临近真实库位：
  - `storage-1609`，R63 C6，`zone-ctu-7`；
  - `storage-1632`，R62 C9，`zone-ctu-108`；
  - `storage-1633`，R63 C9，`zone-ctu-108`；
  - `storage-1656`，R62 C10，`zone-ctu-109`；
  - `storage-1657`，R63 C10，`zone-ctu-109`。

这些道路与库位都真实存在，但道路没有 `zoneId`，库位仅按几何距离选取，Pick Face 未知，
Access Cell 为空。因此草案的 `eligible_for_planning` 固定为 `false`。

草案保存在独立的 `ctu_topology_drafts` 表中，不会修改拓扑、库存、正式 Access Mapping
或 path-demo 路径数据。拓扑版本变化后草案显示 `topology_version_mismatch`。

## 需要研究生 B 确认

1. 哪些蓝色或青色巷道在业务上对应 CTU road？请提供确切 Cell 范围。
2. 每条通道宽度是多少，允许单向还是双向，车辆占用多少格？
3. 每排红色货架允许从左、右、前、后哪一侧取货？
4. CTU 停靠时除 Access Cell 外是否需要预约相邻道路空间？
5. 哪些 CTU 巷道属于同一个可连通区域，入口和出口在哪里？
6. 不同货架层是否共用同一个平面 Access Cell？
7. 车体与货架之间是否有最小/最大操作距离或安全间距？
8. 是否需要增加正式站台、入口节点或车辆初始位置？
9. `cell-r68-c8`～`cell-r69-c11` 是否确实属于 CTU 区域？若是，应在新拓扑版本中补充 Zone 归属。
10. `cell-r37-c85` 是误绘道路还是缺失相邻 CTU 通道的一部分？

研究生 B 确认后，应由拓扑负责人发布新 revision；研究生 C 再运行同一审计，不能直接修改
当前 2.0.0 数据或把 draft 改名为 verified。
