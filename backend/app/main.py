"""Local demonstration API and bundled frontend."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, STATIC_DIR
from app.api.router import router
from app.storage import store
from app.data.grid import load_grid

@asynccontextmanager
async def lifespan(app):
    store.engine()
    load_grid()
    yield

app = FastAPI(title="运行方式智能调整平台B",version="4.0.0",lifespan=lifespan)
app.include_router(router)
from app.api.workspace import router as workspace_router
app.include_router(workspace_router)

@app.exception_handler(KeyError)
async def missing(request,exc):
    return JSONResponse(status_code=404,content={"detail":str(exc)})

@app.exception_handler(ValueError)
async def invalid(request,exc):
    return JSONResponse(status_code=422,content={"detail":str(exc)})

if (STATIC_DIR/"assets").exists():
    app.mount("/assets",StaticFiles(directory=STATIC_DIR/"assets"),name="assets")

@app.get("/{path:path}",include_in_schema=False)
def frontend(path:str):
    if path.startswith("api/"):
        raise HTTPException(404,"API不存在")
    safe=(STATIC_DIR/path).resolve()
    if STATIC_DIR.resolve() not in safe.parents and safe!=STATIC_DIR.resolve():
        raise HTTPException(404)
    if safe.is_file():
        return FileResponse(safe)
    if (STATIC_DIR/"index.html").exists():
        return FileResponse(STATIC_DIR/"index.html")
    return JSONResponse(status_code=503,content={"detail":"前端尚未构建，请进入frontend执行npm install和npm run build"})
