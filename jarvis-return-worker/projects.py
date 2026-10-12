"""Sandbox project and task registry. This records work; it never performs external actions."""
from .queue import Conflict, QueueRepository


TRANSITIONS = {
    "INBOX": {"READY", "BLOCKED", "CANCELLED"},
    "READY": {"IN_PROGRESS", "BLOCKED", "CANCELLED"},
    "IN_PROGRESS": {"REVIEW", "BLOCKED", "CANCELLED"},
    "BLOCKED": {"READY", "CANCELLED"},
    "REVIEW": {"DONE", "IN_PROGRESS", "BLOCKED", "CANCELLED"},
    "DONE": set(),
    "CANCELLED": set(),
}


class ProjectRepository:
    def __init__(self, config):
        self.db = QueueRepository(config)

    def list_projects(self, limit=100):
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT * FROM jarvis_projects ORDER BY created_at, project_key LIMIT %s",
                (limit,),
            ).fetchall()

    def create_project(self, project_key, title, description="", actor="sandbox-preparer"):
        if not project_key or not title:
            raise ValueError("project key and title are required")
        description = description or ""
        with self.db.connection() as conn:
            row = conn.execute(
                """INSERT INTO jarvis_projects(project_key,title,description)
                   VALUES (%s,%s,%s) ON CONFLICT(project_key) DO NOTHING RETURNING *""",
                (project_key, title, description),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO jarvis_project_events
                       (project_key,actor,action,metadata)
                       VALUES (%s,%s,'project_created',%s)""",
                    (project_key, actor, {"title": title}),
                )
                return row
            row = conn.execute(
                "SELECT * FROM jarvis_projects WHERE project_key=%s",
                (project_key,),
            ).fetchone()
            if (row["title"], row["description"]) != (title, description):
                raise Conflict("project key already belongs to different project data")
            return row

    def list_tasks(self, project_key=None, state=None, limit=100):
        clauses, params = [], []
        if project_key:
            clauses.append("project_key=%s")
            params.append(project_key)
        if state:
            clauses.append("state=%s")
            params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self.db.connection() as conn:
            return conn.execute(
                """SELECT * FROM jarvis_project_tasks"""
                + where
                + " ORDER BY created_at, task_id LIMIT %s",
                tuple(params),
            ).fetchall()

    def create_task(
        self, task_id, project_key, title, details="", priority="NORMAL",
        source="", due_at=None, actor="sandbox-preparer",
    ):
        if not task_id or not project_key or not title:
            raise ValueError("task id, project key and title are required")
        with self.db.connection() as conn:
            project = conn.execute(
                "SELECT state FROM jarvis_projects WHERE project_key=%s",
                (project_key,),
            ).fetchone()
            if not project or project["state"] != "ACTIVE":
                raise Conflict("task requires an active registered project")
            row = conn.execute(
                """INSERT INTO jarvis_project_tasks
                   (task_id,project_key,title,details,priority,source,due_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(task_id) DO NOTHING RETURNING *""",
                (task_id, project_key, title, details or "", priority, source or "", due_at),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO jarvis_project_task_events
                       (task_id,from_state,to_state,actor,metadata)
                       VALUES (%s,NULL,'INBOX',%s,%s)""",
                    (task_id, actor, {"action": "task_created", "source": source or ""}),
                )
                return row
            row = conn.execute(
                "SELECT * FROM jarvis_project_tasks WHERE task_id=%s",
                (task_id,),
            ).fetchone()
            expected = (project_key, title, details or "", priority, source or "", due_at)
            actual = (
                row["project_key"], row["title"], row["details"], row["priority"],
                row["source"], row["due_at"],
            )
            if actual != expected:
                raise Conflict("task id already belongs to different task data")
            return row

    def transition_task(self, task_id, state, actor, evidence=""):
        if state not in TRANSITIONS or not actor:
            raise ValueError("valid task state and authenticated actor required")
        with self.db.connection() as conn:
            before = conn.execute(
                "SELECT * FROM jarvis_project_tasks WHERE task_id=%s FOR UPDATE",
                (task_id,),
            ).fetchone()
            if not before:
                raise Conflict("task not found")
            if state == before["state"]:
                return before
            if state not in TRANSITIONS[before["state"]]:
                raise Conflict("project task transition is not allowed")
            if state == "DONE" and not evidence.strip():
                raise Conflict("completion requires verification evidence")
            completed = state == "DONE"
            after = conn.execute(
                """UPDATE jarvis_project_tasks
                   SET state=%s, completed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                       completion_evidence=CASE WHEN %s THEN %s ELSE NULL END,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s RETURNING *""",
                (state, completed, completed, evidence.strip() if completed else None, task_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO jarvis_project_task_events
                   (task_id,from_state,to_state,actor,metadata)
                   VALUES (%s,%s,%s,%s,%s)""",
                (task_id, before["state"], state, actor,
                 {"evidence": evidence.strip()} if completed else {}),
            )
            return after
