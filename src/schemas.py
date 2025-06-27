from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, Union


class Database(BaseModel):
    database: str
    schema_: str = Field(..., alias="schema")


class Query(BaseModel):
    transform: Optional[Dict[str, Optional[str]]] = {}
    where: Optional[Union[str, Dict[str, Any]]] = {}


class Job(BaseModel):
    source: Database
    target: Database
    tables: Dict[str, Query]
