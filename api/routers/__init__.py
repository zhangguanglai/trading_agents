"""API routers package."""

from api.routers.analyze import router as analyze_router
from api.routers.auth import router as auth_router
from api.routers.chip_deep import router as chip_deep_router
from api.routers.config import router as config_router
from api.routers.dashboard import router as dashboard_router
from api.routers.feedback import router as feedback_router
from api.routers.jobs import router as jobs_router
from api.routers.market import router as market_router
from api.routers.misc import router as misc_router, mount_static_files
from api.routers.portfolio import router as portfolio_router
from api.routers.reports import router as reports_router
from api.routers.scheduled import router as scheduled_router
from api.routers.watchlist import router as watchlist_router

__all__ = [
    "analyze_router",
    "auth_router",
    "chip_deep_router",
    "config_router",
    "dashboard_router",
    "feedback_router",
    "jobs_router",
    "market_router",
    "misc_router",
    "mount_static_files",
    "portfolio_router",
    "reports_router",
    "scheduled_router",
    "watchlist_router",
]
