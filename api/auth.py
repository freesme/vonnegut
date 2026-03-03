"""
JWT 鉴权模块：用户表管理 + 登录 / 注册 / Token 校验。
"""
from __future__ import annotations

import datetime as dt

import bcrypt
import jwt
import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from api.schemas import LoginRequest, RegisterRequest, TokenResponse
from utils.logger import log

router = APIRouter(prefix="/auth", tags=["认证"])

_bearer = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------
# 用户表
# ------------------------------------------------------------------

def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _ensure_user_table():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # 自动创建默认管理员（仅首次）
    cur.execute("SELECT 1 FROM users WHERE username = %s", ("admin",))
    if cur.fetchone() is None:
        hashed = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            ("admin", hashed),
        )
        conn.commit()
        log.info("已创建默认管理员用户: admin / admin123")

    cur.close()
    conn.close()


def _get_user(username: str) -> dict | None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, password_hash FROM users WHERE username = %s",
        (username,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2]}


def _create_user(username: str, password: str) -> dict:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
        (username, hashed),
    )
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"id": user_id, "username": username}


# ------------------------------------------------------------------
# JWT 工具
# ------------------------------------------------------------------

def _create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(minutes=config.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])


# ------------------------------------------------------------------
# 依赖注入：Token 校验
# ------------------------------------------------------------------

async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """FastAPI 依赖：解析并验证 JWT，返回 username。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = _decode_token(credentials.credentials)
        username: str = payload.get("sub", "")
        if not username:
            raise HTTPException(status_code=401, detail="无效的 Token")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse, summary="登录获取 Token")
def login(req: LoginRequest):
    user = _get_user(req.username)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _create_token(user["username"])
    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/register", response_model=TokenResponse, summary="注册新用户（需登录）")
def register(req: RegisterRequest, _current_user: str = Depends(verify_token)):
    if _get_user(req.username):
        raise HTTPException(status_code=400, detail=f"用户名已存在: {req.username}")
    _create_user(req.username, req.password)
    token = _create_token(req.username)
    return TokenResponse(access_token=token, token_type="bearer")


# ------------------------------------------------------------------
# 初始化（模块加载时建表）
# ------------------------------------------------------------------
try:
    _ensure_user_table()
except Exception as e:
    log.warning(f"用户表初始化失败（数据库可能未就绪）: {e}")
