"""LangGraph 節點：格式化並輸出環境建置指南（單份輸出版）。

從 State 中取得 ``env_guide_content``，以 ``aiofiles`` 非同步寫出
``{project_name}_env_guide.md`` 單一文件，並回寫輸出路徑至 State。

與 ``format_output_node`` 的差異：
    - 此節點**只寫出環境建置指南**，不產生任何 API 文件佔位文件。
    - 適用於 ``env_guide_graph``（單獨生成環境指南的精簡管線）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiofiles

from app.application.graphs.state import GraphState

logger = logging.getLogger(__name__)


async def format_env_guide_output_node(state: GraphState) -> GraphState:
    """將環境建置指南非同步寫入磁碟，並回傳輸出路徑至 State。

    **邏輯重點**：直接操作 ``aiofiles``，只寫出 ``env_guide_content`` 單份文件，
    不會產生任何 API 文件的空白佔位文件（與複用 ReportWriter 的方案相比更乾淨）。

    Args:
        state: 需包含 ``env_guide_content``、``output_dir`` 與 ``project_name`` 欄位。

    Returns:
        更新後的 :class:`~app.application.graphs.state.GraphState`，
        新增了 ``env_guide_output_path`` 欄位。

    Raises:
        OSError: 若目錄建立或文件寫入時發生 I/O 錯誤。
    """
    logger.info("[format_env_guide_output_node] 開始寫出環境建置指南...")

    project_name = state["project_name"]
    output_dir = Path(state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = project_name.replace(" ", "_").replace("/", "_")
    env_guide_path = output_dir / f"{safe_name}_env_guide.md"

    async with aiofiles.open(env_guide_path, mode="w", encoding="utf-8") as f:
        await f.write(state["env_guide_content"])

    logger.info(
        "[format_env_guide_output_node] 寫出完成 → %s (%d bytes)",
        env_guide_path,
        env_guide_path.stat().st_size,
    )

    return {"env_guide_output_path": str(env_guide_path)}
