"""One command, local-only demonstration server."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
if __name__ == "__main__":
    from app import config  # load optional .env before reading launch settings
    import uvicorn
    parser = argparse.ArgumentParser(description="运行方式智能调整平台B")
    parser.add_argument("--port",type=int,default=int(os.getenv("WORKBENCH_PORT","8000")))
    args = parser.parse_args()
    print(f"运行方式智能调整平台B：http://localhost:{args.port}（本地演示；Ctrl+C 停止）", flush=True)
    uvicorn.run("app.main:app", host=os.getenv("WORKBENCH_HOST", "127.0.0.1"),
                port=args.port, log_level="info")
