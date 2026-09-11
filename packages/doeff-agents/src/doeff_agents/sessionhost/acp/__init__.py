"""agentd — the sessionhost's arms into the ACP cluster (段 2・agora-redesign #19 / #20).

Sub-namespace of the sessionhost: ``effects`` (typed requests and values, no I/O),
``judgment`` (pure decisions, Hy ``defk``), ``agentd`` (the programs, Hy ``defk``),
``handlers`` (real HTTP / RPC / filesystem I/O), ``fake`` (in-memory handlers for
tests), ``valve`` (the on/off decision) and ``runtime`` (the composition root that
selects handlers and drives the loop).  The sessionhost's RPC and backends are not
changed: agentd is a client of the host's own socket.
"""
