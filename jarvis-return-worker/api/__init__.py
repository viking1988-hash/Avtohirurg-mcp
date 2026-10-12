import asyncio
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from ..metrics import render_metrics
from ..queue import Conflict
from ..reconciliation import read_task, log_rejected

router=APIRouter()

class TaskID(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id: str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9:_-]+$')

class Approval(TaskID):
    fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')
    expires_at: datetime

class Enqueue(TaskID):
    destination: str=Field(min_length=9,max_length=128,pattern=r'^sandbox:[A-Za-z0-9:_-]+$')
    payload: str=Field(min_length=1,max_length=16000)
    correlation_id: str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9:_-]+$')

class Reconciliation(TaskID):
    outcome: str=Field(pattern=r'^(delivered|not_delivered)$')
    receipt_id: str|None=Field(default=None,max_length=512)
    message_id: str|None=Field(default=None,max_length=512)

class CRMReconciliation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(ge=0)

class EmailCancel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(ge=0)

class EmailReconciliation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(ge=0)
    outcome: str=Field(pattern=r'^(delivered|not_delivered)$')
    provider_message_id: str|None=Field(default=None,max_length=256)
    receipt: str|None=Field(default=None,max_length=512)
    reason: str=Field(min_length=3,max_length=500)

def auth(role):
    def check(request:Request,authorization:str=Header(default='')):
        c=request.app.state.config
        expected=c.approval_token if role=='approval' else c.read_token
        if not secrets.compare_digest(authorization,'Bearer '+expected):
            raise HTTPException(401,'unauthorized')
        return c.operator_id if role=='approval' else 'sandbox-reader'
    return check

read=auth('read')
human=auth('approval')

async def call(fn,*args):
    try:
        return await asyncio.to_thread(fn,*args)
    except (Conflict,ValueError):
        raise HTTPException(409,'operation rejected by state/approval/lease guard') from None
    except Exception:
        raise HTTPException(503,'sandbox repository unavailable') from None

@router.get('/queue',dependencies=[Depends(read)])
async def listing(request:Request,limit:int=100):
    if not 1<=limit<=500:
        raise HTTPException(422,'limit must be 1..500')
    return await call(request.app.state.orchestrator.list_tasks,limit)

@router.get('/queue/{task_id}')
async def task(task_id:str,request:Request,actor:str=Depends(read)):
    result=await call(request.app.state.orchestrator.read_task,task_id,actor)
    if not result:
        raise HTTPException(404,'task not found')
    return result

@router.post('/enqueue',dependencies=[Depends(read)])
async def enqueue(body:Enqueue,request:Request):
    return await call(request.app.state.orchestrator.enqueue,body.task_id,body.destination,body.payload,body.correlation_id)

@router.post('/approve')
async def approve(body:Approval,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.orchestrator.approve,body.task_id,body.fingerprint,body.expires_at,actor)

@router.post('/cancel')
async def cancel(body:TaskID,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.orchestrator.cancel,body.task_id,actor)

@router.post('/requeue')
async def requeue(body:TaskID,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.orchestrator.requeue,body.task_id,actor)

@router.post('/reconcile')
async def reconcile(body:Reconciliation,request:Request,actor:str=Depends(human)):
    await call(log_rejected,request.app.state.repository,body.task_id,actor,'legacy_reconcile',
               'use the versioned /queue/{task_id}/ reconciliation API')
    raise HTTPException(410,'use the versioned /queue/{task_id}/ reconciliation API')

@router.get('/metrics',dependencies=[Depends(read)])
async def metrics(request:Request):
    return Response(await call(render_metrics,request.app.state.repository),media_type='text/plain; version=0.0.4')

@router.get('/crm/operations/{event_id}')
async def crm_operation(event_id:str,request:Request,actor:str=Depends(read)):
    if not __import__('re').fullmatch(r'[0-9a-fA-F-]{36}',event_id):
        raise HTTPException(422,'invalid CRM event ID')
    result=await call(request.app.state.orchestrator.read_crm_operation,event_id)
    if not result:
        raise HTTPException(404,'CRM operation not found')
    return result

@router.post('/crm/operations/{event_id}/reconcile')
async def reconcile_crm_operation(event_id:str,body:CRMReconciliation,request:Request,actor:str=Depends(human)):
    if not __import__('re').fullmatch(r'[0-9a-fA-F-]{36}',event_id):
        raise HTTPException(422,'invalid CRM event ID')
    return await call(request.app.state.orchestrator.reconcile_crm_operation,
                      event_id,actor,body.expected_version)

@router.get('/email/operations/{event_id}')
async def email_operation(event_id:str,request:Request,actor:str=Depends(read)):
    if not __import__('re').fullmatch(r'[0-9a-fA-F-]{36}',event_id):
        raise HTTPException(422,'invalid email event ID')
    result=await call(request.app.state.orchestrator.read_email_operation,event_id)
    if not result:
        raise HTTPException(404,'email operation not found')
    return result

@router.post('/email/operations/{event_id}/cancel')
async def cancel_email_operation(event_id:str,body:EmailCancel,request:Request,actor:str=Depends(human)):
    if not __import__('re').fullmatch(r'[0-9a-fA-F-]{36}',event_id):
        raise HTTPException(422,'invalid email event ID')
    return await call(request.app.state.orchestrator.cancel_email_operation,
                      event_id,actor,body.expected_version)

@router.post('/email/operations/{event_id}/reconcile')
async def reconcile_email_operation(event_id:str,body:EmailReconciliation,request:Request,actor:str=Depends(human)):
    if not __import__('re').fullmatch(r'[0-9a-fA-F-]{36}',event_id):
        raise HTTPException(422,'invalid email event ID')
    evidence={'provider_message_id':body.provider_message_id,'receipt':body.receipt,'reason':body.reason}
    return await call(request.app.state.orchestrator.reconcile_email_operation,
        event_id,actor,body.expected_version,body.outcome,evidence)

from .reconciliation import router as reconciliation_router
router.include_router(reconciliation_router,prefix='/queue')

