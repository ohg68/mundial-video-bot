"""Autenticación sin contraseña, con Telegram como identidad.

No hay contraseñas: no hay nada que recordar ni que recuperar. Para entrar se le
pide un código al bot (`/entrar`), que sólo responde a los chats de la whitelist.
El código vale una vez y cinco minutos; a cambio se recibe una sesión de 30 días.

Telegram ya era la identidad real del sistema —los proyectos guardan el chat_id
de quien los creó— y el bot corre en este mismo proceso, así que emitir el código
no necesita ni servidor de correo ni servicio externo.

Llave de emergencia: si `LAYERCUT_RECOVERY_TOKEN` está definida, ese valor vale
como sesión por sí mismo. Es la salida si el bot se cae o Telegram falla — se lee
del panel de Railway y se pega en la web. Sin esto, un bot roto dejaría al dueño
fuera de su propio sistema sin más arreglo que redesplegar.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

log = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer(auto_error=False)

SECRET_KEY = os.getenv("JWT_SECRET", "layercut-dev-secret-change-in-production")
TOKEN_EXPIRY = 86400 * 30          # 30 días: entrar es un trámite, no un hábito
CODE_EXPIRY = 300                  # 5 minutos para copiar 6 dígitos
MAX_CODE_ATTEMPTS = 5              # frena la fuerza bruta sobre 10^6 combinaciones

# code -> {"chat_id": int, "expira": float}. En memoria a propósito: los códigos
# viven cinco minutos y guardarlos en SQLite ensuciaría el backup a Cloudinary que
# corre cada cinco. Si el contenedor reinicia justo en medio, se pide otro.
_CODIGOS: dict[str, dict] = {}
_INTENTOS = {"fallos": 0, "desde": time.time()}


# ── Sesiones ──────────────────────────────────────────────────────────────────

def _create_token(chat_id: int) -> str:
    payload = {"chat_id": chat_id, "exp": int(time.time()) + TOKEN_EXPIRY}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_token(token: str) -> dict | None:
    # La llave de emergencia vale como sesión. compare_digest para no filtrar
    # cuántos caracteres coinciden por el tiempo de respuesta.
    recovery = os.getenv("LAYERCUT_RECOVERY_TOKEN", "")
    if recovery and hmac.compare_digest(token, recovery):
        return {"chat_id": None, "via": "recovery"}

    try:
        payload_b64, sig = token.rsplit(".", 1)
        esperada = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, esperada):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        payload["via"] = "telegram"
        return payload
    except Exception:
        return None


COOKIE_NAME = "layercut_session"


def is_valid_token(token: str | None) -> bool:
    """Lo usa el middleware que protege /api."""
    return bool(token) and _verify_token(token) is not None


def set_session_cookie(response: Response, token: str, request: Request):
    """Guarda la sesión también en una cookie.

    Hace falta porque el navegador pide los medios por su cuenta: un
    `<video src="/api/layers/.../download/video">`, un `<img>` de miniatura o un
    `window.open` para descargar NO pasan por fetch, así que el interceptor que
    pone la cabecera Authorization no llega a verlos y salían sin credencial.
    La cookie sí viaja sola en esas peticiones.

    HttpOnly para que el JavaScript no pueda leerla, y SameSite=Lax para que no
    acompañe a peticiones que vengan de otro sitio.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=TOKEN_EXPIRY,
        httponly=True,
        samesite="lax",
        # En local se sirve por http y una cookie Secure no se guardaría.
        secure=(proto == "https"),
        path="/",
    )


def _token_de(request: Request, credentials) -> str | None:
    """La sesión, venga por cabecera o por cookie."""
    if credentials:
        return credentials.credentials
    return request.cookies.get(COOKIE_NAME)


async def get_current_user(request: Request,
                           credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = _token_de(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    payload = _verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Sesión inválida o vencida")
    return payload


async def get_optional_user(request: Request,
                            credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = _token_de(request, credentials)
    return _verify_token(token) if token else None


# ── Códigos de un solo uso ────────────────────────────────────────────────────

def _limpiar_vencidos():
    ahora = time.time()
    for code in [c for c, d in _CODIGOS.items() if d["expira"] < ahora]:
        _CODIGOS.pop(code, None)


def issue_login_code(chat_id: int) -> tuple[str, int]:
    """Emite un código para ese chat. Lo llama el bot desde /entrar.

    Devuelve (código, minutos de validez). Cada emisión invalida la anterior del
    mismo chat: si pediste otro es porque el primero se perdió, y dejar los viejos
    vivos sólo alarga la ventana en la que sirven.
    """
    _limpiar_vencidos()
    for code in [c for c, d in _CODIGOS.items() if d["chat_id"] == chat_id]:
        _CODIGOS.pop(code, None)

    code = f"{secrets.randbelow(1_000_000):06d}"
    _CODIGOS[code] = {"chat_id": chat_id, "expira": time.time() + CODE_EXPIRY}
    return code, CODE_EXPIRY // 60


def _registrar_fallo() -> bool:
    """Cuenta intentos fallidos en ventanas de 15 min. True si hay que cortar."""
    ahora = time.time()
    if ahora - _INTENTOS["desde"] > 900:
        _INTENTOS.update(fallos=0, desde=ahora)
    _INTENTOS["fallos"] += 1
    return _INTENTOS["fallos"] > MAX_CODE_ATTEMPTS


@router.post("/telegram/verify")
def verify_code(body: dict, request: Request, response: Response):
    """Canjea un código del bot por una sesión.

    Acepta también la llave de emergencia en el mismo campo: si el bot no
    responde, quien entra no tiene otro sitio donde pegarla, y mandarlo a
    construir una cabecera Authorization a mano no es una salida de emergencia.
    """
    code = str(body.get("code", "")).strip().replace(" ", "")
    if not code:
        raise HTTPException(status_code=400, detail="Falta el código")

    recovery = os.getenv("LAYERCUT_RECOVERY_TOKEN", "")
    if recovery and hmac.compare_digest(code, recovery):
        log.warning("Auth: sesión abierta con la llave de emergencia")
        set_session_cookie(response, recovery, request)
        return {"token": recovery, "chat_id": None, "via": "recovery"}

    _limpiar_vencidos()
    datos = _CODIGOS.pop(code, None)          # pop: un código sirve una sola vez
    if not datos:
        if _registrar_fallo():
            log.warning("Auth: demasiados códigos fallidos, cortando por 15 minutos")
            raise HTTPException(
                status_code=429,
                detail="Demasiados intentos fallidos. Esperá 15 minutos y pedí un código nuevo.")
        raise HTTPException(status_code=401, detail="Código incorrecto o vencido. Pedí otro con /entrar.")

    _INTENTOS.update(fallos=0, desde=time.time())
    log.info("Auth: sesión abierta para el chat %s", datos["chat_id"])
    token = _create_token(datos["chat_id"])
    set_session_cookie(response, token, request)
    return {"token": token, "chat_id": datos["chat_id"]}


@router.post("/logout")
def logout(response: Response):
    """Cierra la sesión del lado del servidor borrando la cookie.

    El token de localStorage lo borra el frontend, pero la cookie es HttpOnly y
    sólo se puede quitar desde acá: sin esto, "cerrar sesión" dejaría los medios
    accesibles.
    """
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(get_current_user)):
    # Reponer la cookie a las sesiones que no la tienen es cosa del middleware
    # de `main`, que ve todas las llamadas y no sólo ésta.
    return {"chat_id": user.get("chat_id"), "via": user.get("via")}


@router.get("/status")
def status():
    """Qué vías de entrada están disponibles. Sin autenticar: lo consulta la
    pantalla de login para saber qué explicarle a quien entra."""
    return {
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(
            os.getenv("TELEGRAM_ALLOWED_CHATS", "").strip()),
        "recovery": bool(os.getenv("LAYERCUT_RECOVERY_TOKEN")),
    }
