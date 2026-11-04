import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()


@app.get("/api/health")
def health():
    return {"status": "ok"}


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if (frontend_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(frontend_dist / "index.html")
else:
    logging.getLogger(__name__).warning(
        "Frontend build missing. Run 'npm ci' and 'npm run build' in frontend/, "
        "then restart Uvicorn. The API remains available at /api/health."
    )

    @app.get("/", include_in_schema=False)
    def index():
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. Run npm ci and npm run build in frontend/, "
            "then restart Uvicorn.",
        )
