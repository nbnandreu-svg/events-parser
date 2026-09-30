from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ContentKind = Literal["news", "event"]


class NewsItem(BaseModel):
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    published_at: Optional[datetime] = None
    date_end: Optional[datetime] = None
    date_evidence: str = ""  # исходная строка календаря, до очистки названия
    summary: str = ""
    body: str = ""
    category: str = "новости"
    region: str = "РФ"
    location: str = ""
    country: Optional[str] = None
    event_type: str = ""
    date_certainty: str = "confirmed"  # confirmed | tentative | unconfirmed
    kind: ContentKind = "news"
    tags: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class NewsFilters(BaseModel):
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    source_id: Optional[str] = None
    category: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    event_type: Optional[str] = None
    date_certainty: Optional[str] = None
    kind: Optional[ContentKind] = None
    query: Optional[str] = None
    decision_status: Optional[str] = None
    priority: Optional[str] = None
    limit: int = 100
    offset: int = 0


class ParseResult(BaseModel):
    source_id: str
    source_name: str
    fetched: int = 0
    saved: int = 0
    accepted: int = 0
    past: int = 0
    undated: int = 0
    conflicts: int = 0
    error: Optional[str] = None
    pages_fetched: int = 0
    pages_failed: int = 0
    coverage: str = "configured_pages"
