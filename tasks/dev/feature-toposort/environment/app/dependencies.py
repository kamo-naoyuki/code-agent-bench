def dependency_order(graph):
    raise NotImplementedError


def node_count(graph):
    return len(set(graph).union(*(set(v) for v in graph.values())))
