"""Shared FastAPI dependencies used across API routers."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.database import UserDB, get_db_ctx
from api.services import auth_service, token_service


def _get_real_ip(request: Request) -> Optional[str]:
    """Extract real client IP, preferring Cloudflare/proxy headers."""
    if request is None:
        return None
    # Cloudflare Tunnel injects the real client IP here
    ip = request.headers.get("CF-Connecting-IP")
    if ip:
        return ip.strip()
    # Standard proxy header fallback
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


_auth_scheme = HTTPBearer(auto_error=False)


class RequireUser:
    def __init__(self, allow_api_token: bool = True):
        self.allow_api_token = allow_api_token

    def __call__(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_auth_scheme),
    ) -> UserDB:
        if not credentials:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

        token = credentials.credentials

        with get_db_ctx() as db:
            # 1. 优先尝试 JWT (网页登录)
            try:
                payload = auth_service.decode_access_token(token)
                user_id = str(payload.get("sub") or "")
                user = auth_service.get_user_by_id(db, user_id)
                if user and user.is_active:
                    # expunge 使 ORM 对象脱离 session，close 后仍可访问属性
                    db.expunge(user)
                    return user
            except Exception:
                # 不是有效的 JWT 或已过期，尝试 API Token
                pass

            # 2. 尝试 API Token (仅在允许时)
            if self.allow_api_token and token.startswith(token_service.TOKEN_PREFIX):
                user = token_service.verify_token(db, token)
                if user and user.is_active:
                    db.expunge(user)
                    return user

        detail = "身份验证失败或该接口不支持 API Token 访问" if self.allow_api_token else "该接口仅限网页端登录访问"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


# 快捷依赖定义
_require_api_user = RequireUser(allow_api_token=True)    # 允许 API Token
_require_web_user = RequireUser(allow_api_token=False)   # 仅限网页登录


def _optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_auth_scheme),
) -> Optional[UserDB]:
    if not credentials:
        return None
    try:
        payload = auth_service.decode_access_token(credentials.credentials)
    except Exception:
        return None
    user_id = str(payload.get("sub") or "")
    if not user_id:
        return None
    with get_db_ctx() as db:
        user = auth_service.get_user_by_id(db, user_id)
        if user:
            db.expunge(user)
        return user
