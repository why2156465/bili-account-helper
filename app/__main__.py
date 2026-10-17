"""兼容 `uvicorn app.main:app` 与 `python -m app` 两种启动方式。"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("DEBUG", "true").lower() in ("1", "true", "yes"),
    )
