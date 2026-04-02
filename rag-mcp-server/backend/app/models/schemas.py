from pydantic import BaseModel


class DocumentUpload(BaseModel):
    collection: str = "default"


class QueryRequest(BaseModel):
    query: str
    collection: str = "default"
    n_results: int = 5


class QueryResult(BaseModel):
    content: str
    source: str
    score: float
    metadata: dict


class QueryResponse(BaseModel):
    results: list[QueryResult]
    query: str


class CollectionInfo(BaseModel):
    name: str
    document_count: int


class SMBShareConfig(BaseModel):
    name: str
    server: str
    share: str
    username: str = "guest"
    password: str = ""
    domain: str = "WORKGROUP"
    path: str = "/"


class SMBListSharesRequest(BaseModel):
    server: str
    username: str = "guest"
    password: str = ""
    domain: str = "WORKGROUP"


class SMBBrowseRequest(BaseModel):
    server: str
    share: str
    path: str = "/"
    username: str = "guest"
    password: str = ""
    domain: str = "WORKGROUP"


class SMBFileEntry(BaseModel):
    name: str
    is_directory: bool
    size: int
    last_modified: str


class APIKeyCreate(BaseModel):
    name: str
    description: str = ""


class APIKeyResponse(BaseModel):
    name: str
    key_prefix: str
    description: str
    created_at: str
    active: bool


class IngestSMBRequest(BaseModel):
    server: str
    share: str
    path: str
    username: str = "guest"
    password: str = ""
    domain: str = "WORKGROUP"
    collection: str = "default"
    recursive: bool = True


class ServerStatus(BaseModel):
    hostname: str
    ip: str
    mcp_enabled: bool
    total_documents: int
    collections: list[str]
    api_keys_count: int
