from pydantic import BaseModel, Field
from typing import Any, Dict, Optional


class Database(BaseModel):
    database: str
    schema_: str = Field(..., alias="schema")


class Query(BaseModel):
    select: Optional[Dict[str, str]] = {}
    where: Optional[Dict[str, Any]] = {}


class Job(BaseModel):
    source: Database
    target: Database
    tables: Dict[str, Query]


class Jobs(BaseModel):
    jobs: Dict[str, Job]
