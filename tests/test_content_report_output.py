"""驗證內容端點在唯讀部署模式與本地寫檔模式下的完整輸出流程。"""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.api.routers import project_router
from app.application.graphs import ai_analysis_graph
from app.application.graphs.state import GraphState
from app.application.services.full_pipeline_service import FullPipelineService
from app.settings import Settings, get_settings


async def generate_api_docs(state: GraphState) -> GraphState:
    """取代外部 LLM 呼叫，提供固定 API 文件內容。"""
    return {"api_docs_content": "# API docs\nHello API"}


async def generate_env_guide(state: GraphState) -> GraphState:
    """取代外部 LLM 呼叫，提供固定環境指南內容。"""
    return {"env_guide_content": "# Environment\nHello environment"}


class ContentReportOutputTests(unittest.IsolatedAsyncioTestCase):
    """以真實 ZIP、Service、LangGraph 與 Writer 驗證寫檔開關。"""

    def setUp(self) -> None:
        """只替換 AI 生成節點，避免測試呼叫外部服務。"""
        for name, replacement in (
            ("generate_api_docs_node", generate_api_docs),
            ("generate_env_guide_node", generate_env_guide),
        ):
            patcher = patch.object(ai_analysis_graph, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = FullPipelineService()
        patcher = patch.object(project_router, "_full_service", self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = FastAPI()
        self.app.include_router(project_router.router)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("demo/main.py", "def hello():\n    return 'hello'\n")
        self.zip_bytes = archive.getvalue()

    async def request_content(self) -> dict:
        """透過 HTTP 路由提交 ZIP 並確認內容回應。"""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/projects/analyze-and-generate/content",
                files={"file": ("demo.zip", self.zip_bytes, "application/zip")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["api_docs_content"], "# API docs\nHello API")
        self.assertEqual(result["env_guide_content"], "# Environment\nHello environment")
        self.assertGreater(result["api_docs_word_count"], 0)
        self.assertGreater(result["env_guide_word_count"], 0)
        return result

    async def test_disabled_output_does_not_access_home_or_write_reports(self) -> None:
        """環境變數關閉寫檔時，即使家目錄不可存取仍可回傳內容。"""
        with patch.dict(os.environ, {"CONTENT_REPORT_WRITE_FILES": "false"}):
            settings = Settings(_env_file=None)
        self.assertFalse(settings.content_report_write_files)
        self.app.dependency_overrides[get_settings] = lambda: settings
        with (
            patch.object(Path, "home", side_effect=OSError("read-only home")),
            patch(
                "app.application.graphs.nodes.format_output_node._writer.write",
                side_effect=OSError("read-only report storage"),
            ) as writer,
        ):
            await self.request_content()
        writer.assert_not_called()

    async def test_default_output_preserves_local_report_files(self) -> None:
        """未設定環境變數時，既有快取路徑仍產生兩份正確文件。"""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
        self.assertTrue(settings.content_report_write_files)
        self.app.dependency_overrides[get_settings] = lambda: settings
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Path, "home", return_value=Path(directory)):
                result = await self.request_content()
            output_dir = Path(directory) / ".cache" / "project-handover" / "ai_docs"
            self.assertEqual(len(list(output_dir.glob("*.md"))), 2)
            for suffix, field in (
                ("api_docs", "api_docs_content"),
                ("env_guide", "env_guide_content"),
            ):
                path = next(output_dir.glob(f"*_{suffix}.md"))
                self.assertEqual(path.read_text(encoding="utf-8"), result[field])

    async def test_service_without_output_directory_returns_content(self) -> None:
        """Service 預設的內容模式回傳文字與空輸出路徑。"""
        with patch(
            "app.application.graphs.nodes.format_output_node._writer.write",
            side_effect=OSError("read-only report storage"),
        ) as writer:
            report = await self.service.analyze_and_generate(self.zip_bytes, "demo.zip")
        writer.assert_not_called()
        self.assertEqual(report.api_docs.content, "# API docs\nHello API")
        self.assertIsNone(report.api_docs_output_path)
        self.assertIsNone(report.env_guide_output_path)

    async def test_explicit_output_still_writes_when_content_output_is_disabled(self) -> None:
        """內容開關不改變 Service 明確要求的檔案輸出。"""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"CONTENT_REPORT_WRITE_FILES": "false"}),
        ):
            report = await self.service.analyze_and_generate(
                self.zip_bytes, "demo.zip", output_dir=Path(directory)
            )
            self.assertEqual(
                Path(report.api_docs_output_path).read_text(encoding="utf-8"),
                "# API docs\nHello API",
            )
            self.assertEqual(
                Path(report.env_guide_output_path).read_text(encoding="utf-8"),
                "# Environment\nHello environment",
            )


if __name__ == "__main__":
    unittest.main()
