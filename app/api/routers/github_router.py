"""API 路由 (Router)：GitHub URL 輸入之 HTTP 進入點。

暴露一個端點：
- ``POST /api/v1/github/analyze-all``：使用者自選要觸發的管線清單
  （api_docs / env_guide / diagram / dockerfile），只執行被選擇的項目，
  所有管線結果統一以 JSON 格式回傳文件內容。

Note:
    目前僅支援公開 repo。私有 repo 支援（GitHub Token 傳入）規劃於 V2。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, HttpUrl

from app.api.schemas.response import (
    ApiDocsResult,
    DiagramResult,
    DockerfileResult,
    EnvGuideResult,
    ErrorResponse,
    GeneratedArtifact,
    GenerationType,
    GitHubSelectiveResponse,
)
from app.application.services.github_selective_service import GitHubSelectiveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["GitHub"])

# 模組層級的 Service 執行個體（Singleton 模式；FastAPI 單一進程情境）
_selective_service = GitHubSelectiveService()

# 快取目錄：GitHub 端點以「回傳內容」為主要目的，
# 磁碟輸出為管線副產物，統一導向此目錄。
_CACHE_OUTPUT_DIR = Path.home() / ".cache" / "project-handover" / "ai_docs"


# ─────────────────────────────────────────────────────────────────────────────
# Request Schema（選擇性生成）
# ─────────────────────────────────────────────────────────────────────────────


class GitHubSelectiveRequest(BaseModel):
    """``POST /api/v1/github/analyze-all`` 的請求 Body Schema。

    Attributes:
        github_url: 目標公開 GitHub repo 的 URL。
        generate: 要觸發的生成管線清單，至少需包含一個合法值。
            合法值：``"api_docs"``、``"env_guide"``、``"diagram"``、``"dockerfile"``。
            傳入不合法的值，Pydantic 直接回 422。
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
            "- ``api_docs``：生成 API 使用文件。\n"
            "- ``env_guide``：生成環境建置指南。\n"
            "- ``diagram``：生成 Mermaid 系統架構圖 + ER 圖。\n"
            "- ``dockerfile``：生成 Dockerfile + .dockerignore + docker-compose.yml。"
        ),
        examples=[["api_docs", "diagram"]],
    )

    model_config = {"json_schema_extra": {"title": "GitHubSelectiveRequest"}}


# ─────────────────────────────────────────────────────────────────────────────
# 端點：GitHub URL → 選擇性管線
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/analyze-all",
    status_code=status.HTTP_200_OK,
    summary="從 GitHub URL 選擇性觸發生成管線（API 文件 / 環境指南 / 圖表 / Docker）",
    description=(
        "接收一個 **公開 GitHub repo URL**，並由使用者指定要觸發的生成管線。\n\n"
        "**Phase 0**（URL → ZIP 下載）：\n"
        "- 解析 owner/repo，自動嘗試 `main` → `master` branch。\n"
        "- ZIP 只下載一次，所有被選管線共用同一份 bytes。\n\n"
        "**可選管線（可複選，至少一個）：**\n"
        "- `api_docs`：生成 API 使用文件。\n"
        "- `env_guide`：生成環境建置指南。\n"
        "- `diagram`：Mermaid 系統架構圖 + ER 圖。\n"
        "- `dockerfile`：Dockerfile + .dockerignore + docker-compose.yml。\n\n"
        "**所有管線結果統一回傳 JSON 格式的文件內容，不寫入磁碟。**\n\n"
        "**未選擇的管線欄位在回應中固定為 `null`。**\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。\n\n"
        "**注意**：目前僅支援公開 repo，私有 repo 支援規劃於 V2。"
    ),
    response_model=GitHubSelectiveResponse,
    responses={
        200: {"description": "成功執行被選管線；未選項目為 null。"},
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
        body: 包含 ``github_url`` 與 ``generate`` 清單的請求 Body。

    Returns:
        :class:`~app.api.schemas.response.GitHubSelectiveResponse`，
        所有被選管線的結果以 JSON 格式回傳；
        未被選擇的管線欄位固定為 ``null``。

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
        output_dir=_CACHE_OUTPUT_DIR,
    )

    # ── 組裝 API 文件子結果 ──────────────────────────────────────────────────
    api_docs_result: ApiDocsResult | None = None
    if result.api_docs_section is not None:
        api_docs_result = ApiDocsResult(
            artifacts=[
                GeneratedArtifact(
                    filename=f"{result.project_name}_api_docs.md",
                    file_type="markdown",
                    content=result.api_docs_section.content,
                )
            ]
        )

    # ── 組裝環境建置指南子結果 ────────────────────────────────────────────────
    env_guide_result: EnvGuideResult | None = None
    if result.env_guide_section is not None:
        env_guide_result = EnvGuideResult(
            artifacts=[
                GeneratedArtifact(
                    filename=f"{result.project_name}_env_guide.md",
                    file_type="markdown",
                    content=result.env_guide_section.content,
                )
            ]
        )

    # ── 組裝圖表子結果 ────────────────────────────────────────────────────────
    diagram_result: DiagramResult | None = None
    if result.diagram_response is not None:
        d = result.diagram_response
        diagram_result = DiagramResult(
            has_database=d.has_database,
            artifacts=d.artifacts,
        )

    # ── 組裝 Docker 子結果 ────────────────────────────────────────────────────
    dockerfile_result: DockerfileResult | None = None
    if result.dockerfile_response is not None:
        df = result.dockerfile_response
        dockerfile_result = DockerfileResult(
            language=df.language,
            framework=df.framework,
            artifacts=df.artifacts,
        )

    return GitHubSelectiveResponse(
        project_name=result.project_name,
        github_url=github_url_str,
        generated_types=result.generated_types,
        api_docs=api_docs_result,
        env_guide=env_guide_result,
        diagram=diagram_result,
        dockerfile=dockerfile_result,
        generated_at=result.generated_at,
    )
