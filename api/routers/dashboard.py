"""Dashboard router."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.database import UserDB, get_db
from api.dependencies import _require_api_user
from api.services import tracking_board_service

router = APIRouter(tags=["dashboard"])


@router.get("/v1/dashboard/tracking-board")
def get_dashboard_tracking_board(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    return tracking_board_service.get_tracking_board(db, current_user.id)
