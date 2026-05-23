"""API 路由 (Router)：API 文件與環境指南的獨立生成端點。

提供兩個端點，讓呼叫端能**單獨觸發**其中一份文件的生成，
相較於 ``/api/v1/projects/analyze-and-generate`` 節省約 50% 的執行時間與 Token 消耗：

- ``POST /api/v1/docs/api-docs/generate``：
    ZIP → Phase 1 → AI 生成 API 使用文件，回傳文字內容 JSON。
- ``POST /api/v1/docs/env-guide/generate``：
    ZIP → Phase 1 → AI 生成環境建置指南，回傳文字內容 JSON。

Note:
    兩個端點皆不將文件寫入磁碟，直接回傳 AI 生成的 Markdown 文字，
    適合前端直接渲染或進行內容對接。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, status

from app.api.schemas.response import ApiDocsContentResponse, EnvGuideContentResponse, ErrorResponse
from app.application.services.api_docs_service import ApiDocsService
from app.application.services.env_guide_service import EnvGuideService
from app.domain.exceptions import UnsupportedFileTypeError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/docs", tags=["Docs — 獨立生成"])

# 模組層級執行個體（Singleton 模式；FastAPI 單一進程情境）
_api_docs_service = ApiDocsService()
_env_guide_service = EnvGuideService()

# 快取目錄：本 router 的端點以「回傳內容」為主要目的，
# 磁碟輸出為管線副產物，統一導向此目錄。
_CACHE_OUTPUT_DIR = Path.home() / ".cache" / "project-handover" / "ai_docs"


# ─────────────────────────────────────────────────────────────────────────────
# 端點 1：只生成 API 使用文件
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/api-docs/generate",
    status_code=status.HTTP_200_OK,
    summary="單獨生成 API 使用文件（JSON）",
    description=(
        "上傳專案的 `.zip` 壓縮檔，只執行 **API 使用文件** 的生成管線，"
        "不觸發環境建置指南節點。\n\n"
        "**管線流程**：\n"
        "1. Phase 1：解壓縮 → 智慧過濾 → 建立目錄樹 → 渲染 Markdown。\n"
        "2. Phase 2（只跑 API 文件分支）：提取端點清單 → 生成概述表格 → "
        "分批展開詳細說明。\n\n"
        "**相較於 `/projects/analyze-and-generate/content`**：\n"
        "- 省略環境指南節點，節省約 **50%** 執行時間與 Token 消耗。\n"
        "- 回傳 JSON 中只含 `api_docs_content`，無環境指南欄位。\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。"
    ),
    response_model=ApiDocsContentResponse,
    responses={
        200: {"description": "成功回傳 API 使用文件完整內容。"},
        415: {"model": ErrorResponse, "description": "上傳檔案非 .zip 格式。"},
        413: {"model": ErrorResponse, "description": "超過 50 MB 上限。"},
        422: {"model": ErrorResponse, "description": "非有效 ZIP 或過濾後無程式碼檔案。"},
        502: {"model": ErrorResponse, "description": "AI 語言模型 API 呼叫失敗。"},
        500: {"model": ErrorResponse, "description": "非預期的內部錯誤。"},
    },
)
async def generate_api_docs(
    file: UploadFile = File(..., description="待分析的 ZIP 壓縮軟體專案。"),
) -> ApiDocsContentResponse:
    """接收 ZIP 上傳，只執行 API 文件管線並回傳生成內容 JSON。

    Args:
        file: FastAPI 解析的 multipart 上傳 ZIP 檔案物件。

    Returns:
        :class:`~app.api.schemas.response.ApiDocsContentResponse`，
        包含 ``api_docs_content`` 完整 Markdown 文字與 ``word_count``。

    Raises:
        UnsupportedFileTypeError: 非 .zip 格式 (→ 415)。
        FileSizeLimitExceededError: 超過 50 MB (→ 413)。
        InvalidZipFileError: 非有效 ZIP (→ 422)。
        EmptyProjectError: 過濾後無程式碼檔案 (→ 422)。
        LLMCallError: Gemini API 呼叫失敗 (→ 502)。
    """
    filename: str = file.filename or "upload.zip"
    if not filename.lower().endswith(".zip"):
        raise UnsupportedFileTypeError(filename=filename)

    logger.info("[docs/api-docs/generate] 收到 '%s'", filename)

    zip_bytes: bytes = await file.read()
    section = await _api_docs_service.generate(
        zip_bytes=zip_bytes,
        filename=filename,
        output_dir=_CACHE_OUTPUT_DIR,
    )

    return ApiDocsContentResponse(
        project_name=filename.removesuffix(".zip"),
        content=section.content,
        word_count=section.word_count,
        generated_at=datetime.now(tz=timezone.utc),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 端點 2：只生成環境建置指南
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/env-guide/generate",
    status_code=status.HTTP_200_OK,
    summary="單獨生成環境建置指南（JSON）",
    description=(
        "上傳專案的 `.zip` 壓縮檔，只執行 **環境建置指南** 的生成管線，"
        "不觸發 API 文件節點。\n\n"
        "**管線流程**：\n"
        "1. Phase 1：解壓縮 → 智慧過濾 → 建立目錄樹 → 渲染 Markdown。\n"
        "2. Phase 2（只跑環境指南分支）：分析系統需求 → 生成安裝步驟 → "
        "輸出環境變數、啟動指令、常見問題排除。\n\n"
        "**相較於 `/projects/analyze-and-generate/content`**：\n"
        "- 省略 API 文件節點，節省約 **50%** 執行時間與 Token 消耗。\n"
        "- 回傳 JSON 中只含 `env_guide_content`，無 API 文件欄位。\n\n"
        "**前置需求**：伺服器需設定 `GOOGLE_API_KEY` 環境變數。"
    ),
    response_model=EnvGuideContentResponse,
    responses={
        200: {"description": "成功回傳環境建置指南完整內容。"},
        415: {"model": ErrorResponse, "description": "上傳檔案非 .zip 格式。"},
        413: {"model": ErrorResponse, "description": "超過 50 MB 上限。"},
        422: {"model": ErrorResponse, "description": "非有效 ZIP 或過濾後無程式碼檔案。"},
        502: {"model": ErrorResponse, "description": "AI 語言模型 API 呼叫失敗。"},
        500: {"model": ErrorResponse, "description": "非預期的內部錯誤。"},
    },
)
async def generate_env_guide(
    file: UploadFile = File(..., description="待分析的 ZIP 壓縮軟體專案。"),
) -> EnvGuideContentResponse:
    """接收 ZIP 上傳，只執行環境指南管線並回傳生成內容 JSON。

    Args:
        file: FastAPI 解析的 multipart 上傳 ZIP 檔案物件。

    Returns:
        :class:`~app.api.schemas.response.EnvGuideContentResponse`，
        包含 ``env_guide_content`` 完整 Markdown 文字與 ``word_count``。

    Raises:
        UnsupportedFileTypeError: 非 .zip 格式 (→ 415)。
        FileSizeLimitExceededError: 超過 50 MB (→ 413)。
        InvalidZipFileError: 非有效 ZIP (→ 422)。
        EmptyProjectError: 過濾後無程式碼檔案 (→ 422)。
        LLMCallError: Gemini API 呼叫失敗 (→ 502)。
    """
    filename: str = file.filename or "upload.zip"
    if not filename.lower().endswith(".zip"):
        raise UnsupportedFileTypeError(filename=filename)

    logger.info("[docs/env-guide/generate] 收到 '%s'", filename)

    zip_bytes: bytes = await file.read()
    section = await _env_guide_service.generate(
        zip_bytes=zip_bytes,
        filename=filename,
        output_dir=_CACHE_OUTPUT_DIR,
    )

    return EnvGuideContentResponse(
        project_name=filename.removesuffix(".zip"),
        content=section.content,
        word_count=section.word_count,
        generated_at=datetime.now(tz=timezone.utc),
    )
