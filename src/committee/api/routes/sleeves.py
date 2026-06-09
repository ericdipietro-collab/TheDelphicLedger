"""Sleeves endpoints — read-only."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.core.sleeves import list_configs

router = APIRouter(prefix="/api/sleeves", tags=["sleeves"])

SessionDep = Annotated[Session, Depends(get_session)]


class SleeveDefinitionOut(BaseModel):
    sleeve_key: str
    label: str
    target_weight: str  # DecimalStr
    sort_order: int


class SleeveConfigOut(BaseModel):
    id: int
    name: str
    version: int
    status: str
    definitions: list[SleeveDefinitionOut]


@router.get("", response_model=list[SleeveConfigOut])
def get_sleeve_configs(session: SessionDep) -> list[SleeveConfigOut]:
    configs = list_configs(session)
    out = []
    for c in configs:
        out.append(SleeveConfigOut(
            id=c.id,
            name=c.name,
            version=c.version,
            status=c.status,
            definitions=[
                SleeveDefinitionOut(
                    sleeve_key=d.sleeve_key,
                    label=d.label,
                    target_weight=str(d.target_weight),
                    sort_order=d.sort_order,
                )
                for d in sorted(c.definitions, key=lambda x: x.sort_order)
            ],
        ))
    return out
