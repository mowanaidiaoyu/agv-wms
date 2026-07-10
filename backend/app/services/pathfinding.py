from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any


def build_graph(topology: dict[str, Any]) -> dict[str, list[tuple[str, float, dict[str, Any]]]]:
    graph: dict[str, list[tuple[str, float, dict[str, Any]]]] = defaultdict(list)
    node_lookup = {node["id"]: node for node in topology["nodes"]}
    for edge in topology["edges"]:
        src = edge["from"]
        dst = edge["to"]
        length = float(edge.get("length", 1))
        graph[src].append((dst, length, edge))
        if edge.get("bidirectional", True):
            reverse = dict(edge)
            reverse["from"] = dst
            reverse["to"] = src
            graph[dst].append((src, length, reverse))
    for node_id in node_lookup:
        graph.setdefault(node_id, [])
    return graph


def heuristic(node_a: dict[str, Any], node_b: dict[str, Any]) -> float:
    return ((node_a["x"] - node_b["x"]) ** 2 + (node_a["y"] - node_b["y"]) ** 2) ** 0.5


def reconstruct_path(came_from: dict[str, str], current: str) -> list[str]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    return list(reversed(path))


def shortest_path(topology: dict[str, Any], start: str, goal: str, policy: str = "astar") -> list[str]:
    if start == goal:
        return [start]
    node_lookup = {node["id"]: node for node in topology["nodes"]}
    graph = build_graph(topology)
    frontier: list[tuple[float, str]] = [(0.0, start)]
    came_from: dict[str, str] = {}
    cost_so_far: dict[str, float] = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            return reconstruct_path(came_from, current)
        for next_node, length, _ in graph[current]:
            new_cost = cost_so_far[current] + length
            if next_node not in cost_so_far or new_cost < cost_so_far[next_node]:
                cost_so_far[next_node] = new_cost
                if policy == "astar":
                    priority = new_cost + heuristic(node_lookup[next_node], node_lookup[goal])
                else:
                    priority = new_cost
                heapq.heappush(frontier, (priority, next_node))
                came_from[next_node] = current
    return [start]
