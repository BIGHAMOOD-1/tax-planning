"""启动本地服务：python run_server.py  ->  http://127.0.0.1:8000

开发期前端可用 Vite（:5173，/api 代理到本服务）；交付期前端 build 后由本服务托管。
"""
from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

# 保证从任意工作目录启动都能 import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    """以固定主机/端口启动服务（供 `python run_server.py` 或安装后的 `tax-plan` 命令调用）。"""
    uvicorn.run("src.api.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
