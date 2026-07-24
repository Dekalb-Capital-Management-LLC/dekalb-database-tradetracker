"""Analyst profiles: ticker visibility and/or category filters."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from db import get_pool
from models.schemas import AnalystCreate, AnalystResponse, AnalystUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysts", tags=["analysts"])

CATEGORY_OPTIONS = [
    "tech",
    "energy",
    "financials",
    "healthcare",
    "consumer",
    "industrials",
]


def _norm_cats(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    out, seen = [], set()
    for v in values:
        key = (v or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _norm_tickers(rows: list | None) -> list[dict] | None:
    if rows is None:
        return None
    out, seen = [], set()
    for r in rows:
        sym = (r.symbol if hasattr(r, "symbol") else r.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        vis = bool(r.visible if hasattr(r, "visible") else r["visible"])
        out.append({"symbol": sym, "visible": vis})
    return out


async def _tickers(pool, analyst_id: int) -> list[dict]:
    rows = await pool.fetch(
        "SELECT symbol, visible FROM analyst_tickers WHERE analyst_id=$1 ORDER BY symbol",
        analyst_id,
    )
    return [{"symbol": r["symbol"], "visible": r["visible"]} for r in rows]


async def _row(pool, r) -> dict:
    return {
        "id": r["id"],
        "display_name": r["display_name"],
        "view_mode": r["view_mode"],
        "categories": list(r["categories"] or []),
        "tickers": await _tickers(pool, r["id"]),
        "onboarded": r["onboarded"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@router.get("/category-options")
async def category_options():
    return {"options": CATEGORY_OPTIONS}


@router.get("", response_model=list[AnalystResponse])
async def list_analysts(pool=Depends(get_pool)):
    rows = await pool.fetch(
        "SELECT id, display_name, view_mode, categories, onboarded, created_at, updated_at "
        "FROM analysts ORDER BY display_name ASC"
    )
    return [await _row(pool, r) for r in rows]


@router.post("", response_model=AnalystResponse)
async def create_analyst(body: AnalystCreate, pool=Depends(get_pool)):
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="display_name required")
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO analysts (display_name)
            VALUES ($1)
            RETURNING id, display_name, view_mode, categories, onboarded, created_at, updated_at
            """,
            name,
        )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Analyst name already exists") from exc
        raise
    logger.info("Created analyst id=%s name=%s", row["id"], name)
    return await _row(pool, row)


@router.patch("/{analyst_id}", response_model=AnalystResponse)
async def update_analyst(analyst_id: int, body: AnalystUpdate, pool=Depends(get_pool)):
    exists = await pool.fetchrow("SELECT id FROM analysts WHERE id=$1", analyst_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Analyst not found")

    cats = _norm_cats(body.categories)
    tickers = _norm_tickers(body.tickers)

    row = await pool.fetchrow(
        """
        UPDATE analysts SET
            view_mode  = COALESCE($2, view_mode),
            categories = COALESCE($3, categories),
            onboarded  = COALESCE($4, onboarded),
            updated_at = NOW()
        WHERE id = $1
        RETURNING id, display_name, view_mode, categories, onboarded, created_at, updated_at
        """,
        analyst_id,
        body.view_mode,
        cats,
        body.onboarded,
    )

    if tickers is not None:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for t in tickers:
                    await conn.execute(
                        """
                        INSERT INTO analyst_tickers (analyst_id, symbol, visible)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (analyst_id, symbol)
                        DO UPDATE SET visible = EXCLUDED.visible, updated_at = NOW()
                        """,
                        analyst_id,
                        t["symbol"],
                        t["visible"],
                    )

    return await _row(pool, row)
