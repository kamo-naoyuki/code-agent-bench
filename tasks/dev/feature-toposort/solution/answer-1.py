def dependency_order(graph):
    remaining = {node: set(deps) for node, deps in graph.items()}
    for node in set().union(*remaining.values()):
        remaining.setdefault(node, set())
    result = []
    while remaining:
        ready = sorted(node for node, deps in remaining.items() if not deps)
        if not ready:
            raise ValueError("Cycle")
        node = ready[0]
        result.append(node)
        del remaining[node]
        for deps in remaining.values():
            deps.discard(node)
    return result


def node_count(graph):
    return len(set(graph).union(*(set(v) for v in graph.values())))
