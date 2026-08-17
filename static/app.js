const form = document.querySelector("#chat-form");
const messageInput = document.querySelector("#message");
const messageError = document.querySelector("#message-error");
const charCount = document.querySelector("#char-count");
const submitButton = document.querySelector("#submit-button");
const buttonLabel = submitButton.querySelector(".button-label");
const answerPanel = document.querySelector("#answer-panel");
const answerText = document.querySelector("#answer-text");
const statusCaption = document.querySelector("#status-caption");
const statusInputs = document.querySelectorAll('[name="passport-status"]');
const debugRequest = document.querySelector("#debug-request");
const debugResponse = document.querySelector("#debug-response");
const MAX_TEXTAREA_HEIGHT = 320;

const getPassportReceived = () =>
  document.querySelector('[name="passport-status"]:checked').value === "true";

const updateStatusCaption = () => {
  const received = getPassportReceived();
  statusCaption.className = `status-caption status-caption--${received ? "received" : "pending"}`;
  statusCaption.innerHTML = `
    <span class="status-dot" aria-hidden="true"></span>
    ${received ? "Документ принят и учтён" : "Ожидаем документ от гостя"}
  `;
};

const setLoading = (loading) => {
  submitButton.disabled = loading;
  submitButton.classList.toggle("is-loading", loading);
  buttonLabel.textContent = loading ? "ИИ формирует ответ..." : "Получить ответ ИИ";
};

const resizeMessageInput = () => {
  messageInput.style.height = "auto";
  const height = Math.min(messageInput.scrollHeight, MAX_TEXTAREA_HEIGHT);
  messageInput.style.height = `${height}px`;
  messageInput.style.overflowY =
    messageInput.scrollHeight > MAX_TEXTAREA_HEIGHT ? "auto" : "hidden";
};

const showAnswer = (text, state = "success") => {
  answerPanel.classList.remove("answer-panel--loading", "answer-panel--error", "answer-panel--success");
  answerPanel.classList.add(`answer-panel--${state}`);
  answerText.className = "answer-text";
  answerText.textContent = text;
};

const extractError = (payload, fallback) => {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail) && payload.detail[0]?.msg) {
    return payload.detail[0].msg.replace(/^Value error, /, "");
  }
  return fallback;
};

const renderDebug = (element, value) => {
  element.textContent = JSON.stringify(value, null, 2);
};

messageInput.addEventListener("input", () => {
  resizeMessageInput();
  charCount.textContent = messageInput.value.length;
  if (messageInput.value.trim()) {
    messageError.textContent = "";
    messageInput.removeAttribute("aria-invalid");
  }
});

statusInputs.forEach((input) => input.addEventListener("change", updateStatusCaption));
resizeMessageInput();

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = messageInput.value.trim();
  if (!message) {
    messageError.textContent = "Введите сообщение гостя.";
    messageInput.setAttribute("aria-invalid", "true");
    messageInput.focus();
    return;
  }

  setLoading(true);
  answerPanel.className = "answer-panel answer-panel--loading";
  answerText.className = "answer-text";
  answerText.textContent = "ИИ формирует ответ...";

  const requestPayload = {
    passport_received: getPassportReceived(),
    message,
  };
  renderDebug(debugRequest, {
    method: "POST",
    path: "/api/chat",
    body: requestPayload,
  });
  renderDebug(debugResponse, { status: "Ожидание ответа Gemini API" });

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // The fallback below is clearer than exposing a non-JSON server response.
    }

    if (payload?.debug?.request) {
      renderDebug(debugRequest, payload.debug.request);
    }
    if (payload?.debug?.response) {
      renderDebug(debugResponse, payload.debug.response);
    } else {
      renderDebug(debugResponse, {
        http_status: response.status,
        body: payload,
      });
    }

    if (!response.ok) {
      throw new Error(extractError(payload, "Не удалось получить ответ. Попробуйте ещё раз."));
    }

    showAnswer(payload.answer);
  } catch (error) {
    const message =
      error instanceof TypeError
        ? "Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз."
        : error.message;
    if (error instanceof TypeError) {
      renderDebug(debugResponse, { error: "network_error", message });
    }
    showAnswer(message, "error");
  } finally {
    setLoading(false);
  }
});
