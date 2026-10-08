"""Settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    public_url: str  # e.g. https://my-mfp.fly.dev (no trailing slash)
    passcode: str
    secret_key: str
    data_dir: str = "/data"
    timezone: str = "UTC"

    @property
    def host(self) -> str:
        return urlparse(self.public_url).netloc

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "mfp.db")

    @classmethod
    def from_env(cls) -> Config:
        missing = [k for k in ("PUBLIC_URL", "MCP_PASSCODE", "SECRET_KEY") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
        return cls(
            public_url=os.environ["PUBLIC_URL"].rstrip("/"),
            passcode=os.environ["MCP_PASSCODE"],
            secret_key=os.environ["SECRET_KEY"],
            data_dir=os.environ.get("DATA_DIR", "/data"),
            timezone=os.environ.get("TIMEZONE", "UTC"),
        )
