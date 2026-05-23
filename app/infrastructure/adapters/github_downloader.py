"""Infrastructure 層的 Adapter：從 GitHub repo 下載 ZIP 壓縮檔。

透過 GitHub Archive 直連 URL（無需 Token）非同步下載公開 repo 的 ZIP 壓縮檔，
並將其標準化為與現有 :class:`ZipExtractor` 相容的 ``(zip_bytes, filename)`` 格式。

支援的 URL 格式範例：
    - ``https://github.com/owner/repo``
    - ``https://github.com/owner/repo.git``
    - ``https://github.com/owner/repo/``

Branch fallback 策略：依序嘗試 ``main`` → ``master``，兩者皆 404 才拋出例外。

Note:
    目前僅支援公開 repo。私有 repo 支援（Token 傳入）規劃於 V2。
"""

from __future__ import annotations

import logging
import re
from typing import Tuple

import httpx

from app.domain.exceptions import (
    FileSizeLimitExceededError,
    GitHubDownloadError,
    GitHubRepoNotFoundError,
    InvalidGitHubUrlError,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 常數定義
# ─────────────────────────────────────────────────────────────────────────────

_GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
"""合法 GitHub repo URL 的正則表達式。"""

_DEFAULT_BRANCHES: Tuple[str, ...] = ("main", "master")
"""Branch fallback 順序：依序嘗試，兩者皆失敗才拋出 GitHubRepoNotFoundError。"""

_MAX_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB，與現有 ZIP 上傳上限一致
_DOWNLOAD_TIMEOUT_SECONDS: float = 30.0


class GitHubDownloader:
    """非同步地從 GitHub 公開 repo 下載 ZIP 壓縮檔。

    此 Adapter 的唯一公開介面為 :meth:`fetch`，其回傳格式與 :class:`ZipExtractor`
    的輸入介面完全相容，確保下游管線零改動。

    Example::

        downloader = GitHubDownloader()
        zip_bytes, filename = await downloader.fetch(
            "https://github.com/tiangolo/fastapi"
        )
        # 接著可直接傳入 ZipExtractor 或 FullPipelineService
    """

    # ------------------------------------------------------------------
    # 公開介面 (Public interface)
    # ------------------------------------------------------------------

    async def fetch(self, github_url: str) -> Tuple[bytes, str]:
        """解析 GitHub URL 並下載對應 repo 的 ZIP 壓縮檔。

        Branch 依序嘗試 ``main`` → ``master``，成功下載後立即執行大小驗證。

        Args:
            github_url: 使用者提供的 GitHub repo URL 字串。

        Returns:
            回傳一個 tuple，格式為 ``(zip_bytes, filename)``。
            其中 *zip_bytes* 為 ZIP 原始位元組，*filename* 為組合後的虛擬檔名
            （例如：``"fastapi-main.zip"``），供下游 Service 記錄與識別使用。

        Raises:
            InvalidGitHubUrlError: URL 格式不符 GitHub repo 規範。
            GitHubRepoNotFoundError: main 與 master 兩個 branch 皆回 404，
                表示 repo 不存在或為私有。
            FileSizeLimitExceededError: 下載的 ZIP 超過 50 MB 大小上限。
            GitHubDownloadError: httpx 連線逾時或其他網路層錯誤。
        """
        owner, repo = self._parse_url(github_url)
        logger.info("[GitHubDownloader] 開始下載：%s/%s", owner, repo)

        for branch in _DEFAULT_BRANCHES:
            archive_url = self._build_archive_url(owner, repo, branch)
            logger.debug("[GitHubDownloader] 嘗試 branch '%s'：%s", branch, archive_url)

            try:
                zip_bytes = await self._download(archive_url)
            except _BranchNotFoundSignal:
                logger.debug("[GitHubDownloader] branch '%s' 回 404，嘗試下一個。", branch)
                continue

            # 大小驗證
            if len(zip_bytes) > _MAX_SIZE_BYTES:
                logger.warning(
                    "[GitHubDownloader] 檔案超過大小上限：%d bytes > %d bytes",
                    len(zip_bytes),
                    _MAX_SIZE_BYTES,
                )
                raise FileSizeLimitExceededError(max_mb=50)

            filename = f"{repo}-{branch}.zip"
            logger.info(
                "[GitHubDownloader] 下載成功：%s (%d bytes)",
                filename,
                len(zip_bytes),
            )
            return zip_bytes, filename

        # 兩個 branch 皆 404
        raise GitHubRepoNotFoundError(
            detail=f"嘗試 branch {_DEFAULT_BRANCHES!r} 皆失敗：{owner}/{repo}"
        )

    # ------------------------------------------------------------------
    # 私有輔助方法 (Private helpers)
    # ------------------------------------------------------------------

    def _parse_url(self, url: str) -> Tuple[str, str]:
        """以正則表達式驗證並解析 GitHub URL，回傳 (owner, repo) tuple。

        Args:
            url: 待解析的 GitHub URL 字串。

        Returns:
            ``(owner, repo)`` tuple，例如 ``("tiangolo", "fastapi")``。

        Raises:
            InvalidGitHubUrlError: URL 不符合 GitHub repo 格式規範。
        """
        match = _GITHUB_URL_PATTERN.match(url.strip())
        if not match:
            raise InvalidGitHubUrlError(detail=f"原始輸入：{url!r}")
        return match.group("owner"), match.group("repo")

    def _build_archive_url(self, owner: str, repo: str, branch: str) -> str:
        """組裝 GitHub Archive 直連 URL。

        Args:
            owner: repo 擁有者（使用者名稱或組織名稱）。
            repo: repo 名稱。
            branch: 分支名稱（例如：``"main"``）。

        Returns:
            完整的 ZIP archive URL 字串。
        """
        return (
            f"https://github.com/{owner}/{repo}"
            f"/archive/refs/heads/{branch}.zip"
        )

    async def _download(self, url: str) -> bytes:
        """發送 GET 請求下載 ZIP，處理 GitHub 的 301 redirect。

        Args:
            url: 完整的 archive URL。

        Returns:
            ZIP 原始位元組。

        Raises:
            _BranchNotFoundSignal: 回應狀態碼為 404，表示該 branch 不存在。
            GitHubDownloadError: 連線逾時或其他 httpx 網路錯誤。
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,  # GitHub archive URL 會 301 至 CDN
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(url)

            if response.status_code == 404:
                raise _BranchNotFoundSignal

            response.raise_for_status()
            return response.content

        except _BranchNotFoundSignal:
            raise
        except httpx.TimeoutException as exc:
            raise GitHubDownloadError(
                detail=f"連線逾時（{_DOWNLOAD_TIMEOUT_SECONDS}s）：{url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise GitHubDownloadError(
                detail=f"HTTP 錯誤 {exc.response.status_code}：{url}"
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubDownloadError(detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# 內部控制流訊號 (Internal control-flow signal)
# ─────────────────────────────────────────────────────────────────────────────

class _BranchNotFoundSignal(Exception):
    """內部使用的輕量控制流訊號，表示特定 branch 回 404。

    僅用於 :class:`GitHubDownloader` 內部的 branch fallback 邏輯，
    不對外暴露，不繼承 AppBaseException。
    """
