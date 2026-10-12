"""Protected provider-neutral reconciliation API."""
import asyncio
import secrets
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from ..queue import Conflict
from .. import reconciliation as service

router = APIRouter()

class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=2, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)

class Delivered(Versioned):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    provider_message_id: str | None = Field(default=None, max_length=512)
    provider_receipt: str | None = Field(default=None, max_length=512)

def _auth(role):
    def check(request: Request, authorization: str = Header(default="")):
        config = request.app.state.config
        token = config.approval_token if role == "operator" else config.read_token
        if not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "unauthorized")
        return config.operator_id if role == "operator" else "sandbox-reader"
    return check

read_actor = _auth("read")
operator = _auth("operator")

async def _call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except Conflict as exc:
        if str(exc) == "task not found":
            raise HTTPException(404, "task not found") from None
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "sandbox repository unavailable") from None

@router.get("/{task_id}")
async def get_task(task_id: str, request: Request, actor: str = Depends(read_actor)):
    result = await _call(service.read_task, request.app.state.repository, task_id, actor)
    if result is None:
        raise HTTPException(404, "task not found")
    return result

@router.get("/{task_id}/events")
async def get_events(task_id: str, request: Request, actor: str = Depends(read_actor)):
    result = await _call(service.read_events, request.app.state.repository, task_id, actor)
    if result is None:
        raise HTTPException(404, "task not found")
    return result

@router.get("/{task_id}/delivery")
async def get_delivery(task_id: str, request: Request, actor: str = Depends(read_actor)):
    result = await _call(service.read_delivery, request.app.state.repository, task_id, actor)
    if result is None:
        raise HTTPException(404, "task not found")
    return result

@router.post("/{task_id}/reconcile")
async def reconcile(task_id: str, body: Versioned, request: Request,
                    actor: str = Depends(operator)):
    return await _call(service.mutate, request.app.state.repository, task_id, actor,
                       "reconcile", body.expected_version, reason=body.reason,
                       comment=body.comment)

@router.post("/{task_id}/confirm-delivered")
async def confirm_delivered(task_id: str, body: Delivered, request: Request,
                            actor: str = Depends(operator)):
    return await _call(service.mutate, request.app.state.repository, task_id, actor,
                       "confirm-delivered", body.expected_version, reason=body.reason,
                       comment=body.comment, provider=body.provider,
                       provider_message_id=body.provider_message_id,
                       provider_receipt=body.provider_receipt)

@router.post("/{task_id}/confirm-undelivered")
async def confirm_undelivered(task_id: str, body: Versioned, request: Request,
                              actor: str = Depends(operator)):
    return await _call(service.mutate, request.app.state.repository, task_id, actor,
                       "confirm-undelivered", body.expected_version, reason=body.reason,
                       comment=body.comment)

@router.post("/{task_id}/request-new-approval")
async def request_new_approval(task_id: str, body: Versioned, request: Request,
                               actor: str = Depends(operator)):
    return await _call(service.mutate, request.app.state.repository, task_id, actor,
                       "request-new-approval", body.expected_version, reason=body.reason,
                       comment=body.comment)
