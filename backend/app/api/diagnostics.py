"""Readiness and independently protected operational diagnostics."""
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.dependencies import CurrentUser

router = APIRouter(prefix="/api")


@router.get("/ready")
def ready(request: Request):
    from fastapi.responses import JSONResponse

    from app.services.observability import readiness
    checks = readiness(request.app.state.database, request.app.state.settings)
    return JSONResponse({"ready": all(checks.values()), "checks": checks}, status_code=200 if all(checks.values()) else 503)


@router.get("/metrics")
def prometheus_metrics(request: Request):
    from app.services.observability import metrics
    token = request.app.state.settings.metrics_token
    if not token:
        raise HTTPException(404)
    if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + token):
        raise HTTPException(401)
    data, mime = metrics(request.app.state.database)
    return Response(content=data, headers={"Content-Type": mime})


@router.get("/ready/models")
def models_ready(request: Request, user: CurrentUser):
    from app.health import model_checks
    return {"checks": model_checks(request.app.state.settings), "scope": "API connectivity and model listing; no inference"}
