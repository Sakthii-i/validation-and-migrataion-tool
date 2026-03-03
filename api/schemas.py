from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class SourceCredentialsBigQuery(BaseModel):
    project_id: str
    dataset_location: str = "US"
    service_account_key_path: str


class SourceCredentialsSnowflake(BaseModel):
    account: str
    user: str
    password: str
    warehouse: str
    role: str | None = None


class TargetCredentialsDatabricks(BaseModel):
    server_hostname: str
    http_path: str
    access_token: str


class CreateSessionRequest(BaseModel):
    source_engine: str = Field(..., description="bigquery or snowflake")
    credential_password: str = Field(default="", description="password to unlock credential.txt (required for snowflake)")
    source: dict = Field(default_factory=dict)
    target: TargetCredentialsDatabricks | None = None


class CreateSessionResponse(BaseModel):
    session_id: str
    expires_at: datetime


class SubmitValidationsResponse(BaseModel):
    session_id: str
    validation_ids: list[str]


class ValidationJobPublicStatus(BaseModel):
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = None


class ValidationResultRow(BaseModel):
    validation_id: str
    validation_ts: datetime | None = None
    src_table_name: str | None = None
    tgt_table_name: str | None = None
    validation_type: str | None = None
    row_count: str | None = None
    schema_check: str | None = None
    numeric_check: str | None = None
    hash_validation: str | None = None
    overall_status: str | None = None


class GetValidationResponse(BaseModel):
    validation_id: str
    job: ValidationJobPublicStatus
    result: ValidationResultRow | None = None
