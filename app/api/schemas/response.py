"""專案交接平台的 API Response Schemas。

所有對外的公開回應模型全部基於 Pydantic v2，以便進行嚴格的資料型別驗證
並且供 FastAPI 自動生成對應的 OpenAPI 文件 (Swagger)。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """由全域 exceptions handler 統一拋出的標準化錯誤回傳結構。

    Attributes:
        error_code: 機器可讀的錯誤代碼標識 (例如：``"INVALID_ZIP_FILE"``)。
        message: 供人類閱讀閱讀的錯誤描述。
        detail: 可選詳細資訊的擴充上下文，如出錯的欄位名稱、或上游發生的原始錯誤訊息。
    """

    error_code: str = Field(..., examples=["INVALID_ZIP_FILE"])
    message: str = Field(..., examples=["上傳的檔案不是一個有效的 ZIP 壓縮檔。"])
    detail: Optional[str] = Field(None, examples=["Bad magic number for file header"])

    model_config = {"json_schema_extra": {"title": "ErrorResponse"}}


class AnalyzeResponse(BaseModel):
    """API ``POST /api/v1/projects/analyze`` 回傳的成功回應。

    Attributes:
        project_name: 由上傳 ZIP 附檔移除掉副檔名後自動推斷得出的專案名。
        file_count: 當套過濾器處理後，會收錄並輸出至報告中的程式碼檔案數量。
        total_files_in_zip: 在這個 ZIP 壓縮包裡頭找到的所有檔案總數量。
        skipped_files: 被忽略而不收錄過濾規則所被除掉的數量。
        markdown_report: 用純文字形式呈現 UTF-8 編碼形式之 Markdown 報告。
        generated_at: 表示該報告生成當下的 UTC 當下時間。
    """

    project_name: str = Field(..., examples=["my-awesome-project"])
    file_count: int = Field(..., ge=0, examples=[12])
    total_files_in_zip: int = Field(..., ge=0, examples=[40])
    skipped_files: int = Field(..., ge=0, examples=[28])
    markdown_report: str = Field(..., description="完整的 Markdown 報告原始內容。")
    generated_at: datetime = Field(
        ...,
        examples=["2026-03-16T11:39:59+08:00"],
        description="分析任務完成當下的 UTC 國際標準時間區段。",
    )

    model_config = {"json_schema_extra": {"title": "AnalyzeResponse"}}


class FullAnalysisResponse(BaseModel):
    """``POST /api/v1/github/analyze`` 的成功回應模型。

    整合 Phase 1 + Phase 2 的完整管線結果，以 artifacts 陣列直接回傳
    AI 生成的文件內容，不寫入磁碟、不回傳路徑。

    Attributes:
        project_name: 由 ZIP 檔名或 GitHub repo 名稱推斷的專案名稱。
        code_file_count: Phase 1 過濾後納入分析的程式碼檔案數量。
        total_files_in_zip: ZIP 內的總檔案數量。
        artifacts: AI 生成的所有文件產出物陣列（API 使用文件 + 環境建置指南）。
        generated_at: 整個管線完成的 UTC 時間戳。
    """

    project_name: str = Field(..., examples=["my_project"])
    code_file_count: int = Field(..., ge=0, examples=[12])
    total_files_in_zip: int = Field(..., ge=0, examples=[40])
    artifacts: List[GeneratedArtifact] = Field(
        ...,
        description="AI 生成的所有文件產出物，包含 API 使用文件與環境建置指南。",
    )
    generated_at: datetime = Field(
        ...,
        description="整個管線完成的 UTC 時間戳。",
        examples=["2026-03-18T07:08:00+00:00"],
    )

    model_config = {"json_schema_extra": {"title": "FullAnalysisResponse"}}


class FullAnalysisContentResponse(BaseModel):
    """ZIP 上傳或 GitHub URL 完整管線的「內容直傳」回應模型。

    與 :class:`FullAnalysisResponse` 的差異在於：此模型直接回傳 AI 生成的
    Markdown 文字內容，而非寫入磁碟後再回傳路徑。適合前端直接渲染或進行二次處理。

    Attributes:
        project_name: 由 ZIP 檔名或 GitHub repo 名稱推斷的專案名稱。
        api_docs_content: AI 生成的 API 使用文件完整 Markdown 文字。
        env_guide_content: AI 生成的環境建置指南完整 Markdown 文字。
        api_docs_word_count: API 文件的粗略字數估算（以空白分詞）。
        env_guide_word_count: 環境指南的粗略字數估算（以空白分詞）。
        generated_at: 整個管線完成的 UTC 時間戳。
    """

    project_name: str = Field(..., examples=["my_project"])
    api_docs_content: str = Field(
        ...,
        description="AI 生成的 API 使用文件完整 Markdown 文字內容。",
    )
    env_guide_content: str = Field(
        ...,
        description="AI 生成的環境建置指南完整 Markdown 文字內容。",
    )
    api_docs_word_count: int = Field(default=0, ge=0, description="API 文件字數估算。")
    env_guide_word_count: int = Field(default=0, ge=0, description="環境指南字數估算。")
    generated_at: datetime = Field(
        ...,
        description="整個管線完成的 UTC 時間戳。",
        examples=["2026-03-18T07:08:00+00:00"],
    )

    model_config = {"json_schema_extra": {"title": "FullAnalysisContentResponse"}}


# ---------------------------------------------------------------------------
# 單獨生成 — API 文件 / 環境指南 各自獨立的回應 Schema
# ---------------------------------------------------------------------------


class ApiDocsContentResponse(BaseModel):
    """``POST /api/v1/docs/api-docs/generate`` 的成功回應模型。

    只包含 API 使用文件的生成結果，不含環境建置指南任何欄位，
    讓前端可以直接取得並渲染，無需再行解包。

    Attributes:
        project_name: 由 ZIP 檔名推斷的專案名稱。
        api_docs_content: AI 生成的 API 使用文件完整 Markdown 文字。
        word_count: 粗略字數估算（以空白分詞）。
        generated_at: 管線完成的 UTC 時間戳。
    """

    project_name: str = Field(..., examples=["my_project"])
    content: str = Field(
        ...,
        description="AI 生成的 API 使用文件完整 Markdown 文字內容。",
    )
    word_count: int = Field(default=0, ge=0, description="API 文件字數估算。")
    generated_at: datetime = Field(
        ...,
        description="管線完成的 UTC 時間戳。",
        examples=["2026-05-23T03:00:00+00:00"],
    )

    model_config = {"json_schema_extra": {"title": "ApiDocsContentResponse"}}


class EnvGuideContentResponse(BaseModel):
    """``POST /api/v1/docs/env-guide/generate`` 的成功回應模型。

    只包含環境建置指南的生成結果，不含 API 文件任何欄位，
    讓前端可以直接取得並渲染，無需再行解包。

    Attributes:
        project_name: 由 ZIP 檔名推斷的專案名稱。
        env_guide_content: AI 生成的環境建置指南完整 Markdown 文字。
        word_count: 粗略字數估算（以空白分詞）。
        generated_at: 管線完成的 UTC 時間戳。
    """

    project_name: str = Field(..., examples=["my_project"])
    content: str = Field(
        ...,
        description="AI 生成的環境建置指南完整 Markdown 文字內容。",
    )
    word_count: int = Field(default=0, ge=0, description="環境指南字數估算。")
    generated_at: datetime = Field(
        ...,
        description="管線完成的 UTC 時間戳。",
        examples=["2026-05-23T03:00:00+00:00"],
    )

    model_config = {"json_schema_extra": {"title": "EnvGuideContentResponse"}}


# ---------------------------------------------------------------------------
# GitHub 選擇性生成 — Enum 與 Schema
# ---------------------------------------------------------------------------


class GenerationType(str, Enum):
    """使用者可選的生成管線類型。

    作為 ``GitHubSelectiveRequest.generate`` 陣列的合法值，
    Pydantic 在反序列化時自動驗證，傳入不支援的值直接回 422。

    Attributes:
        AI_DOCS: 觸發 AI 技術文件管線，生成 API 使用文件與環境建置指南。
        DIAGRAM: 觸發 Mermaid 圖表管線，生成系統架構圖與 ER 圖。
        DOCKERFILE: 觸發 Docker 設定管線，生成 Dockerfile、.dockerignore 與 docker-compose.yml。
    """

    API_DOCS = "api_docs"
    ENV_GUIDE = "env_guide"
    DIAGRAM = "diagram"
    DOCKERFILE = "dockerfile"


class ApiDocsResult(BaseModel):
    """API 文件管線的輸出結果。

    直接回傳 AI 生成的文件內容，不依賴伺服器磁碟路徑。

    Attributes:
        artifacts: AI 生成的 API 使用文件產出物。
    """

    artifacts: List[GeneratedArtifact] = Field(
        ...,
        description="AI 生成的 API 使用文件。",
    )

    model_config = {"json_schema_extra": {"title": "ApiDocsResult"}}


class EnvGuideResult(BaseModel):
    """環境建置指南管線的輸出結果。

    直接回傳 AI 生成的文件內容，不依賴伺服器磁碟路徑。

    Attributes:
        artifacts: AI 生成的環境建置指南產出物。
    """

    artifacts: List[GeneratedArtifact] = Field(
        ...,
        description="AI 生成的環境建置指南。",
    )

    model_config = {"json_schema_extra": {"title": "EnvGuideResult"}}


class GeneratedArtifact(BaseModel):
    """通用的生成產出物模型，用於陣列化多文件輸出。"""
    filename: str = Field(..., description="檔案名稱，例如 Dockerfile、architecture.mermaid。")
    file_type: str = Field(..., description="檔案類型，例如 dockerfile、yaml、mermaid。")
    content: str = Field(..., description="檔案內容。")

    model_config = {"json_schema_extra": {"title": "GeneratedArtifact"}}


class DiagramResult(BaseModel):
    """Mermaid 圖表管線的輸出結果。

    對應 ``DiagramService`` 的生成結果，包含裝載圖表內容的陣列。
    """

    has_database: bool = Field(
        ...,
        description="是否在專案中偵測到資料庫相關程式碼。",
    )
    artifacts: List[GeneratedArtifact] = Field(
        ...,
        description="圖表生成的所有產出物。",
    )

    model_config = {"json_schema_extra": {"title": "DiagramResult"}}


class DockerfileResult(BaseModel):
    """Docker 設定管線的輸出結果。

    對應 ``DockerfileService`` 的生成結果，包含裝載設定檔內容的陣列。
    """

    language: str = Field(..., description="偵測到的主要程式語言，例如 python、node。")
    framework: Optional[str] = Field(
        default=None,
        description="偵測到的框架，例如 fastapi、express；未偵測到則為 null。",
    )
    artifacts: List[GeneratedArtifact] = Field(
        ...,
        description="Docker 設定檔的所有產出物。",
    )

    model_config = {"json_schema_extra": {"title": "DockerfileResult"}}


class GitHubSelectiveResponse(BaseModel):
    """``POST /api/v1/github/analyze-all`` 的成功回應模型。

    依使用者在 ``generate`` 陣列中選擇的項目，填入對應的生成結果；
    未選擇的管線欄位固定為 ``null``，結構不變，前端解析無歧義。

    Attributes:
        project_name: 由 GitHub repo 名稱推斷的專案名稱。
        github_url: 使用者提供的原始 GitHub repo URL。
        generated_types: 實際執行的生成管線清單。
        ai_docs: AI 技術文件結果；未選擇時為 null。
        diagram: Mermaid 圖表結果；未選擇時為 null。
        dockerfile: Docker 設定結果；未選擇時為 null。
        generated_at: 整個管線完成的 UTC 時間戳。
    """

    project_name: str = Field(..., examples=["fastapi"])
    github_url: str = Field(
        ...,
        description="使用者提供的原始 GitHub repo URL。",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    generated_types: List[GenerationType] = Field(
        ...,
        description="實際執行的生成管線清單。",
        examples=[["ai_docs", "diagram"]],
    )
    api_docs: Optional[ApiDocsResult] = Field(
        default=None,
        description="API 使用文件管線結果；未選擇時為 null。",
    )
    env_guide: Optional[EnvGuideResult] = Field(
        default=None,
        description="環境建置指南管線結果；未選擇時為 null。",
    )
    diagram: Optional[DiagramResult] = Field(
        default=None,
        description="Mermaid 圖表管線結果；未選擇時為 null。",
    )
    dockerfile: Optional[DockerfileResult] = Field(
        default=None,
        description="Docker 設定管線結果；未選擇時為 null。",
    )
    generated_at: datetime = Field(
        ...,
        description="整個管線完成的 UTC 時間戳。",
        examples=["2026-05-19T03:00:00+00:00"],
    )

    model_config = {"json_schema_extra": {"title": "GitHubSelectiveResponse"}}

