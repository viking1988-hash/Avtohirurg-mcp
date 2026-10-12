from time import monotonic
from psycopg.types.json import Jsonb

def record(conn, before, after, action, worker_id, started=None, metadata=None):
    details={
        'action':action,
        'worker_id':worker_id,
        'lease_owner':(before or after).get('lease_owner'),
        'duration':max(0,monotonic()-started) if started else 0,
        **(metadata or {}),
    }
    reason=details.get('reason')
    conn.execute('''INSERT INTO jarvis_return_action_events
        (task_id,from_state,to_state,actor,correlation_id,metadata,
         action,provider,reason,previous_state,new_state)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (after['task_id'],before['state'] if before else None,after['state'],worker_id,
         after['correlation_id'],Jsonb(details),action,after.get('provider'),reason,
         before['state'] if before else None,after['state']))
