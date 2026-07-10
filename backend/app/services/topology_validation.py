from __future__ import annotations

from typing import Any


def validate_topology_business_rules(topology: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    nodes = topology.get("nodes", [])
    edges = topology.get("edges", [])
    resources = topology.get("resources", [])

    node_types = {node.get("type") for node in nodes}
    edge_types = {edge.get("type") for edge in edges}
    resource_types = {resource.get("type") for resource in resources}

    stations = [node for node in nodes if node.get("type") == "station"]
    conveyor_nodes = [node for node in nodes if node.get("type") == "conveyor_gate"]
    entry_nodes = [node for node in nodes if node.get("type") == "entry"]
    buffers = [node for node in nodes if node.get("type") == "buffer"]

    if not nodes:
        errors.append("拓扑缺少节点。")
    if not stations:
        errors.append("拓扑缺少站台节点，无法完成交付。")
    if "conveyor" in edge_types and len(conveyor_nodes) < 2:
        errors.append("存在输送线边，但输送线入口/出口节点不足。")
    if "conveyor" in edge_types and "conveyor_segment" not in resource_types:
        warnings.append("存在输送线边，但尚未配置 conveyor_segment 资源。")
    if "cross_zone" in edge_types and not entry_nodes:
        warnings.append("存在跨区边，但没有入口或交接入口节点。")
    if "buffer" not in node_types and any(resource.get("type") == "buffer_slot" for resource in resources):
        warnings.append("定义了缓存位资源，但没有 buffer 节点。")
    if "buffer" in node_types and not any(resource.get("type") == "buffer_slot" for resource in resources):
        warnings.append("存在 buffer 节点，但没有对应的 buffer_slot 资源。")
    if "lift" in node_types and "lift" not in resource_types:
        warnings.append("存在提升机节点，但没有对应的 lift 资源。")

    conveyor_edges = [edge for edge in edges if edge.get("type") == "conveyor"]
    edge_ids = {edge.get("id") for edge in edges}
    for resource in resources:
        if resource.get("type") == "conveyor_segment":
            if not resource.get("edgeIds"):
                errors.append(f"输送线资源 {resource.get('id')} 未绑定任何边。")
            for edge_id in resource.get("edgeIds", []):
                if edge_id not in edge_ids:
                    errors.append(f"输送线资源 {resource.get('id')} 绑定了不存在的边 {edge_id}。")
        if resource.get("type") == "station" and not resource.get("nodeIds"):
            errors.append(f"站台资源 {resource.get('id')} 未绑定站台节点。")
        if resource.get("type") == "buffer_slot" and not resource.get("nodeIds"):
            errors.append(f"缓存位资源 {resource.get('id')} 未绑定 buffer 节点。")

    if conveyor_edges and len(buffers) == 0:
        warnings.append("当前输送线没有任何缓存位，拥堵场景可能缺少可解释性。")

    return {"valid": not errors, "errors": errors, "warnings": warnings}
