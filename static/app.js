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

messageInput.addEventListener("input", () => {
  charCount.textContent = messageInput.value.length;
  if (messageInput.value.trim()) {
    messageError.textContent = "";
    messageInput.removeAttribute("aria-invalid");
  }
});

statusInputs.forEach((input) => input.addEventListener("change", updateStatusCaption));

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

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        passport_received: getPassportReceived(),
        message,
      }),
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // The fallback below is clearer than exposing a non-JSON server response.
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
    showAnswer(message, "error");
  } finally {
    setLoading(false);
  }
});
