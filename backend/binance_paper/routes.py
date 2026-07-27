"""Typed FastAPI routes for Binance paper state only."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/binance-paper", tags=["binance-paper"])
_service = None


class StatusResponse(BaseModel):
    paper_only: bool
    real_orders_disabled: bool
    hard_gate_enabled: bool
    runtime_state: str
    initialized: bool
    database_path: str
    market: dict[str, Any]
    pending_order_count: int


class ListResponse(BaseModel):
    items: list[dict[str, Any]]


class ConfirmRequest(BaseModel):
    confirm: bool = False


class StrategyPatchRequest(BaseModel):
    enabled: bool | None = None
    risk: dict[str, Any] = Field(default_factory=dict)


class ActionResponse(BaseModel):
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


def configure_service(service) -> None:
    global _service
    _service = service


def service():
    if _service is None:
        raise HTTPException(status_code=503, detail="Binance paper service unavailable")
    return _service


def _translate(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc


@router.get("/status", response_model=StatusResponse)
def status():
    return service().status()


@router.get("/market")
def market():
    return service().market_status()


@router.get("/strategies", response_model=ListResponse)
def strategies():
    try:
        return {"items": service().strategy_statuses()}
    except Exception as exc:
        _translate(exc)


@router.get("/accounts", response_model=ListResponse)
def accounts():
    try:
        return {"items": service().accounts()}
    except Exception as exc:
        _translate(exc)


@router.get("/positions", response_model=ListResponse)
def positions():
    try:
        return {"items": service().positions()}
    except Exception as exc:
        _translate(exc)


@router.get("/orders", response_model=ListResponse)
def orders(limit: int = Query(100, ge=1, le=1000)):
    try:
        return {"items": service().orders(limit)}
    except Exception as exc:
        _translate(exc)


@router.get("/fills", response_model=ListResponse)
def fills(limit: int = Query(100, ge=1, le=1000)):
    try:
        return {"items": service().fills(limit)}
    except Exception as exc:
        _translate(exc)


@router.get("/funding", response_model=ListResponse)
def funding(limit: int = Query(100, ge=1, le=1000)):
    try:
        return {"items": service().funding_events(limit)}
    except Exception as exc:
        _translate(exc)


@router.get("/trades", response_model=ListResponse)
def trades(limit: int = Query(100, ge=1, le=1000)):
    try:
        return {"items": service().trades(limit)}
    except Exception as exc:
        _translate(exc)


@router.get("/metrics")
def metrics():
    try:
        return service().metrics()
    except Exception as exc:
        _translate(exc)


@router.get("/equity", response_model=ListResponse)
def equity(
    strategy_id: str | None = None,
    limit: int = Query(1000, ge=1, le=20_000),
):
    try:
        return {"items": service().equity(strategy_id, limit)}
    except Exception as exc:
        _translate(exc)


@router.get("/events", response_model=ListResponse)
def events(limit: int = Query(100, ge=1, le=1000)):
    try:
        return {"items": service().events(limit)}
    except Exception as exc:
        _translate(exc)


@router.post("/start", response_model=StatusResponse)
def start():
    try:
        return service().start_engine()
    except Exception as exc:
        _translate(exc)


@router.post("/pause", response_model=StatusResponse)
def pause():
    try:
        return service().pause_engine()
    except Exception as exc:
        _translate(exc)


@router.post("/positions/{position_id}/close", response_model=ActionResponse)
def close_position(position_id: str, request: ConfirmRequest):
    try:
        result = service().close_position(position_id, confirm=request.confirm)
        return {"status": result.pop("status"), "details": result}
    except Exception as exc:
        _translate(exc)


@router.post("/close-all", response_model=ActionResponse)
def close_all(request: ConfirmRequest):
    try:
        result = service().close_all(confirm=request.confirm)
        return {"status": result.pop("status"), "details": result}
    except Exception as exc:
        _translate(exc)


@router.patch("/strategies/{strategy_id}")
def patch_strategy(strategy_id: str, request: StrategyPatchRequest):
    try:
        return service().update_strategy(
            strategy_id, enabled=request.enabled, risk_patch=request.risk
        )
    except Exception as exc:
        _translate(exc)
