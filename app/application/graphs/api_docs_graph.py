"""LangGraph API 文件生成管線的圖定義 (API Docs Graph Builder)。

此模組建構並編譯只生成「API 使用文件」的精簡 LangGraph StateGraph，
省略環境建置指南相關節點，縮短管線執行時間並降低 LLM Token 消耗。

管線拓撲（DAG）：

    START
      │
    parse_markdown_node         ← 讀取 .md 來源文件（或接受 in-memory content）
      │
    map_reduce_node             ← 大型專案 Map-Reduce 分批摘要（小型專案 passthrough）
      │
    generate_api_docs_node      ← AI 生成 API 使用文件（三階段分批）
      │
    format_api_docs_output_node ← 只寫出 API 文件單份，不產生環境指南佔位文件
      │
     END
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.application.graphs.state import GraphState
from app.application.graphs.nodes.parse_markdown_node import parse_markdown_node
from app.application.graphs.nodes.map_reduce_node import map_reduce_node
from app.application.graphs.nodes.generate_api_docs_node import generate_api_docs_node
from app.application.graphs.nodes.format_api_docs_output_node import format_api_docs_output_node

logger = logging.getLogger(__name__)


def build_api_docs_graph() -> StateGraph:
    """建構並編譯只生成 API 使用文件的精簡 LangGraph StateGraph。

    **邏輯重點**：
    - 管線拓撲為線性（無分支），執行路徑唯一。
    - ``map_reduce_node`` 保留，確保大型專案與完整管線行為一致。
    - ``generate_env_guide_node`` 與 ``format_output_node`` 被完全移除，
      改由 ``format_api_docs_output_node`` 只寫出 API 文件。

    Returns:
        已編譯完成的 LangGraph 可執行圖實例。

    Example::

        graph = build_api_docs_graph()
        result = await graph.ainvoke({
            "project_name": "my-project",
            "markdown_content": "...",
            "source_md_path": "<in-memory:my_project.zip>",
            "output_dir": "/path/to/output",
        })
        print(result["api_docs_content"])
        print(result["api_docs_output_path"])
    """
    builder = StateGraph(GraphState)

    # ── 節點註冊 ──────────────────────────────────────────────────────────────
    builder.add_node("parse_markdown", parse_markdown_node)
    builder.add_node("map_reduce", map_reduce_node)
    builder.add_node("generate_api_docs", generate_api_docs_node)
    builder.add_node("format_api_docs_output", format_api_docs_output_node)

    # ── 線性邊連接 ────────────────────────────────────────────────────────────
    builder.add_edge(START, "parse_markdown")
    builder.add_edge("parse_markdown", "map_reduce")
    builder.add_edge("map_reduce", "generate_api_docs")
    builder.add_edge("generate_api_docs", "format_api_docs_output")
    builder.add_edge("format_api_docs_output", END)

    compiled = builder.compile()
    logger.info("API 文件生成管線 StateGraph 編譯完成。")
    return compiled
