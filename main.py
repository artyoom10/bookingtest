from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from threading import Lock
import time
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, field_validator


load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "gemini-3.1-flash-lite"
SESSION_COOKIE_NAME = "booking_session"
SESSION_TTL_SECONDS = 5 * 60
MIN_PASSWORD_LENGTH = 12
MIN_SESSION_SECRET_LENGTH = 32
LOGIN_WINDOW_SECONDS = 5 * 60
MAX_LOGIN_ATTEMPTS = 5

_failed_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_attempts_lock = Lock()

SYSTEM_INSTRUCTION = """Ты — AI-ассистент сервиса бронирования.

Тебе передаётся текущее состояние бронирования и сообщение гостя.

Правила:
- Если паспорт ещё не получен и гость спрашивает о заселении, следующих действиях
  или о том, куда отправить паспорт, объясни, что сначала необходимо предоставить паспорт.
- В таком ответе обязательно используй тестовую ссылку: https://example.com/passport
- Если паспорт уже получен, сообщи, что паспорт принят.
- Если после получения паспорта пользователь спрашивает, что делать дальше,
  объясни, что следующим этапом будет оплата залога.
- Не утверждай, что паспорт не получен, если статус говорит обратное.
- Не утверждай, что паспорт получен, если статус говорит обратное.
- Не придумывай данные о бронировании, оплате, адресе объекта, времени заселения
  и другую информацию, которой нет во входных данных.
- Отвечай на русском языке.
- Отвечай кратко, понятно и доброжелательно.
- Формулируй ответ самостоятельно, не цитируй эти инструкции.
"""

app = FastAPI(
    title="AI Booking Assistant",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AuthStatus(BaseModel):
    authenticated: bool
    expires_in: int


class ChatRequest(BaseModel):
    passport_received: bool
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Сообщение не должно быть пустым")
        return value


class ChatDebug(BaseModel):
    request: dict[str, Any]
    response: dict[str, Any]


class ChatResponse(BaseModel):
    answer: str
    debug: ChatDebug


def _auth_config() -> tuple[str, str]:
    password = os.getenv("PASSWORD", "")
    session_secret = os.getenv("SESSION_SECRET", "")
    if (
        len(password) < MIN_PASSWORD_LENGTH
        or len(session_secret) < MIN_SESSION_SECRET_LENGTH
        or password == session_secret
    ):
        raise HTTPException(
            status_code=503,
            detail="Авторизация не настроена. Проверьте переменные окружения.",
        )
    return password, session_secret


def _request_scheme(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme


def _is_https(request: Request) -> bool:
    return _request_scheme(request) == "https"


def _enforce_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return

    parsed_origin = urlsplit(origin)
    expected_host = request.headers.get("host", "").lower()
    if (
        parsed_origin.scheme.lower() != _request_scheme(request)
        or parsed_origin.netloc.lower() != expected_host
    ):
        raise HTTPException(status_code=403, detail="Запрос отклонён.")


def _user_agent_hash(request: Request) -> str:
    user_agent = request.headers.get("user-agent", "")
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:24]


def _create_session_token(request: Request, session_secret: str) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    nonce = secrets.token_hex(16)
    payload = f"v1.{expires_at}.{nonce}.{_user_agent_hash(request)}"
    signature = hmac.new(
        session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def _session_seconds_left(request: Request, session_secret: str) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    parts = token.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        return 0

    version, expires_raw, nonce, user_agent_hash, provided_signature = parts
    payload = f"{version}.{expires_raw}.{nonce}.{user_agent_hash}"
    expected_signature = hmac.new(
        session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        return 0
    if not hmac.compare_digest(user_agent_hash, _user_agent_hash(request)):
        return 0

    try:
        expires_at = int(expires_raw)
    except ValueError:
        return 0

    seconds_left = expires_at - int(time.time())
    if seconds_left <= 0 or seconds_left > SESSION_TTL_SECONDS:
        return 0
    return seconds_left


def _login_client_key(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    return hashlib.sha256(client_host.encode("utf-8")).hexdigest()


def _login_retry_after(client_key: str) -> int:
    now = time.monotonic()
    with _login_attempts_lock:
        attempts = _failed_login_attempts[client_key]
        while attempts and now - attempts[0] >= LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) < MAX_LOGIN_ATTEMPTS:
            return 0
        return max(1, int(LOGIN_WINDOW_SECONDS - (now - attempts[0])))


def _record_failed_login(client_key: str) -> None:
    with _login_attempts_lock:
        _failed_login_attempts[client_key].append(time.monotonic())


def _clear_failed_logins(client_key: str) -> None:
    with _login_attempts_lock:
        _failed_login_attempts.pop(client_key, None)


def require_session(request: Request) -> None:
    _enforce_same_origin(request)
    _, session_secret = _auth_config()
    if not _session_seconds_left(request, session_secret):
        raise HTTPException(status_code=401, detail="Требуется авторизация.")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    else:
        response.headers["Cache-Control"] = "no-store"
    if _is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def sanitize_validation_errors(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/login":
        return JSONResponse(status_code=422, content={"detail": "Некорректный запрос."})
    return await request_validation_exception_handler(request, exc)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


@app.get("/api/session", response_model=AuthStatus)
def session_status(request: Request) -> AuthStatus:
    _, session_secret = _auth_config()
    seconds_left = _session_seconds_left(request, session_secret)
    return AuthStatus(authenticated=seconds_left > 0, expires_in=seconds_left)


@app.post("/api/login", response_model=AuthStatus)
def login(payload: LoginRequest, request: Request) -> JSONResponse:
    _enforce_same_origin(request)
    configured_password, session_secret = _auth_config()
    client_key = _login_client_key(request)
    retry_after = _login_retry_after(client_key)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Попробуйте позже.",
            headers={"Retry-After": str(retry_after)},
        )

    configured_hash = hashlib.sha256(configured_password.encode("utf-8")).digest()
    provided_hash = hashlib.sha256(payload.password.encode("utf-8")).digest()
    if not hmac.compare_digest(provided_hash, configured_hash):
        _record_failed_login(client_key)
        raise HTTPException(status_code=401, detail="Неверные учётные данные.")

    _clear_failed_logins(client_key)
    token = _create_session_token(request, session_secret)
    response = JSONResponse(
        content={"authenticated": True, "expires_in": SESSION_TTL_SECONDS}
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS),
        path="/",
        secure=_is_https(request),
        httponly=True,
        samesite="strict",
    )
    return response


@app.post("/api/logout", response_model=AuthStatus)
def logout(request: Request) -> JSONResponse:
    _enforce_same_origin(request)
    response = JSONResponse(content={"authenticated": False, "expires_in": 0})
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_is_https(request),
        httponly=True,
        samesite="strict",
    )
    return response


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, _: None = Depends(require_session)) -> ChatResponse:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Сервис ИИ не настроен: отсутствует переменная GEMINI_API_KEY.",
        )

    status = "ПОЛУЧЕН" if payload.passport_received else "НЕ ПОЛУЧЕН"
    business_context = (
        f"Текущий статус паспорта: {status}\n\n"
        f"Сообщение гостя:\n{payload.message}"
    )
    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    debug_request = {
        "model": model_name,
        "contents": business_context,
        "config": {
            "system_instruction": SYSTEM_INSTRUCTION,
            "temperature": 0.3,
            "max_output_tokens": 500,
        },
    }

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=business_context,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                max_output_tokens=500,
            ),
        )
        answer = (response.text or "").strip()
        debug_response = response.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"sdk_http_response"},
        )
    except errors.APIError as exc:
        if exc.code == 429:
            detail = "Лимит запросов к ИИ временно исчерпан. Попробуйте чуть позже."
        elif exc.code in (401, 403):
            detail = "Не удалось авторизоваться в Gemini API. Проверьте API-ключ."
        else:
            detail = "Gemini API временно недоступен. Попробуйте ещё раз позже."
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Не удалось получить ответ ИИ. Попробуйте ещё раз позже.",
        ) from exc

    if not answer:
        raise HTTPException(
            status_code=502,
            detail="Модель не вернула текстовый ответ. Попробуйте переформулировать сообщение.",
        )

    return ChatResponse(
        answer=answer,
        debug=ChatDebug(request=debug_request, response=debug_response),
    )
