"""API 路由 (Router)：GitHub URL 輸入之 HTTP 進入點。

暴露三個端點：
- ``POST /api/v1/github/analyze``：固定觸發 AI 技術文件管線（API 文件 + 環境指南），寫入磁碟回傳路徑。
- ``POST /api/v1/github/analyze/content``：與 analyze 相同管線，不寫入磁碟，直接回傳 AI 生成內容 JSON。
- ``POST /api/v1/github/analyze-all``：使用者自選要觸發的管線清單
  （ai_docs / diagram / dockerfile），只執行被選擇的項目。

Note:
    目前僅支援公開 repo。私有 repo 支援（GitHub Token 傳入）規劃於 V2。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, HttpUrl

from typing import List

from app.api.schemas.response import (
    AiDocsResult,
    DiagramResult,
    DockerfileResult,
    ErrorResponse,
    FullAnalysisContentResponse,
    FullAnalysisResponse,
    GenerationType,
    GitHubSelectiveResponse,
)
from app.application.services.github_ingestion_service import GitHubIngestionService
from app.application.services.github_selective_service import GitHubSelectiveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["GitHub"])

# 模組層級的 Service 執行個體（Singleton 模式；FastAPI 單一進程情境）
_service = GitHubIngestionService()
_selective_service = GitHubSelectiveService()


# ─────────────────────────────────────────────────────────────────────────────
# Request Schema
# ─────────────────────────────────────────────────────────────────────────────


class GitHubAnalyzeRequest(BaseModel):
    """``POST /api/v1/github/analyze`` 的請求 Body Schema。

    Attributes:
        github_url: 目標公開 GitHub repo 的 URL。
            Pydantic 內建 ``HttpUrl`` 驗證確保基本格式正確性，
            業務層的 Regex 驗證在 GitHubDownloader 內執行。
        output_dir: AI 生成兩份文件的伺服器本地輸出目錄，不存在則自動建立。
    """

    github_url: HttpUrl = Field(
        ...,
        description="目標公開 GitHub repo 的 URL（例如：https://github.com/owner/repo）。",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    output_dir: str = Field(
        default=str(Path.home() / "Desktop" / "ai_docs"),
        description="AI 文件的伺服器本地輸出目錄，不存在將自動建立。",
        examples=["/Users/user/Desktop/ai_docs"],
    )

    model_config = {"json_schema_extra": {"title": "GitHubAnalyzeRequest"}}


# ─────────────────────────────────────────────────────────────────────────────
# 端點：GitHub URL → 完整管線
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/analyze",
    status_code=status.HTTP_200_OK,
    summary="從 GitHub URL 分析公開 Repo 並以 AI 生成技術文件",
    description=(
        "接收一個 **公開 GitHub repo URL**，在後端完成以下完整流程：\n\n"
        "**Phase 0**（URL → ZIP 下載）：\n"
        "- 解析 owner/repo，自動嘗試 `main` → `master` branch。\n"
        "- 無需 GitHub Token，直接下載 Archive ZIP（上限 50 MB）。\n\n"
        "**Phase 1**（ZIP → Markdown）：\n"
        "- 解壓縮、智慧過濾雜訊檔案、建立目錄樹、渲染 Markdown。\n\n"
        "**Phase 2**（Markdown → AI 文件，透過 LangGraph）：\n"
        "- `generate_api_docs_node`：生成 API 使用文件。\n"
        "- `generate_env_guide_node`：生成環境建置指南。\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。\n\n"
        "**注意**：目前僅支援公開 repo，私有 repo 支援規劃於 V2。"
    ),
    response_model=FullAnalysisResponse,
    responses={
        200: {"description": "成功生成兩份文件，回傳輸出路徑。"},
        404: {"model": ErrorResponse, "description": "Repo 不存在或為私有。"},
        413: {"model": ErrorResponse, "description": "下載的 ZIP 超過 50 MB 上限。"},
        422: {"model": ErrorResponse, "description": "無效的 GitHub URL 格式。"},
        502: {"model": ErrorResponse, "description": "GitHub 下載逾時或 LLM API 呼叫失敗。"},
        500: {"model": ErrorResponse, "description": "非預期的內部錯誤。"},
    },
)
async def analyze_from_github(body: GitHubAnalyzeRequest) -> FullAnalysisResponse:
    """接收 GitHub repo URL，執行下載 → Phase 1 → Phase 2 AI 生成完整管線。

    Args:
        body: 包含 ``github_url`` 與 ``output_dir`` 的請求 Body。

    Returns:
        :class:`~app.api.schemas.response.FullAnalysisResponse`，
        包含 ``api_docs_path`` 與 ``env_guide_path`` 兩個獨立輸出路徑。

    Raises:
        InvalidGitHubUrlError: URL 格式不符規範（→ 422）。
        GitHubRepoNotFoundError: Repo 不存在或為私有（→ 404）。
        FileSizeLimitExceededError: ZIP 超過 50 MB（→ 413）。
        GitHubDownloadError: 下載逾時或網路錯誤（→ 502）。
        InvalidZipFileError: GitHub 回傳的 ZIP 無法解析（→ 422）。
        EmptyProjectError: 過濾後無程式碼檔案（→ 422）。
        LLMCallError: Gemini API 呼叫失敗（→ 502）。
    """
    github_url_str = str(body.github_url)
    logger.info("[github-router] 收到請求：%s，輸出目錄：%s", github_url_str, body.output_dir)

    report = await _service.analyze_from_github(
        github_url=github_url_str,
        output_dir=Path(body.output_dir),
    )

    return FullAnalysisResponse(
        project_name=report.project_name,
        code_file_count=report.api_docs.word_count,
        total_files_in_zip=0,
        api_docs_path=report.api_docs_output_path or "",
        env_guide_path=report.env_guide_output_path or "",
        api_docs_word_count=report.api_docs.word_count,
        env_guide_word_count=report.env_guide.word_count,
        generated_at=report.generated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request Schema（選擇性生成）
# ─────────────────────────────────────────────────────────────────────────────


class GitHubSelectiveRequest(BaseModel):
    """``POST /api/v1/github/analyze-all`` 的請求 Body Schema。

    Attributes:
        github_url: 目標公開 GitHub repo 的 URL。
        generate: 要觸發的生成管線清單，至少需包含一個合法值。
            合法值：``"ai_docs"``、``"diagram"``、``"dockerfile"``。
            傳入不合法的值，Pydantic 直接回 422。
        output_dir: AI 技術文件的伺服器本地輸出目錄，僅 ``ai_docs`` 管線使用。
    """

    github_url: HttpUrl = Field(
        ...,
        description="目標公開 GitHub repo 的 URL（例如：https://github.com/owner/repo）。",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    generate: List[GenerationType] = Field(
        ...,
        min_length=1,
        description=(
            "要觸發的生成管線清單，至少需包含一個。\n\n"
            "合法值：\n"
            "- ``ai_docs``：生成 API 使用文件 + 環境建置指南（輸出至 output_dir）。\n"
            "- ``diagram``：生成 Mermaid 系統架構圖 + ER 圖。\n"
            "- ``dockerfile``：生成 Dockerfile + .dockerignore + docker-compose.yml。"
        ),
        examples=[["ai_docs", "diagram"]],
    )
    output_dir: str = Field(
        default=str(Path.home() / "Desktop" / "ai_docs"),
        description="AI 技術文件的伺服器本地輸出目錄（僅 ai_docs 管線使用），不存在將自動建立。",
        examples=["/Users/user/Desktop/ai_docs"],
    )

    model_config = {"json_schema_extra": {"title": "GitHubSelectiveRequest"}}


# ─────────────────────────────────────────────────────────────────────────────
# 端點：GitHub URL → 選擇性管線
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/analyze-all",
    status_code=status.HTTP_200_OK,
    summary="從 GitHub URL 選擇性觸發生成管線（AI 文件 / 圖表 / Docker）",
    description=(
        "接收一個 **公開 GitHub repo URL**，並由使用者指定要觸發的生成管線。\n\n"
        "**Phase 0**（URL → ZIP 下載）：\n"
        "- 解析 owner/repo，自動嘗試 `main` → `master` branch。\n"
        "- ZIP 只下載一次，所有被選管線共用同一份 bytes。\n\n"
        "**可選管線（可複選，至少一個）：**\n"
        "- `ai_docs`：AI 技術文件（API 使用文件 + 環境建置指南，輸出 .md 至 output_dir）。\n"
        "- `diagram`：Mermaid 系統架構圖 + ER 圖（JSON 語法字串）。\n"
        "- `dockerfile`：Dockerfile + .dockerignore + docker-compose.yml（JSON 文字）。\n\n"
        "**未選擇的管線欄位在回應中固定為 `null`。**\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。\n\n"
        "**注意**：目前僅支援公開 repo，私有 repo 支援規劃於 V2。"
    ),
    response_model=GitHubSelectiveResponse,
    responses={
        200: {"description": "成功執行被選管線，回傳彙整結果；未選項目為 null。"},
        404: {"model": ErrorResponse, "description": "Repo 不存在或為私有。"},
        413: {"model": ErrorResponse, "description": "下載的 ZIP 超過 50 MB 上限。"},
        422: {"model": ErrorResponse, "description": "無效的 GitHub URL 或 generate 值。"},
        502: {"model": ErrorResponse, "description": "GitHub 下載逾時或 AI 管線呼叫失敗。"},
        500: {"model": ErrorResponse, "description": "非預期的內部錯誤。"},
    },
)
async def analyze_all_from_github(
    body: GitHubSelectiveRequest,
) -> GitHubSelectiveResponse:
    """接收 GitHub repo URL 與生成管線清單，並行執行被選管線後回傳彙整結果。

    Args:
        body: 包含 ``github_url``、``generate`` 清單與 ``output_dir`` 的請求 Body。

    Returns:
        :class:`~app.api.schemas.response.GitHubSelectiveResponse`，
        未被選擇的管線欄位（``ai_docs`` / ``diagram`` / ``dockerfile``）固定為 ``null``。

    Raises:
        InvalidGitHubUrlError: URL 格式不符規範（→ 422）。
        GitHubRepoNotFoundError: Repo 不存在或為私有（→ 404）。
        FileSizeLimitExceededError: ZIP 超過 50 MB（→ 413）。
        GitHubDownloadError: 下載逾時或網路錯誤（→ 502）。
        InvalidZipFileError: ZIP 無法解析（→ 422）。
        EmptyProjectError: 過濾後無程式碼檔案（→ 422）。
        LLMCallError: AI 管線呼叫失敗（→ 502）。
    """
    github_url_str = str(body.github_url)
    logger.info(
        "[github-router] analyze-all 收到請求：%s，管線：%s",
        github_url_str,
        [g.value for g in body.generate],
    )

    result = await _selective_service.analyze_selective(
        github_url=github_url_str,
        generate=body.generate,
        output_dir=Path(body.output_dir),
    )

    # ── 組裝 AI 文件子結果 ────────────────────────────────────────────────────
    ai_docs_result: AiDocsResult | None = None
    if result.ai_docs_report is not None:
        r = result.ai_docs_report
        ai_docs_result = AiDocsResult(
            api_docs_path=r.api_docs_output_path or "",
            env_guide_path=r.env_guide_output_path or "",
            api_docs_word_count=r.api_docs.word_count,
            env_guide_word_count=r.env_guide.word_count,
        )

    # ── 組裝圖表子結果 ────────────────────────────────────────────────────────
    diagram_result: DiagramResult | None = None
    if result.diagram_response is not None:
        d = result.diagram_response
        diagram_result = DiagramResult(
            has_database=d.has_database,
            architecture_diagram=d.architecture_diagram,
            er_diagram=d.er_diagram,
        )

    # ── 組裝 Docker 子結果 ────────────────────────────────────────────────────
    dockerfile_result: DockerfileResult | None = None
    if result.dockerfile_response is not None:
        df = result.dockerfile_response
        dockerfile_result = DockerfileResult(
            language=df.language,
            framework=df.framework,
            dockerfile_content=df.dockerfile_content,
            dockerignore_content=df.dockerignore_content,
            compose_content=df.compose_content,
        )

    return GitHubSelectiveResponse(
        project_name=result.project_name,
        github_url=github_url_str,
        generated_types=result.generated_types,
        ai_docs=ai_docs_result,
        diagram=diagram_result,
        dockerfile=dockerfile_result,
        generated_at=result.generated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 端點：GitHub URL → 內容直傳（不寫入磁碟）
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/analyze/content",
    status_code=status.HTTP_200_OK,
    summary="從 GitHub URL 分析 Repo 並直接回傳 AI 生成內容 JSON",
    description=(
        "接收一個 **公開 GitHub repo URL**，執行與 `/analyze` 完全相同的完整管線：\n\n"
        "**Phase 0** → **Phase 1** → **Phase 2**（LangGraph AI 分析）。\n\n"
        "**差異在於回傳格式**：\n\n"
        "- `/analyze`：將兩份文件寫入伺服器磁碟，回傳 `路徑`。\n"
        "- `/analyze/content`：**不依賴磁碟路徑**，直接回傳 AI 生成的 Markdown 文字內容。\n\n"
        "適用情境：前端需要直接渲染內容，或進行內容對接 / 二次處理，"
        "不需要對接伺服器檔案系統。\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。\n\n"
        "**注意**：目前僅支援公開 repo，私有 repo 支援規劃於 V2。"
    ),
    response_model=FullAnalysisContentResponse,
    responses={
        200: {"description": "成功回傳 AI 生成的兩份文件完整內容。"},
        404: {"model": ErrorResponse, "description": "Repo 不存在或為私有。"},
        413: {"model": ErrorResponse, "description": "下載的 ZIP 超過 50 MB 上限。"},
        422: {"model": ErrorResponse, "description": "無效的 GitHub URL 格式。"},
        502: {"model": ErrorResponse, "description": "GitHub 下載逾時或 LLM API 呼叫失敗。"},
        500: {"model": ErrorResponse, "description": "非預期的內部錯誤。"},
    },
)
async def analyze_content_from_github(body: GitHubAnalyzeRequest) -> FullAnalysisContentResponse:
    """接收 GitHub repo URL，執行完整管線後直接回傳 AI 生成內容 JSON，不寫入磁碟。

    Args:
        body: 包含 ``github_url`` 的請求 Body（``output_dir`` 欄位在本端點不使用）。

    Returns:
        :class:`~app.api.schemas.response.FullAnalysisContentResponse`，
        包含 ``api_docs_content`` 與 ``env_guide_content`` 完整 Markdown 內容字串。

    Raises:
        InvalidGitHubUrlError: URL 格式不符規範（→ 422）。
        GitHubRepoNotFoundError: Repo 不存在或為私有（→ 404）。
        FileSizeLimitExceededError: ZIP 超過 50 MB（→ 413）。
        GitHubDownloadError: 下載逾時或網路錯誤（→ 502）。
        InvalidZipFileError: GitHub 回傳的 ZIP 無法解析（→ 422）。
        EmptyProjectError: 過濾後無程式碼檔案（→ 422）。
        LLMCallError: Gemini API 呼叫失敗（→ 502）。
    """
    github_url_str = str(body.github_url)
    logger.info("[github-router] analyze/content 收到請求：%s", github_url_str)

    report = await _service.analyze_from_github(
        github_url=github_url_str,
        output_dir=Path.home() / ".cache" / "project-handover" / "ai_docs",
    )

    return FullAnalysisContentResponse(
        project_name=report.project_name,
        api_docs_content=report.api_docs.content,
        env_guide_content=report.env_guide.content,
        api_docs_word_count=report.api_docs.word_count,
        env_guide_word_count=report.env_guide.word_count,
        generated_at=report.generated_at,
    )
