import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _check_collection(name: str) -> str:
    if not COLLECTION_RE.match(name):
        raise ValueError("collection must be 1-64 chars: letters, digits, '_' or '-'")
    return name


class DocumentUpload(BaseModel):
    collection: str = "default"

    @field_validator("collection")
    @classmethod
    def _v_collection(cls, v: str) -> str:
        return _check_collection(v)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    collection: str = "default"
    n_results: int = Field(default=5, ge=1, le=50)

    @field_validator("collection")
    @classmethod
    def _v_collection(cls, v: str) -> str:
        return _check_collection(v)


class QueryResult(BaseModel):
    content: str
    source: str
    score: float
    metadata: dict[str, Any]


class QueryResponse(BaseModel):
    results: list[QueryResult]
    query: str


class CollectionInfo(BaseModel):
    name: str
    document_count: int


class SMBShareConfig(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    server: str = Field(min_length=1, max_length=253)
    share: str = Field(min_length=1, max_length=80)
    username: str = Field(default="guest", max_length=256)
    password: str = Field(default="", max_length=256)
    domain: str = Field(default="WORKGROUP", max_length=64)
    path: str = Field(default="/", max_length=1024)


class SMBListSharesRequest(BaseModel):
    server: str = Field(min_length=1, max_length=253)
    username: str = Field(default="guest", max_length=256)
    password: str = Field(default="", max_length=256)
    domain: str = Field(default="WORKGROUP", max_length=64)


class SMBBrowseRequest(BaseModel):
    server: str = Field(min_length=1, max_length=253)
    share: str = Field(min_length=1, max_length=80)
    path: str = Field(default="/", max_length=1024)
    username: str = Field(default="guest", max_length=256)
    password: str = Field(default="", max_length=256)
    domain: str = Field(default="WORKGROUP", max_length=64)


class SMBFileEntry(BaseModel):
    name: str
    is_directory: bool
    size: int
    last_modified: str


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(default="", max_length=256)
    is_admin: bool = False


class APIKeyResponse(BaseModel):
    name: str
    key_prefix: str
    description: str
    is_admin: bool = False
    created_at: str
    active: bool


class IngestSMBRequest(BaseModel):
    server: str = Field(min_length=1, max_length=253)
    share: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=1024)
    username: str = Field(default="guest", max_length=256)
    password: str = Field(default="", max_length=256)
    domain: str = Field(default="WORKGROUP", max_length=64)
    collection: str = "default"
    recursive: bool = True

    @field_validator("collection")
    @classmethod
    def _v_collection(cls, v: str) -> str:
        return _check_collection(v)


class ServerStatus(BaseModel):
    hostname: str
    ip: str
    mcp_enabled: bool
    total_documents: int
    collections: list[str]
    api_keys_count: int


class SavedShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    server: str = Field(min_length=1, max_length=253)
    share: str = Field(min_length=1, max_length=80)
    path: str = Field(default="/", max_length=1024)
    username: str = Field(default="guest", max_length=256)
    password: str = Field(default="", max_length=256)
    domain: str = Field(default="WORKGROUP", max_length=64)
    collection: str = "default"
    recursive: bool = True
    auto_sync: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=10080)

    @field_validator("collection")
    @classmethod
    def _v_collection(cls, v: str) -> str:
        return _check_collection(v)


class SavedShareInfo(BaseModel):
    name: str
    server: str
    share: str
    path: str = "/"
    username: str = "guest"
    domain: str = "WORKGROUP"
    collection: str = "default"
    recursive: bool = True
    auto_sync: bool = False
    interval_minutes: int = 60
    last_sync: str | None = None
    last_result: dict[str, Any] | None = None
    sync_running: bool = False


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    source: str = Field(min_length=1, max_length=512)
    collection: str = "default"

    @field_validator("collection")
    @classmethod
    def _v_collection(cls, v: str) -> str:
        return _check_collection(v)
