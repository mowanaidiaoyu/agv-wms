# Motion Distance Contract v1

## 1. Scope and status

This contract defines pure, deterministic distance calculations for future CTU and shuttle telemetry. It is not connected to `simulation.py`, a Run, an API, or a database in P3-1. It does not change routing, vehicle motion, speed, tick progression, or 3D rendering.

All metric values in examples are illustrative. They are not approved warehouse or device specifications.

## 2. Existing coordinate contract audit

- CTU positions use logical `row`, `col`, and `floor` values parsed from topology cell IDs. Current ordinary CTU neighbors are cardinal grid neighbors.
- Shuttle positions use `row`, `col`, and internal `layer`. In the formal shuttle graph, a track node is the center of a 2×2 logical slot. Adjacent graph nodes therefore normally differ by two rows or two columns.
- `frontend/src/lib/topology3d.ts` defines `CELL = 1` as one Three.js world-space length. It is a rendering scale, not evidence that one cell equals one physical meter.
- The frontend `FLOOR_HEIGHT` and current shuttle visualization level height are visual geometry constants, not approved physical dimensions.
- A planned path is a sequence of legal planner edges. It is not proof that every edge was executed.
- Current `robot_position` events contain `robotId`, `cellId`, `floor`, `row`, `col`, `layer`, and status after recorded moves. Normal moves, CTU backtracking, post-task retreat, and return-to-standby moves have such events.
- A CTU active yield currently has a separate `robot_yield` event with `fromCellId` and `toCellId`, but no accompanying `robot_position` event at that point. Shuttle head-on backoff changes state without an independent position event. Event type, previous coordinate, run generation, physics version, and stable movement ID therefore need explicit capture before complete actual mileage can be claimed.
- A missing intermediate movement event is a trajectory gap. The distance between the last known and next known positions must not be filled with a straight line.

## 3. Frozen physical parameters

Every calculation receives an explicit immutable `PhysicsContract`:

| JSON field | Meaning |
| --- | --- |
| `physicsContractVersion` | Stable identity of the complete physical parameter set |
| `metersPerCell` | Physical horizontal meters represented by one logical row/column grid unit |
| `shuttleLevelHeightMeters` | Physical vertical separation of adjacent shuttle internal levels |
| `floorHeightMeters` | Confirmed physical separation of adjacent warehouse floor indexes |
| `coordinateContractVersion` | Identity of the row/column/floor/layer interpretation |

Every numeric parameter must be a finite positive number. Boolean values, numeric strings, zero, negative values, NaN, and infinity are rejected. No default physical values are supplied by this module.

When runtime integration is implemented, the complete physical contract must be frozen into the Run generation at creation. Historical Runs without that frozen contract remain unavailable; they must not silently inherit `1 cell = 1 meter` or a later configuration.

## 4. Horizontal distance

For each verified same-floor edge:

```text
horizontalMeters = metersPerCell × hypot(deltaRow, deltaCol)
```

For CTUs, an adjacent cardinal move is accepted. A diagonal move requires an explicit verified topology edge and an explicit diagonal permission. A jump over more than one cardinal grid unit also requires an explicit verified edge; this module never treats a sparse pair of observations as a direct edge.

For shuttles, every non-stationary horizontal move requires a verified track edge. A normal edge between 2×2 track-node centers differs by two logical grid units, so its distance is:

```text
2 × metersPerCell
```

It is never calculated as “one event equals one cell.” A multi-edge path is the sum of its individual legal edges.

A verified stationary segment has a real distance of `0`. Waiting ticks alone do not create distance.

## 5. Lift and vertical distance

Shuttle internal level movement is calculated separately:

```text
verticalMeters = abs(deltaLayer) × shuttleLevelHeightMeters
```

Warehouse-floor movement requires explicit numeric floor indexes:

```text
verticalMeters = abs(deltaFloorIndex) × floorHeightMeters
```

A floor-label change without both floor indexes is `INCOMPLETE`, because labels such as `1F` and `B1` are not a safe arithmetic scale. Any vertical coordinate change must be identified as `movementType=LIFT`.

Horizontal vehicle travel and lift-carried vertical displacement remain separate fields. Lift displacement must not be presented as autonomous vehicle horizontal mileage. A combined equipment-motion total may be derived later only with a clearly named metric.

## 6. Movement-segment JSON contract

The result of a movement calculation is JSON serializable and includes:

```json
{
  "movementId": "example-only",
  "robotId": "ctu-example",
  "robotType": "ctu",
  "runId": "run-example",
  "runInstanceId": "generation-example",
  "fromPosition": {"row": 1.0, "col": 1.0, "floor": "1F", "layer": 1, "floorIndex": 1},
  "toPosition": {"row": 1.0, "col": 2.0, "floor": "1F", "layer": 1, "floorIndex": 1},
  "fromTick": 10,
  "toTick": 11,
  "movementType": "NORMAL",
  "segmentHorizontalMeters": null,
  "segmentVerticalMeters": null,
  "plannedPathId": "path-example",
  "replanId": null,
  "physicsContractVersion": "example-physics-version",
  "dataQuality": "UNAVAILABLE",
  "issue": "example values are not an approved physical contract"
}
```

Supported movement types are `NORMAL`, `YIELD`, `BACKOFF`, `RETURN_TO_STANDBY`, `LIFT`, and `UNKNOWN`. Actual distance must eventually include executed normal travel, rerouted travel, active yield, backoff, return to standby, loaded travel, and empty travel. Movement classification does not change geometry; it enables auditable subtotals.

## 7. Planned and actual distance

- `plannedHorizontalMeters` is the sum of legal edges in one identified planned path.
- `actualHorizontalMeters` is the sum of complete executed horizontal movement segments.
- `actualVerticalMeters` is the separate sum of complete lift-carried displacement.
- `extraDistanceMeters` is calculated only when planned and actual totals are complete and the actual segments are correlated to the same non-null planned path ID.
- `attributableReplanExtraMeters` remains `null` unless a future runtime contract proves which extra executed edges came from a specific replan. A positive actual-minus-planned difference alone does not prove replan causation.
- `distanceSource` states whether values came from planned path edges, actual movement segments, or a controlled comparison.
- `dataQuality` is `COMPLETE`, `INCOMPLETE`, `UNAVAILABLE`, or `INVALID`.

Planned distance and actual distance must not be merged into one numerator. Synthetic wave-picking grid proxy distances are not vehicle mileage.

## 8. Completeness, continuity, and deduplication

1. `movementId` is the idempotency key. Byte-for-model-identical duplicates are counted once and reported in `duplicateCount`.
2. Conflicting records with the same `movementId` are excluded and make the aggregate incomplete.
3. Within a Run generation, robot, and physics version, consecutive segments must satisfy `previous.toPosition == current.fromPosition`.
4. Missing positions or coordinates produce null segment distance and incomplete quality.
5. Reverse tick order is rejected. A non-stationary segment within one tick is rejected. Duplicate/non-increasing event ticks in a trajectory make the aggregate incomplete.
6. A trajectory gap retains the subtotal of independently known segments but marks it `INCOMPLETE`; it is never filled with endpoint distance.
7. An aggregate with no valid movement samples has null totals. It is not reported as a complete zero.
8. A complete stationary sample is a legitimate zero.
9. Aggregation keys isolate Run ID, Run instance/generation, robot, robot type, floor scope, and `physicsContractVersion`. Results from different physical versions must not be mixed.

The calculation functions materialize immutable results and do not mutate input objects.

## 9. Historical compatibility

Historical Runs need all of the following before formal physical distance can be reconstructed: a frozen physical contract, a known coordinate contract, stable movement IDs, complete ordered positions, Run generation identity, and explicit movement type. If any required element is absent, the affected metric remains null with its quality reason.

Existing rendering frames are not authoritative telemetry. Interpolated 3D positions may be used for presentation, but not as a replacement for executed logical movement events.

## 10. Runtime collection reserved for P3-2

P3-2 should create one immutable movement record at the point each logical move is committed, including old and new position, tick interval, movement type, active planned path/replan IDs, Run generation, and frozen physics version. It must cover CTU yield/backoff/standby movement and shuttle horizontal/lift/backoff movement without changing movement behavior.

Runtime collection should also preserve future speed-model inputs (for example elapsed simulated seconds and device speed-contract version), but P3-1 does not define speed, derive distance from time, or change playback multipliers.

## 11. Algorithm A/B experiments

Comparable experiments must share topology revision, coordinate contract, frozen physical parameters, initial inventory/order snapshot, random seed, device speed contract, scenario ID, completion criteria, and telemetry-completeness rules. Algorithm and implementation versions must be recorded. Planned distance, actual distance, lift displacement, and incomplete samples must be reported separately before any optimization rate is calculated.

This P3-1 module remains a standalone calculation contract until a separately reviewed runtime integration gate.
