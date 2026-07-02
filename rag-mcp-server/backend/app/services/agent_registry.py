"""Agent registry: file-backed store for agent state and tasks."""

import fcntl
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

AGENTS_FILE = Path(settings.config_dir) / "agents.json"


def _lock_and_load() -> tuple:
    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not AGENTS_FILE.exists():
        AGENTS_FILE.write_text(json.dumps({"agents": []}, indent=2))
    fh = open(AGENTS_FILE, "r+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    data = json.loads(fh.read())
    return fh, data


def _save_and_unlock(fh, data: dict):
    fh.seek(0)
    fh.write(json.dumps(data, indent=2))
    fh.truncate()
    fcntl.flock(fh, fcntl.LOCK_UN)
    fh.close()


def load_agents() -> list[dict]:
    if not AGENTS_FILE.exists():
        return []
    data = json.loads(AGENTS_FILE.read_text())
    return data.get("agents", [])


def get_agent(agent_id: str) -> dict | None:
    for agent in load_agents():
        if agent["agent_id"] == agent_id:
            return agent
    return None


def register_agent(agent_id: str, agent_type: str, container_name: str, config: dict | None = None) -> dict:
    fh, data = _lock_and_load()
    try:
        agents = data.get("agents", [])
        for a in agents:
            if a["agent_id"] == agent_id:
                a["status"] = "running"
                a["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                a["container_name"] = container_name
                _save_and_unlock(fh, data)
                return a

        entry = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "status": "running",
            "container_name": container_name,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "config": config or {},
            "tasks": [],
        }
        agents.append(entry)
        data["agents"] = agents
        _save_and_unlock(fh, data)
        return entry
    except Exception:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        raise


def deregister_agent(agent_id: str) -> bool:
    fh, data = _lock_and_load()
    try:
        agents = data.get("agents", [])
        original_len = len(agents)
        data["agents"] = [a for a in agents if a["agent_id"] != agent_id]
        removed = len(data["agents"]) < original_len
        _save_and_unlock(fh, data)
        return removed
    except Exception:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        raise


def update_heartbeat(agent_id: str, status: str = "running", tasks_completed: int = 0) -> bool:
    fh, data = _lock_and_load()
    try:
        for agent in data.get("agents", []):
            if agent["agent_id"] == agent_id:
                agent["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                agent["status"] = status
                agent["tasks_completed"] = tasks_completed
                _save_and_unlock(fh, data)
                return True
        _save_and_unlock(fh, data)
        return False
    except Exception:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        raise


def submit_task(agent_id: str, description: str, task_type: str = "shell", payload: dict | None = None) -> dict | None:
    fh, data = _lock_and_load()
    try:
        for agent in data.get("agents", []):
            if agent["agent_id"] == agent_id:
                task = {
                    "task_id": str(uuid.uuid4())[:8],
                    "description": description,
                    "task_type": task_type,
                    "payload": payload or {},
                    "status": "queued",
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "completed_at": None,
                    "result": None,
                }
                agent.setdefault("tasks", []).append(task)
                _save_and_unlock(fh, data)
                return task
        _save_and_unlock(fh, data)
        return None
    except Exception:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        raise


def get_tasks(agent_id: str, limit: int = 10) -> list[dict]:
    agent = get_agent(agent_id)
    if not agent:
        return []
    tasks = agent.get("tasks", [])
    return sorted(tasks, key=lambda t: t.get("submitted_at", ""), reverse=True)[:limit]


def update_task(agent_id: str, task_id: str, status: str, result: str | None = None) -> bool:
    fh, data = _lock_and_load()
    try:
        for agent in data.get("agents", []):
            if agent["agent_id"] == agent_id:
                for task in agent.get("tasks", []):
                    if task["task_id"] == task_id:
                        task["status"] = status
                        task["result"] = result
                        task["completed_at"] = datetime.now(timezone.utc).isoformat()
                        _save_and_unlock(fh, data)
                        return True
        _save_and_unlock(fh, data)
        return False
    except Exception:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        raise
