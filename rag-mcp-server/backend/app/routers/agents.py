"""Agent management endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services import agent_registry
from app.services.security import require_admin_key, require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentRegister(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    agent_type: str = Field(min_length=1, max_length=64)
    container_name: str = Field(min_length=1, max_length=128)
    config: dict = {}


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
async def get_agent_status(agent_id: str, _: dict = Depends(require_api_key)):
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    recent_tasks = agent_registry.get_tasks(agent_id, limit=5)
    return {
        "agent_id": agent["agent_id"],
        "agent_type": agent["agent_type"],
        "status": agent.get("status", "unknown"),
        "container_name": agent.get("container_name", ""),
        "registered_at": agent.get("registered_at", ""),
        "last_heartbeat": agent.get("last_heartbeat", ""),
        "tasks_completed": agent.get("tasks_completed", 0),
        "recent_tasks": recent_tasks,
    }


@router.post("/register")
async def register_agent(req: AgentRegister, _: dict = Depends(require_api_key)):
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
async def heartbeat(agent_id: str, req: HeartbeatRequest, _: dict = Depends(require_api_key)):
    if agent_registry.update_heartbeat(agent_id, req.status, req.tasks_completed):
        return {"ok": True}
    entry = agent_registry.register_agent(agent_id, "unknown", f"agent-{agent_id}")
    return {"ok": True, "auto_registered": True}


@router.post("/{agent_id}/tasks")
async def submit_task(agent_id: str, req: TaskSubmit, _: dict = Depends(require_admin_key)):
    task = agent_registry.submit_task(agent_id, req.description, req.task_type, req.payload)
    if not task:
        raise HTTPException(404, f"Agent not found: {agent_id}")
    logger.info("Task submitted to %s: %s", agent_id, task["task_id"])
    return task


@router.get("/{agent_id}/tasks")
async def get_tasks(agent_id: str, limit: int = 10, _: dict = Depends(require_api_key)):
    tasks = agent_registry.get_tasks(agent_id, limit=min(limit, 100))
    return {"agent_id": agent_id, "tasks": tasks}
