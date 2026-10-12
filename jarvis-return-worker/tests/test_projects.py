import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis_return_worker.app import app
from jarvis_return_worker.projects import ProjectRepository
from jarvis_return_worker.queue import Conflict, QueueRepository


@pytest.fixture
def projects():
    url = os.environ.get("JARVIS_WORKER_CI_DATABASE_URL")
    if not url:
        pytest.skip("ephemeral worker CI PostgreSQL not configured")
    config = SimpleNamespace(database_url=url)
    QueueRepository(config).migrate()
    return ProjectRepository(config)


def test_project_registry_is_idempotent_and_tracks_verified_completion(projects):
    suffix = uuid.uuid4().hex
    project_key = "test-" + suffix
    task_id = "project-test-" + suffix

    project = projects.create_project(project_key, "Sandbox project", "CI only")
    assert project["state"] == "ACTIVE"
    assert projects.create_project(project_key, "Sandbox project", "CI only")["project_key"] == project_key
    with pytest.raises(Conflict):
        projects.create_project(project_key, "Different project")

    task = projects.create_task(task_id, project_key, "Verify queue", source="ci")
    assert task["state"] == "INBOX"
    assert projects.create_task(task_id, project_key, "Verify queue", source="ci")["task_id"] == task_id
    with pytest.raises(Conflict):
        projects.create_task(task_id, project_key, "Different task", source="ci")

    projects.transition_task(task_id, "READY", "ci-human")
    projects.transition_task(task_id, "IN_PROGRESS", "ci-human")
    projects.transition_task(task_id, "REVIEW", "ci-human")
    with pytest.raises(Conflict):
        projects.transition_task(task_id, "DONE", "ci-human")
    completed = projects.transition_task(task_id, "DONE", "ci-human", "CI assertion passed")
    assert completed["state"] == "DONE"
    assert completed["completion_evidence"] == "CI assertion passed"
    with pytest.raises(Conflict):
        projects.transition_task(task_id, "IN_PROGRESS", "ci-human")

    history = projects.db.connection
    with history() as conn:
        events = conn.execute(
            "SELECT count(*) AS n FROM jarvis_project_task_events WHERE task_id=%s",
            (task_id,),
        ).fetchone()["n"]
    assert events == 5


def test_project_task_requires_active_project(projects):
    with pytest.raises(Conflict):
        projects.create_task("orphan-" + uuid.uuid4().hex, "missing-project", "No project")


def test_project_api_keeps_transitions_operator_only():
    app.state.config = SimpleNamespace(
        read_token="r" * 32,
        approval_token="a" * 32,
        operator_id="sandbox-operator",
    )
    client = TestClient(app)
    task_id = "project-api-test"

    assert client.get("/projects").status_code == 401
    assert client.post(
        "/projects", json={"project_key": "api-project", "title": "API test"}
    ).status_code == 401
    assert client.post(
        f"/project-tasks/{task_id}/transition",
        json={"state": "DONE", "completion_evidence": "verified"},
        headers={"Authorization": "Bearer " + app.state.config.read_token},
    ).status_code == 401
