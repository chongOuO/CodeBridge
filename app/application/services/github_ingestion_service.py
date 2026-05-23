"""GitHub 來源輸入整合 Service (GitHub Ingestion Service)。

此 Application Service 的職責為：協調 :class:`GitHubDownloader` 與現有
:class:`FullPipelineService`，將 GitHub URL 作為輸入源標準化後，
無縫接入既有的「ZIP → Markdown → AI 文件」完整管線。

設計原則：
    - 本 Service 不含任何業務邏輯，僅負責輸入源轉接。
    - :class:`FullPipelineService` 零改動，維持單一職責原則。

管線流向：
    GitHub URL
      → GitHubDownloader.fetch()     # 下載並取得 zip_bytes
      → FullPipelineService.analyze_and_generate()  # 接入現有完整管線
      → AIReport                     # 與 ZIP 上傳管線回傳完全相同的結構
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.application.services.full_pipeline_service import FullPipelineService
from app.domain.entities.ai_report import AIReport
from app.infrastructure.adapters.github_downloader import GitHubDownloader

logger = logging.getLogger(__name__)


class GitHubIngestionService:
    """協調 GitHub 下載與現有 FullPipelineService 的 Application Service。

    使用者只需傳入 GitHub repo URL，本 Service 即完成下載、轉換、
    並觸發完整的 Phase 1 + Phase 2 管線，最終回傳兩份 AI 生成技術文件的路徑。

    Example::

        service = GitHubIngestionService()
        report = await service.analyze_from_github(
            github_url="https://github.com/tiangolo/fastapi",
            output_dir=Path("/tmp/output"),
        )
        print(report.api_docs_output_path)
        print(report.env_guide_output_path)
    """

    def __init__(self) -> None:
        """預先初始化 GitHubDownloader 與 FullPipelineService（Singleton 模式）。

        兩個依賴皆在應用啟動時初始化，LangGraph 只編譯一次，避免重複開銷。
        """
        self._downloader = GitHubDownloader()
        self._pipeline = FullPipelineService()
        logger.info("GitHubIngestionService 初始化完成。")

    async def analyze_from_github(
        self,
        github_url: str,
        output_dir: Path,
    ) -> AIReport:
        """從 GitHub URL 下載 repo 並執行完整分析管線。

        Args:
            github_url: 使用者提供的 GitHub repo URL（公開 repo）。
            output_dir: AI 生成的兩份 Markdown 文件的輸出目錄，不存在則自動建立。

        Returns:
            包含兩份文件輸出路徑的 :class:`~app.domain.entities.ai_report.AIReport` 實體，
            結構與 ZIP 上傳管線回傳完全相同。

        Raises:
            InvalidGitHubUrlError: URL 格式不符 GitHub repo 規範（422）。
            GitHubRepoNotFoundError: Repo 不存在或為私有（404）。
            FileSizeLimitExceededError: 下載的 ZIP 超過 50 MB（413）。
            GitHubDownloadError: 下載逾時或網路錯誤（502）。
            InvalidZipFileError: GitHub 回傳的 ZIP 無法解析（422）。
            EmptyProjectError: 過濾後無程式碼檔案（422）。
            LLMCallError: Gemini API 呼叫失敗（502）。
        """
        logger.info("[GitHubIngestion] 收到請求：%s", github_url)

        # ── 輸入源標準化：GitHub URL → zip_bytes ─────────────────────────────
        zip_bytes, filename = await self._downloader.fetch(github_url)
        logger.info(
            "[GitHubIngestion] 下載完成：%s（%d bytes），接入管線。",
            filename,
            len(zip_bytes),
        )

        # ── 接入現有完整管線（Phase 1 + Phase 2），零改動 ──────────────────────
        report: AIReport = await self._pipeline.analyze_and_generate(
            zip_bytes=zip_bytes,
            filename=filename,
            output_dir=output_dir,
        )

        logger.info(
            "[GitHubIngestion] 完成 → API 文件：%s | 環境指南：%s",
            report.api_docs_output_path,
            report.env_guide_output_path,
        )
        return report
