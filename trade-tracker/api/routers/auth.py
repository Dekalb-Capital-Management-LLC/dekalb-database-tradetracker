from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import config
from services.auth import AuthError, verify_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"])

class VerifyRequest(BaseModel):
    id_token: str

@router.get("/config")
async def get_auth_config():
    return {
        "auth_enabled": config.AUTH_ENABLED,
        "google_client_id": config.GOOGLE_CLIENT_ID if config.AUTH_ENABLED else "",
        "allowed_domain": config.ALLOWED_EMAIL_DOMAIN,
    }

@router.post("/verify")
async def verify_token(body: VerifyRequest):
    try:
        claims = verify_google_id_token(body.id_token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"email": claims.get("email"), "name": claims.get("name"), "picture": claims.get("picture"), "sub": claims.get("sub")}

@router.get("/me")
async def get_me(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
