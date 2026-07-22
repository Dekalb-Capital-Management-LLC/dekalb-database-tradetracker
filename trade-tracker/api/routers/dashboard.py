"""Dashboard metadata endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from models.schemas import DashboardCompatibilityResponse
from services.dashboard_capabilities import get_dashboard_capabilities

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/capabilities", response_model=DashboardCompatibilityResponse)
async def get_capabilities():
    return get_dashboard_capabilities()
