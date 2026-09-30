import json
import os
import urllib.request

def list_workflows(limit: int = 100):
    base = os.environ.get('N8N_BASE_URL', '').strip().rstrip('/')
    key = os.environ.get('N8N_API_KEY', '').strip()
    if not base or not key:
        raise RuntimeError('n8n API configuration is incomplete')
    limit = max(1, min(int(limit), 250))
    req = urllib.request.Request(
        base + '/api/v1/workflows?limit=' + str(limit),
        headers={'X-N8N-API-KEY': key, 'Accept': 'application/json'},
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))