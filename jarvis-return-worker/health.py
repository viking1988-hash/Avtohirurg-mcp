import asyncio
from fastapi import HTTPException

async def ready(request):
    if request.app.state.stop.is_set() or any(t.done() for t in request.app.state.loops):
        raise HTTPException(503,'worker not ready')
    try:
        await asyncio.to_thread(request.app.state.repository.stats)
    except Exception:
        raise HTTPException(503,'sandbox database unavailable') from None
    return {'status':'ready','transport':'fake'}
