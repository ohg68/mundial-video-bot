import os
import hashlib
import hmac
import json
import time
import base64
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import get_db, User, SessionLocal

router = APIRouter()
security = HTTPBearer(auto_error=False)

SECRET_KEY = os.getenv("JWT_SECRET", "layercut-dev-secret-change-in-production")
TOKEN_EXPIRY = 86400 * 7  # 7 days


def _hash_password(password: str) -> str:
    salt = "layercut"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def _create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + TOKEN_EXPIRY,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_token(token: str) -> dict:
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    if not credentials:
        return None
    payload = _verify_token(credentials.credentials)
    return payload


@router.post("/register")
def register(body: dict):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or len(password) < 4:
        raise HTTPException(status_code=400, detail="Username and password (min 4 chars) required")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")

        user = User(username=username, password_hash=_hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        token = _create_token(user.id, user.username)
        return {"token": token, "username": user.username, "user_id": user.id}
    finally:
        db.close()


@router.post("/login")
def login(body: dict):
    username = body.get("username", "").strip()
    password = body.get("password", "")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or user.password_hash != _hash_password(password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = _create_token(user.id, user.username)
        return {"token": token, "username": user.username, "user_id": user.id}
    finally:
        db.close()


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return {"username": user["username"], "user_id": user["user_id"]}
