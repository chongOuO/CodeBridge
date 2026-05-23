"""LangGraph 環境建置指南生成管線的圖定義 (Env Guide Graph Builder)。

此模組建構並編譯只生成「環境建置指南」的精簡 LangGraph StateGraph，
省略 API 文件相關節點，縮短管線執行時間並降低 LLM Token 消耗。

管線拓撲（DAG）：

    START
      │
    parse_markdown_node          ← 讀取 .md 來源文件（或接受 in-memory content）
      │
    map_reduce_node              ← 大型專案 Map-Reduce 分批摘要（小型專案 passthrough）
      │
    generate_env_guide_node      ← AI 生成環境建置指南
      │
    format_env_guide_output_node ← 只寫出環境指南單份，不產生 API 文件佔位文件
      │
     END
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.application.graphs.state import GraphState
from app.application.graphs.nodes.parse_markdown_node import parse_markdown_node
from app.application.graphs.nodes.map_reduce_node import map_reduce_node
from app.application.graphs.nodes.generate_env_guide_node import generate_env_guide_node
from app.application.graphs.nodes.format_env_guide_output_node import format_env_guide_output_node

logger = logging.getLogger(__name__)


def build_env_guide_graph() -> StateGraph:
    """建構並編譯只生成環境建置指南的精簡 LangGraph StateGraph。

    **邏輯重點**：
    - 管線拓撲為線性（無分支），執行路徑唯一。
    - ``map_reduce_node`` 保留，確保大型專案與完整管線行為一致。
    - ``generate_api_docs_node`` 與 ``format_output_node`` 被完全移除，
      改由 ``format_env_guide_output_node`` 只寫出環境指南。

    Returns:
        已編譯完成的 LangGraph 可執行圖實例。

    Example::

        graph = build_env_guide_graph()
        result = await graph.ainvoke({
            "project_name": "my-project",
            "markdown_content": "...",
            "source_md_path": "<in-memory:my_project.zip>",
            "output_dir": "/path/to/output",
        })
        print(result["env_guide_content"])
        print(result["env_guide_output_path"])
    """
    builder = StateGraph(GraphState)

    # ── 節點註冊 ──────────────────────────────────────────────────────────────
    builder.add_node("parse_markdown", parse_markdown_node)
    builder.add_node("map_reduce", map_reduce_node)
    builder.add_node("generate_env_guide", generate_env_guide_node)
    builder.add_node("format_env_guide_output", format_env_guide_output_node)

    # ── 線性邊連接 ────────────────────────────────────────────────────────────
    builder.add_edge(START, "parse_markdown")
    builder.add_edge("parse_markdown", "map_reduce")
    builder.add_edge("map_reduce", "generate_env_guide")
    builder.add_edge("generate_env_guide", "format_env_guide_output")
    builder.add_edge("format_env_guide_output", END)

    compiled = builder.compile()
    logger.info("環境指南生成管線 StateGraph 編譯完成。")
    return compiled
