import os
import uvicorn
uvicorn.run('jarvis_return_worker.app:app',host='0.0.0.0',port=int(os.environ.get('PORT','8080')),
            timeout_graceful_shutdown=30,access_log=False)
