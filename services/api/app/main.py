"""FastAPI 앱 2개. 공개(8000): /admin, /s, /sessions, /ws, /assets. 내부(8001): /internal.

라우터는 app/routers/<이름>.py의 `router`다. 공개 앱은 /assets/missions/<id>/avatars/를 읽기
전용으로
서빙한다(카탈로그 정적 에셋, D13-3).
오류 응답은 docs/contracts/README.md "공통" 형식 `{"error": {"code", "message"}}`,
입력 검증 실패는 422 invalid_input.
"""

import importlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

PUBLIC_ROUTERS: tuple[str, ...] = ("candidate", "admin", "ws", "voice")
INTERNAL_ROUTERS: tuple[str, ...] = ("internal",)
CORS_HEADERS = ["Authorization", "Content-Type"]


def _include(app: FastAPI, names: tuple[str, ...]) -> None:
    for name in names:
        app.include_router(importlib.import_module(f"app.routers.{name}").router)


async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    # 의존성이 detail에 이미 {"error": {...}}를 담았으면 그대로, 아니면 같은 형식으로 감싼다
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        body = exc.detail
    else:
        body = {"error": {"code": "http_error", "message": str(exc.detail)}}
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


async def _service_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, **exc.extra}}, status_code=exc.status
    )


async def _invalid_input(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse({"error": {"code": "invalid_input", "message": message}}, status_code=422)


def _base(title: str, ctx: Any) -> FastAPI:
    app = FastAPI(title=title)
    app.state.ctx = ctx
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid_input)
    from app.services.common import ServiceError

    app.add_exception_handler(ServiceError, _service_error)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_public_app(ctx: Any, web_origins: tuple[str, ...] = ()) -> FastAPI:
    app = _base("Day Zero api", ctx)
    # 브라우저(web)가 8000을 직접 부른다. 인증은 Bearer 헤더라 쿠키 자격 증명은 허용하지 않는다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(web_origins),
        allow_methods=["*"],
        allow_headers=CORS_HEADERS,
    )
    _include(app, PUBLIC_ROUTERS)
    _mount_assets(app, ctx)
    return app


class _CachedStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
        return response


def _mount_assets(app: FastAPI, ctx: Any) -> None:
    """/assets/missions/<mission_id>/avatars/<file> → scenarios/missions/<id>/assets/avatars/."""
    settings = getattr(ctx, "settings", None)
    if settings is None:
        return
    missions = Path(settings.scenarios_dir) / "missions"
    for mission in sorted(p for p in missions.iterdir() if (p / "assets" / "avatars").is_dir()):
        app.mount(
            f"/assets/missions/{mission.name}/avatars",
            _CachedStatic(directory=mission / "assets" / "avatars"),
            name=f"avatars-{mission.name}",
        )


def create_internal_app(ctx: Any) -> FastAPI:
    app = _base("Day Zero internal api", ctx)
    _include(app, INTERNAL_ROUTERS)
    return app
