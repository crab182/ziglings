"""Agent management endpoints."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.services import agent_registry
from app.services.security import require_admin_key, require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])

# Bounds for persisted agent config to keep agents.json from growing unbounded.
MAX_CONFIG_KEYS = 50
MAX_CONFIG_BYTES = 4096


class AgentRegister(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    agent_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    container_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    config: dict = {}

    @field_validator("config")
    @classmethod
    def _bound_config(cls, v: dict) -> dict:
        if len(v) > MAX_CONFIG_KEYS:
            raise ValueError(f"config has too many keys (max {MAX_CONFIG_KEYS})")
        if len(json.dumps(v)) > MAX_CONFIG_BYTES:
            raise ValueError(f"config too large (max {MAX_CONFIG_BYTES} bytes serialized)")
        return v


class TaskSubmit(BaseModel):
    description: str = Field(min_length=1, max_length=4096)
    task_type: str = Field(default="shell", max_length=32)
    payload: dict = {}


class HeartbeatRequest(BaseModel):
    status: str = "running"
    tasks_completed: int = 0


@router.get("/list")
async def list_agents(_: dict = Depends(require_api_key)):
    agents = agent_registry.load_agents()
    return {
        "agents": [
            {
                "agent_id": a["agent_id"],
                "agent_type": a["agent_type"],
                "status": a.get("status", "unknown"),
                "container_name": a.get("container_name", ""),
                "last_heartbeat": a.get("last_heartbeat", ""),
                "tasks_completed": a.get("tasks_completed", 0),
            }
            for a in agents
        ]
    }


@router.get("/{agent_id}/status")
async def get_agent_status(agent_id: str, caller: dict = Depends(require_api_key)):
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    response = {
        "agent_id": agent["agent_id"],
        "agent_type": agent["agent_type"],
        "status": agent.get("status", "unknown"),
        "container_name": agent.get("container_name", ""),
        "registered_at": agent.get("registered_at", ""),
        "last_heartbeat": agent.get("last_heartbeat", ""),
        "tasks_completed": agent.get("tasks_completed", 0),
    }
    # Task bodies (payload/result) are admin-only: only admins submit tasks,
    # so only admins may read them back. Non-admins get status without task details.
    response["recent_tasks"] = (
        agent_registry.get_tasks(agent_id, limit=5) if caller.get("is_admin", False) else []
    )
    return response


@router.post("/register")
async def register_agent(req: AgentRegister, _: dict = Depends(require_admin_key)):
    # Registration creates/updates a persistent agent record — admin-only.
    entry = agent_registry.register_agent(
        agent_id=req.agent_id,
        agent_type=req.agent_type,
        container_name=req.container_name,
        config=req.config,
    )
    logger.info("Agent registered: %s (%s)", req.agent_id, req.agent_type)
    return {"registered": True, "agent_id": entry["agent_id"]}


@router.delete("/{agent_id}")
async def deregister_agent(agent_id: str, _: dict = Depends(require_admin_key)):
    if agent_registry.deregister_agent(agent_id):
        logger.info("Agent deregistered: %s", agent_id)
        return {"deregistered": True, "agent_id": agent_id}
    raise HTTPException(404, f"Agent not found: {agent_id}")


@router.post("/{agent_id}/heartbeat")
async def heartbeat(agent_id: str, req: HeartbeatRequest, _: dict = Depends(require_admin_key)):
    # Heartbeats only update an already-registered agent. Unknown agents are
    # rejected (no auto-registration) so a caller cannot spoof agents or grow
    # agents.json with arbitrary identifiers.
    if agent_registry.update_heartbeat(agent_id, req.status, req.tasks_completed):
        return {"ok": True}
    raise HTTPException(404, f"Agent not registered: {agent_id}")


@router.post("/{agent_id}/tasks")
async def submit_task(agent_id: str, req: TaskSubmit, _: dict = Depends(require_admin_key)):
    task = agent_registry.submit_task(agent_id, req.description, req.task_type, req.payload)
    if not task:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    logger.info("Task submitted to %s: %s", agent_id, task["task_id"])
    return task


@router.get("/{agent_id}/tasks")
async def get_tasks(agent_id: str, limit: int = 10, _: dict = Depends(require_admin_key)):
    # Returns full task bodies (payload/result) — admin-only, matching task submission.
    tasks = agent_registry.get_tasks(agent_id, limit=min(limit, 100))
    return {"agent_id": agent_id, "tasks": tasks}
