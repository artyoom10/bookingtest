import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, field_validator


load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "gemini-3.1-flash-lite"

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

app = FastAPI(title="AI Booking Assistant")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Сервис ИИ не настроен: отсутствует переменная GEMINI_API_KEY.",
        )

    status = "ПОЛУЧЕН" if request.passport_received else "НЕ ПОЛУЧЕН"
    business_context = (
        f"Текущий статус паспорта: {status}\n\n"
        f"Сообщение гостя:\n{request.message}"
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
