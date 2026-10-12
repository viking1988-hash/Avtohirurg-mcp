from time import monotonic

def record(conn, before, after, action, worker_id, started=None, metadata=None):
    details={'action': action, 'worker_id': worker_id,
        'lease_owner': (before or after).get('lease_owner'),
        'duration': max(0, monotonic() - started) if started else 0,
        'version_before': before.get('version') if before else None,
        'version_after': after.get('version'),
        **(metadata or {})}
    conn.execute('''INSERT INTO jarvis_return_action_events
        (task_id,from_state,to_state,actor,correlation_id,metadata)
        VALUES (%s,%s,%s,%s,%s,%s)''',
        (after['task_id'], before['state'] if before else None, after['state'], worker_id,
         after['correlation_id'], __import__('psycopg').types.json.Jsonb({
             **details})))
