"""What every downloader shares: retries, resumable output, atomic writes.

Each source subclasses :class:`HttpSource`. The base class owns the session
and the file handling; subclasses say only what to fetch and how to read it.
A fetch that is interrupted -- by a network fault, a rate limit or a closed
laptop -- is resumed by running the same command again: finished files are
skipped, and a half-written file never appears under its final name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "skydelay-alpha/0.1 (Duke AIPI 520 coursework)"


def build_session(total_retries: int = 5) -> requests.Session:
    """A session that retries transient failures with exponential backoff."""
    retry = Retry(
        total=total_retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def write_atomic(writer: Callable[[Path], Any], path: Path) -> None:
    """Write through a temporary file so a failure cannot leave a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    writer(temporary)
    temporary.replace(path)


class HttpSource:
    """Base class for one remote source."""

    name: str = "source"

    def __init__(
        self,
        config: Mapping[str, Any],
        output_dir: Path,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.output_dir = output_dir / self.name
        self.session = session or build_session()

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> requests.Response:
        response = self.session.get(
            url, params=params, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        return response

    def done(self, path: Path, *, overwrite: bool) -> bool:
        """Whether ``path`` exists and should be skipped."""
        if path.exists() and not overwrite:
            print(f"  skip {path.name} (exists)", flush=True)
            return True
        return False
