"""AI 助手：配置、工具集、agent 与 MCP 服务端。

分成三块，边界刻意划清：

* ``config``  —— 模型端点/密钥/审批策略，密钥加密落库
* ``tools``   —— 面板能力的工具化封装，只依赖服务层，不认识 pydantic-ai
* ``agent``   —— 把工具挂到 pydantic-ai 的 Agent 上，负责多轮与流式

``tools`` 不认识 agent、``agent`` 不认识 HTTP，所以同一批工具既给面板内置的
对话用，也给 ``python -m nautilus.mcp_server`` 暴露给外部 agent 用。
"""
