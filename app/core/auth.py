import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.models.entities import Operator

# Secret configuration with sensible defaults for development
SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "fleet-ops-secret-key-super-secure-change-in-prod-123456789")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def ensure_default_operator(session: AsyncSession) -> Operator:
    """Ensure at least one operator exists (admin / admin123)."""
    result = await session.execute(select(Operator).limit(1))
    existing = result.scalar_one_or_none()
    if not existing:
        admin_op = Operator(
            username="admin",
            hashed_password=get_password_hash("admin123"),
            full_name="Fleet Operations Admin",
            role="admin",
            is_active=True
        )
        session.add(admin_op)
        await session.commit()
        await session.refresh(admin_op)
        return admin_op
    return existing

async def get_current_operator(
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db)
) -> Operator:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await session.execute(select(Operator).where(Operator.username == username))
    operator = result.scalar_one_or_none()
    if operator is None or not operator.is_active:
        raise credentials_exception
    return operator
