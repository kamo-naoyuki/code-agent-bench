import heapq


def dependency_order(graph):
    nodes = set(graph).union(*(set(v) for v in graph.values()))
    edges = {node: set() for node in nodes}
    degree = dict.fromkeys(nodes, 0)
    for node, deps in graph.items():
        for dep in set(deps):
            edges[dep].add(node)
            degree[node] += 1
    ready = [node for node in nodes if degree[node] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        node = heapq.heappop(ready)
        result.append(node)
        for child in edges[node]:
            degree[child] -= 1
            if degree[child] == 0:
                heapq.heappush(ready, child)
    if len(result) != len(nodes):
        raise ValueError("Cycle")
    return result


def node_count(graph):
    return len(set(graph).union(*(set(v) for v in graph.values())))
