from typing import Any, Dict, List, Optional


from pydantic import BaseModel, Field


class DatabricksConnection(BaseModel):
    host: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    warehouse_id: str = Field(..., min_length=1)
    catalog: Optional[str] = None
    schema: Optional[str] = None
    timeout_seconds: int = 90
    max_rows: int = 200


class TranslateRequest(BaseModel):
    bq_sql: str = Field(..., min_length=1)
    source_engine: str = "bigquery"
    provider: str = "OpenAI"
    model: str = "gpt-5-mini"
    mode: str = "Auto (deterministic -> LLM migration -> validation)"
    api_key: Optional[str] = None
    run_in_databricks: bool = False
    databricks: Optional[DatabricksConnection] = None
    session_id: Optional[str] = None


class TranslateResponse(BaseModel):
    translated_sql: str
    explanation: str
    stats: Dict[str, Any]
    final_error: Optional[str]
    validation: Dict[str, Any]
    suggestions: List[str]
    execution: Optional[Dict[str, Any]] = None


class DatabricksExecuteRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    databricks: DatabricksConnection


class DatabricksExecuteResponse(BaseModel):
    execution: Dict[str, Any]


class StoredExecuteRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    source_sql: Optional[str] = None
    source_engine: str = "bigquery"
    provider: str = "OpenAI"
    model: str = "gpt-5-mini"
    api_key: Optional[str] = None
    session_id: Optional[str] = None


class QueryStatsResponse(BaseModel):
    session_id: Optional[str] = None
    stats: Dict[str, int]


class NormalizeRequest(BaseModel):
    sql: str


class NormalizeResponse(BaseModel):
    normalized_sql: str


class GitFilesRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    ref: Optional[str] = None
    token: Optional[str] = None


class GitBranchesRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    token: Optional[str] = None


class GitBranchesResponse(BaseModel):
    branches: List[str]
    default_branch: str


class GitFilesResponse(BaseModel):
    files: List[str]
    ref: str


class GitFileRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    ref: Optional[str] = None
    token: Optional[str] = None


class GitFileResponse(BaseModel):
    path: str
    content: str
    ref: str


class GitUploadRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    token: Optional[str] = None
    content: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    mode: str = "existing"
    branch: Optional[str] = None
    base_branch: Optional[str] = None
    new_branch: Optional[str] = None
    message: Optional[str] = None


class GitUploadResponse(BaseModel):
    branch: str
    path: str
    commit_sha: Optional[str] = None
    html_url: Optional[str] = None


class CacheClearResponse(BaseModel):
    cleared_persistent_entries: int
    expression_cache_cleared: bool


class ConfigResponse(BaseModel):
    providers: List[str]
    provider_model_options: Dict[str, List[str]]
    modes: List[str]


class CsvQueryResult(BaseModel):
    row_index: int
    query_index: int
    original_sql: str
    translated_sql: str
    explanation: str
    stats: Dict[str, Any]
    final_error: Optional[str]
    validation: Dict[str, Any]
    suggestions: List[str]
    execution: Optional[Dict[str, Any]] = None


class CsvTranslateResponse(BaseModel):
    total_queries: int
    results: List[CsvQueryResult]
    headers: List[str] = []
    translated_rows: List[Dict[str, str]] = []
