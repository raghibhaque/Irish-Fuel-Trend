"""GET /api/news — recent oil-relevant news events.

v1: returns whatever is in the news_events table (empty until the RSS
monitor from step 8 populates it).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from app.db import connection
from app.models import NewsItem, NewsResponse

router = APIRouter(prefix="/api", tags=["news"])


@router.get("/news", response_model=NewsResponse)
def get_news(limit: int = Query(20, ge=1, le=200)) -> NewsResponse:
    with connection() as conn:
        rows = conn.execute(
            "SELECT published_at, source, title, url, summary, matched_keywords "
            "FROM news_events ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = [
        NewsItem(
            published_at=datetime.fromisoformat(r["published_at"]) if isinstance(r["published_at"], str) else r["published_at"],
            source=r["source"],
            title=r["title"],
            url=r["url"],
            summary=r["summary"],
            matched_keywords=r["matched_keywords"],
        )
        for r in rows
    ]
    return NewsResponse(items=items)
