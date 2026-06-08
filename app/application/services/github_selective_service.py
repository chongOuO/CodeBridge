"""GitHub 選擇性生成整合 Service (GitHub Selective Service)。

允許呼叫端指定要執行的生成管線清單（ai_docs、diagram、dockerfile），
系統只並行執行被選擇的管線，ZIP 只下載一次，三個管線共用同一份 bytes。

設計原則：
    - ``FullPipelineService``、``DiagramService``、``DockerfileService`` 零改動。
    - 並行執行（``asyncio.gather``）只包含被選擇的任務，節省資源。
    - 任一被選管線失敗時，``asyncio.gather`` 預設 fail-fast，
      拋出對應的 Domain Exception，由全域 handler 格式化後回傳。

管線流向：
    GitHub URL
      → GitHubDownloader.fetch()          # 下載一次，取得 zip_bytes
      → asyncio.gather(selected tasks)     # 只執行被選管線，並行
      → GitHubSelectiveResult              # 彙整結果，未選者為 None
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.api.schemas.response import GenerationType
from app.application.services.diagram_service import DiagramService
from app.application.services.dockerfile_service import DockerfileService
from app.application.services.api_docs_service import ApiDocsService
from app.application.services.env_guide_service import EnvGuideService
from app.domain.entities.ai_report import AnalysisSection
from app.api.schemas.diagram_schema import DiagramResponse
from app.api.schemas.dockerfile_schema import DockerfileResponse
from app.infrastructure.adapters.github_downloader import GitHubDownloader

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Service 層內部資料結構（Domain Model）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GitHubSelectiveResult:
    """``GitHubSelectiveService.analyze_selective`` 的回傳結果。

    Router 層將此結果對應至 ``GitHubSelectiveResponse`` Schema。
    未被選擇的管線欄位為 ``None``。

    Attributes:
        project_name: 由 GitHub repo 名稱推斷的專案名稱。
        github_url: 使用者提供的原始 GitHub repo URL。
        generated_types: 實際執行的生成管線清單。
        ai_docs_report: AI 技術文件管線結果；未選擇時為 None。（已廢棄）
        api_docs_section: API 使用文件管線結果；未選擇時為 None。
        env_guide_section: 環境建置指南管線結果；未選擇時為 None。
        diagram_response: Mermaid 圖表管線結果；未選擇時為 None。
        dockerfile_response: Docker 設定管線結果；未選擇時為 None。
        generated_at: 整個 gather 完成後的 UTC 時間戳。
    """

    project_name: str
    github_url: str
    generated_types: List[GenerationType]
    api_docs_section: Optional[AnalysisSection] = field(default=None)
    env_guide_section: Optional[AnalysisSection] = field(default=None)
    diagram_response: Optional[DiagramResponse] = field(default=None)
    dockerfile_response: Optional[DockerfileResponse] = field(default=None)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────


class GitHubSelectiveService:
    """協調 GitHub 下載與多管線並行生成的 Application Service。

    使用者透過 ``generate`` 清單指定要觸發的管線，
    系統只對被選擇的管線執行 ``asyncio.gather``，未選者不觸發，節省資源。

    Example::

        service = GitHubSelectiveService()
        result = await service.analyze_selective(
            github_url="https://github.com/tiangolo/fastapi",
            generate=[GenerationType.AI_DOCS, GenerationType.DIAGRAM],
            output_dir=Path("/tmp/output"),
        )
        # result.ai_docs_report  → AIReport
        # result.diagram_response → DiagramResponse
        # result.dockerfile_response → None（未選）
    """

    def __init__(self) -> None:
        """預先初始化所有依賴 Service（Singleton 模式）。

        所有 Service 在應用啟動時建立，LangGraph 各管線只編譯一次，
        避免每次請求重建管線物件的開銷。
        """
        self._downloader = GitHubDownloader()
        self._api_docs = ApiDocsService()
        self._env_guide = EnvGuideService()
        self._diagram = DiagramService()
        self._dockerfile = DockerfileService()
        logger.info("GitHubSelectiveService 初始化完成。")

    async def analyze_selective(
        self,
        github_url: str,
        generate: List[GenerationType],
        output_dir: Path,
    ) -> GitHubSelectiveResult:
        """從 GitHub URL 下載 repo 並並行執行被選擇的生成管線。

        Args:
            github_url: 使用者提供的 GitHub repo URL（公開 repo）。
            generate: 要執行的管線清單，至少一個。
                合法值：``GenerationType.AI_DOCS``、``GenerationType.DIAGRAM``、
                ``GenerationType.DOCKERFILE``。
            output_dir: AI 技術文件的輸出目錄（僅 ``ai_docs`` 管線使用）。

        Returns:
            :class:`GitHubSelectiveResult`，未執行的管線欄位為 ``None``。

        Raises:
            InvalidGitHubUrlError: URL 格式不符規範（422）。
            GitHubRepoNotFoundError: Repo 不存在或為私有（404）。
            FileSizeLimitExceededError: ZIP 超過 50 MB（413）。
            GitHubDownloadError: 下載逾時或網路錯誤（502）。
            InvalidZipFileError: ZIP 無法解析（422）。
            EmptyProjectError: 過濾後無程式碼檔案（422）。
            LLMCallError: AI 管線呼叫失敗（502）。
        """
        logger.info(
            "[GitHubSelective] 收到請求：%s，選擇管線：%s",
            github_url,
            [g.value for g in generate],
        )

        # ── 步驟 1：下載一次，所有管線共用 zip_bytes ──────────────────────────
        zip_bytes, filename = await self._downloader.fetch(github_url)
        project_name = filename.replace(".zip", "")
        logger.info(
            "[GitHubSelective] 下載完成：%s（%d bytes），準備觸發 %d 個管線。",
            filename,
            len(zip_bytes),
            len(generate),
        )

        # ── 步驟 2：動態組裝任務，只執行被選擇的管線 ─────────────────────────
        task_keys: List[str] = []
        tasks = []

        if GenerationType.API_DOCS in generate:
            task_keys.append("api_docs")
            tasks.append(
                self._api_docs.generate(
                    zip_bytes=zip_bytes,
                    filename=filename,
                    output_dir=output_dir,
                )
            )

        if GenerationType.ENV_GUIDE in generate:
            task_keys.append("env_guide")
            tasks.append(
                self._env_guide.generate(
                    zip_bytes=zip_bytes,
                    filename=filename,
                    output_dir=output_dir,
                )
            )

        if GenerationType.DIAGRAM in generate:
            task_keys.append("diagram")
            tasks.append(
                self._diagram.generate_diagrams(
                    zip_bytes=zip_bytes,
                    filename=filename,
                )
            )

        if GenerationType.DOCKERFILE in generate:
            task_keys.append("dockerfile")
            tasks.append(
                self._dockerfile.generate_dockerfile(
                    zip_bytes=zip_bytes,
                    filename=filename,
                )
            )

        # ── 步驟 3：並行執行，任一失敗即 fail-fast ────────────────────────────
        logger.info("[GitHubSelective] 並行執行管線：%s", task_keys)
        raw_results = await asyncio.gather(*tasks)
        result_map = dict(zip(task_keys, raw_results))
        logger.info("[GitHubSelective] 所有管線執行完畢。")

        # ── 步驟 4：組裝結果 ──────────────────────────────────────────────────
        return GitHubSelectiveResult(
            project_name=project_name,
            github_url=github_url,
            generated_types=generate,
            api_docs_section=result_map.get("api_docs"),
            env_guide_section=result_map.get("env_guide"),
            diagram_response=result_map.get("diagram"),
            dockerfile_response=result_map.get("dockerfile"),
        )
