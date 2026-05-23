"""Application 層 API 文件生成 Service (ApiDocsService)。

串聯 Phase 1（ZIP → Markdown）與 Phase 2（Markdown → API 文件）兩個管線，
讓呼叫端只需傳入 ZIP 壓縮檔，即可取得**單份** AI 生成的 API 使用文件。

與 :class:`FullPipelineService` 的差異：
    - 只執行 ``api_docs_graph``，不觸發環境指南生成節點。
    - 執行時間約為完整管線的一半，Token 消耗減少約 50%。

管線流向：
    ZIP bytes
      → ProjectAnalysisService.analyze()        # Phase 1：解析、過濾、建樹
      → ProjectAnalysisService.render_markdown() # Phase 1：渲染 Markdown 字串
      → api_docs_graph.ainvoke()                 # Phase 2：只生成 API 文件
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.application.graphs.api_docs_graph import build_api_docs_graph
from app.application.services.project_analysis_service import ProjectAnalysisService
from app.domain.entities.ai_report import AnalysisSection, SectionType
from app.infrastructure.adapters.file_filter import FilterMode

logger = logging.getLogger(__name__)


class ApiDocsService:
    """只生成 API 使用文件的 Application Service。

    透過注入 ``api_docs_graph`` 只執行 API 文件分支，
    對 Router 層隱藏 Phase 1 + Phase 2 的內部細節。

    Example::

        service = ApiDocsService()
        section = await service.generate(
            zip_bytes=b"...",
            filename="my_project.zip",
            output_dir=Path("/tmp/output"),
        )
        print(section.content)
        print(section.word_count)
    """

    def __init__(self) -> None:
        """預先初始化 Phase 1 Service 並編譯 API 文件 LangGraph。

        **邏輯重點**：Graph 在 Service 初始化時一次性編譯完成，
        後續每次請求只需執行 ``ainvoke()``，避免重複編譯開銷。
        """
        self._phase1 = ProjectAnalysisService()
        self._graph = build_api_docs_graph()
        logger.info("ApiDocsService 初始化完成，API 文件 LangGraph 已編譯。")

    async def generate(
        self,
        zip_bytes: bytes,
        filename: str,
        output_dir: Path,
    ) -> AnalysisSection:
        """執行 Phase 1 + API 文件 Phase 2 管線，回傳 API 文件 Section 實體。

        Args:
            zip_bytes: 上傳的 ZIP 檔案之原始位元組資料。
            filename: 上傳時的原始檔名（例如：``\"my_project.zip\"``）。
            output_dir: AI 生成的 API 文件的輸出目錄，不存在則自動建立。

        Returns:
            包含 API 使用文件內容的 :class:`~app.domain.entities.ai_report.AnalysisSection`，
            ``content`` 為完整 Markdown 文字，``word_count`` 為粗略字數估算。

        Raises:
            FileSizeLimitExceededError: ZIP 超過 50 MB。
            InvalidZipFileError: 上傳的不是有效 ZIP。
            EmptyProjectError: 過濾後無程式碼檔案。
            LLMCallError: Gemini API 呼叫失敗。
        """
        # ── Phase 1：ZIP → Markdown 字串 ──────────────────────────────────────
        logger.info("[ApiDocsService] Phase 1 啟動：%s", filename)
        structure = await self._phase1.analyze(
            zip_bytes=zip_bytes,
            filename=filename,
            filter_mode=FilterMode.API_DOCS,
        )
        markdown_content = self._phase1.render_markdown(structure)
        project_name = structure.project_name
        logger.info(
            "[ApiDocsService] Phase 1 完成：%s（%d 個程式碼檔案，Markdown %d 字元）",
            project_name,
            structure.code_file_count,
            len(markdown_content),
        )

        # ── Phase 2：Markdown → API 文件（只跑 api_docs_graph）────────────────
        logger.info("[ApiDocsService] Phase 2 啟動：API 文件 LangGraph 管線")
        initial_state = {
            "project_name": project_name,
            "markdown_content": markdown_content,
            "source_md_path": f"<in-memory:{filename}>",
            "output_dir": str(output_dir.resolve()),
        }

        final_state = await self._graph.ainvoke(initial_state)

        section = AnalysisSection(
            title="API 使用文件",
            content=final_state.get("api_docs_content", ""),
            section_type=SectionType.API_DOCS,
        )

        logger.info(
            "[ApiDocsService] 完成：project=%s，API 文件 %d 字元，輸出路徑：%s",
            project_name,
            len(section.content),
            final_state.get("api_docs_output_path", "（無磁碟輸出）"),
        )
        return section
