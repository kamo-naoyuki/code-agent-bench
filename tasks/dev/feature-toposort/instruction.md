# Deterministic dependency order

Implement `dependency_order(graph)` in `/app/dependencies.py`.
`graph` maps string node names to lists of prerequisites.
Include nodes mentioned only as prerequisites. Return each node once, after all
its prerequisites. Whenever several nodes are ready, choose the lexicographically
smallest available node. Duplicate dependencies count only once.
Raise `ValueError` for any cycle, including self-dependencies.
Empty input returns `[]`. Do not mutate the graph or its lists.
Keep `node_count(graph)` counting the union of keys and prerequisites.
Change only `dependencies.py`.
