import asyncio
import secrets
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from ..metrics import render_metrics
from ..queue import Conflict

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
    return await call(request.app.state.repository.list,limit)

@router.get('/queue/{task_id}',dependencies=[Depends(read)])
async def task(task_id:str,request:Request):
    result=await call(request.app.state.repository.get,task_id)
    if not result:
        raise HTTPException(404,'task not found')
    return result

@router.post('/enqueue',dependencies=[Depends(read)])
async def enqueue(body:Enqueue,request:Request):
    return await call(request.app.state.repository.enqueue,body.task_id,body.destination,body.payload,body.correlation_id)

@router.post('/approve')
async def approve(body:Approval,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.repository.approve,body.task_id,body.fingerprint,body.expires_at,actor)

@router.post('/cancel')
async def cancel(body:TaskID,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.repository.cancel,body.task_id,actor)

@router.post('/requeue')
async def requeue(body:TaskID,request:Request,actor:str=Depends(human)):
    return await call(request.app.state.repository.requeue,body.task_id,actor)

@router.get('/metrics',dependencies=[Depends(read)])
async def metrics(request:Request):
    return Response(await call(render_metrics,request.app.state.repository),media_type='text/plain; version=0.0.4')


# Project registry endpoints are Sandbox-local and do not invoke the action worker.
import uuid
from typing import Literal

from ..projects import ProjectRepository

class ProjectCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_key: str=Field(min_length=2,max_length=64,pattern=r'^[a-z0-9][a-z0-9_-]{1,63}$')
    title: str=Field(min_length=1,max_length=160)
    description: str=Field(default='',max_length=2000)

class ProjectTaskCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id: str=Field(default_factory=lambda:'project-'+uuid.uuid4().hex,
        min_length=1,max_length=128,pattern=r'^[A-Za-z0-9:_-]+$')
    project_key: str=Field(min_length=2,max_length=64,pattern=r'^[a-z0-9][a-z0-9_-]{1,63}$')
    title: str=Field(min_length=1,max_length=240)
    details: str=Field(default='',max_length=4000)
    priority: Literal['LOW','NORMAL','HIGH','URGENT']='NORMAL'
    source: str=Field(default='',max_length=128)

class ProjectTaskTransition(BaseModel):
    model_config=ConfigDict(extra='forbid')
    state: Literal['READY','IN_PROGRESS','BLOCKED','REVIEW','DONE','CANCELLED']
    completion_evidence: str=Field(default='',max_length=2000)

@router.get('/projects',dependencies=[Depends(read)])
async def projects(request:Request,limit:int=100):
    if not 1<=limit<=500:
        raise HTTPException(422,'limit must be 1..500')
    return await call(ProjectRepository(request.app.state.config).list_projects,limit)

@router.post('/projects',dependencies=[Depends(read)])
async def create_project(body:ProjectCreate,request:Request):
    return await call(ProjectRepository(request.app.state.config).create_project,
        body.project_key,body.title,body.description)

@router.get('/project-tasks',dependencies=[Depends(read)])
async def project_tasks(request:Request,project_key:str|None=None,
                        state:Literal['INBOX','READY','IN_PROGRESS','BLOCKED','REVIEW','DONE','CANCELLED']|None=None,
                        limit:int=100):
    if not 1<=limit<=500:
        raise HTTPException(422,'limit must be 1..500')
    return await call(ProjectRepository(request.app.state.config).list_tasks,
        project_key,state,limit)

@router.post('/project-tasks',dependencies=[Depends(read)])
async def create_project_task(body:ProjectTaskCreate,request:Request):
    return await call(ProjectRepository(request.app.state.config).create_task,
        body.task_id,body.project_key,body.title,body.details,body.priority,body.source)

@router.post('/project-tasks/{task_id}/transition')
async def transition_project_task(task_id:str,body:ProjectTaskTransition,
                                  request:Request,actor:str=Depends(human)):
    return await call(ProjectRepository(request.app.state.config).transition_task,
        task_id,body.state,actor,body.completion_evidence)
