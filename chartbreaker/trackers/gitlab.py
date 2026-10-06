"""Minimal GitLab REST client for filing ChartBreaker findings as issues.

Scope intentionally tiny: create issue, add note, set labels, get issue. The
goal is to support the closed-loop flow sketched in docs (ChartBreaker →
Tracker → OpenEMR fixer → re-verify) without growing a dependency on a
full-featured GitLab SDK.

Auth: pass ``GITLAB_TOKEN`` (Personal Access Token with ``api`` scope) via
the ``GITLAB_TOKEN`` env var or the constructor.
Host: defaults to ``GITLAB_HOST`` env var, then ``https://labs.gauntletai.com``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_HOST = "https://labs.gauntletai.com"
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class Issue:
    project_id: int
    iid: int
    web_url: str
    title: str
    labels: list[str]


class GitLabError(RuntimeError):
    """Raised when the GitLab API returns a non-success status."""


class GitLabClient:
    def __init__(
        self,
        *,
        host: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = (host or os.environ.get("GITLAB_HOST") or DEFAULT_HOST).rstrip("/")
        tok = token or os.environ.get("GITLAB_TOKEN")
        if not tok:
            raise RuntimeError(
                "GITLAB_TOKEN env var is required (Personal Access Token with 'api' scope)."
            )
        self._client = httpx.Client(
            base_url=f"{self._host}/api/v4",
            headers={"PRIVATE-TOKEN": tok},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitLabClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _project_segment(project: str | int) -> str:
        if isinstance(project, int):
            return str(project)
        # GitLab accepts either numeric ID or URL-encoded path like
        # "namespace%2Frepo". Encode slashes only when a path is given.
        return quote(str(project), safe="")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise GitLabError(
                f"GitLab {method} {path} returned {resp.status_code}: {resp.text[:300]}"
            )
        return resp

    def create_issue(
        self,
        project: str | int,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Issue:
        payload: dict[str, Any] = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        resp = self._request(
            "POST", f"/projects/{self._project_segment(project)}/issues", json=payload
        )
        data = resp.json()
        return Issue(
            project_id=int(data["project_id"]),
            iid=int(data["iid"]),
            web_url=str(data["web_url"]),
            title=str(data["title"]),
            labels=list(data.get("labels") or []),
        )

    def get_issue(self, project: str | int, iid: int) -> Issue:
        resp = self._request(
            "GET", f"/projects/{self._project_segment(project)}/issues/{iid}"
        )
        data = resp.json()
        return Issue(
            project_id=int(data["project_id"]),
            iid=int(data["iid"]),
            web_url=str(data["web_url"]),
            title=str(data["title"]),
            labels=list(data.get("labels") or []),
        )

    def add_note(self, project: str | int, iid: int, body: str) -> None:
        self._request(
            "POST",
            f"/projects/{self._project_segment(project)}/issues/{iid}/notes",
            json={"body": body},
        )

    def set_labels(self, project: str | int, iid: int, labels: list[str]) -> None:
        self._request(
            "PUT",
            f"/projects/{self._project_segment(project)}/issues/{iid}",
            json={"labels": ",".join(labels)},
        )
