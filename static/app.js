const authGate = document.querySelector("#auth-gate");
const appLayout = document.querySelector("#app-layout");
const loginForm = document.querySelector("#login-form");
const passwordInput = document.querySelector("#password");
const passwordToggle = document.querySelector("#password-toggle");
const loginSubmit = document.querySelector("#login-submit");
const loginButtonLabel = loginSubmit.querySelector(".login-button-label");
const loginError = document.querySelector("#login-error");
const logoutButton = document.querySelector("#logout-button");
const form = document.querySelector("#chat-form");
const messageInput = document.querySelector("#message");
const messageError = document.querySelector("#message-error");
const charCount = document.querySelector("#char-count");
const submitButton = document.querySelector("#submit-button");
const buttonLabel = submitButton.querySelector(".button-label");
const answerPanel = document.querySelector("#answer-panel");
const answerText = document.querySelector("#answer-text");
const statusCaption = document.querySelector("#status-caption");
const statusCaptionText = document.querySelector("#status-caption-text");
const statusInputs = document.querySelectorAll('[name="passport-status"]');
const debugRequest = document.querySelector("#debug-request");
const debugResponse = document.querySelector("#debug-response");
const MAX_TEXTAREA_HEIGHT = 320;
const MIN_TEXTAREA_HEIGHT = 116;
const TEXTAREA_HEIGHT_STEP = 26;
const TEXTAREA_HEIGHT_CLASSES = Array.from(
  { length: 9 },
  (_, index) => `message-height-${index + 1}`,
);
let sessionExpiryTimer;

const getPassportReceived = () =>
  document.querySelector('[name="passport-status"]:checked').value === "true";

const updateStatusCaption = () => {
  const received = getPassportReceived();
  statusCaption.className = `status-caption status-caption--${received ? "received" : "pending"}`;
  statusCaptionText.textContent = received
    ? "Документ принят и учтён"
    : "Ожидаем документ от гостя";
};

const setLoading = (loading) => {
  submitButton.disabled = loading;
  submitButton.classList.toggle("is-loading", loading);
  buttonLabel.textContent = loading ? "ИИ формирует ответ..." : "Получить ответ ИИ";
};

const resizeMessageInput = () => {
  messageInput.classList.remove(...TEXTAREA_HEIGHT_CLASSES, "message-overflow");
  messageInput.classList.add(TEXTAREA_HEIGHT_CLASSES[0]);
  const contentHeight = messageInput.scrollHeight;
  const heightIndex = Math.min(
    TEXTAREA_HEIGHT_CLASSES.length - 1,
    Math.max(0, Math.ceil((contentHeight - MIN_TEXTAREA_HEIGHT) / TEXTAREA_HEIGHT_STEP)),
  );
  messageInput.classList.replace(TEXTAREA_HEIGHT_CLASSES[0], TEXTAREA_HEIGHT_CLASSES[heightIndex]);
  messageInput.classList.toggle("message-overflow", contentHeight > MAX_TEXTAREA_HEIGHT);
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

const scheduleSessionExpiry = (expiresIn) => {
  window.clearTimeout(sessionExpiryTimer);
  sessionExpiryTimer = window.setTimeout(() => {
    lockApp("Сессия истекла. Введите пароль снова.");
  }, Math.max(1, expiresIn) * 1000);
};

const lockApp = (message = "") => {
  window.clearTimeout(sessionExpiryTimer);
  document.body.classList.add("auth-locked");
  authGate.hidden = false;
  appLayout.setAttribute("inert", "");
  appLayout.setAttribute("aria-hidden", "true");
  loginError.textContent = message;
  passwordInput.value = "";
  passwordInput.type = "password";
  passwordToggle.setAttribute("aria-pressed", "false");
  passwordToggle.setAttribute("aria-label", "Показать пароль");
  window.requestAnimationFrame(() => passwordInput.focus());
};

const unlockApp = (expiresIn) => {
  document.body.classList.remove("auth-locked");
  authGate.hidden = true;
  appLayout.removeAttribute("inert");
  appLayout.setAttribute("aria-hidden", "false");
  loginError.textContent = "";
  passwordInput.value = "";
  scheduleSessionExpiry(expiresIn);
};

const checkSession = async () => {
  try {
    const response = await fetch("/api/session", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(extractError(payload, "Не удалось проверить сессию."));
    }
    if (payload.authenticated) {
      unlockApp(payload.expires_in);
    } else {
      lockApp();
    }
  } catch (error) {
    lockApp(error.message || "Сервис авторизации временно недоступен.");
  }
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

passwordToggle.addEventListener("click", () => {
  const showPassword = passwordInput.type === "password";
  passwordInput.type = showPassword ? "text" : "password";
  passwordToggle.setAttribute("aria-pressed", String(showPassword));
  passwordToggle.setAttribute("aria-label", showPassword ? "Скрыть пароль" : "Показать пароль");
  passwordInput.focus();
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = passwordInput.value;
  if (!password) {
    loginError.textContent = "Введите пароль.";
    passwordInput.focus();
    return;
  }

  loginSubmit.disabled = true;
  loginSubmit.classList.add("is-loading");
  loginButtonLabel.textContent = "Проверяем...";
  loginError.textContent = "";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(extractError(payload, "Не удалось выполнить вход."));
    }
    unlockApp(payload.expires_in);
  } catch (error) {
    passwordInput.value = "";
    loginError.textContent = error.message || "Не удалось выполнить вход.";
    passwordInput.focus();
  } finally {
    loginSubmit.disabled = false;
    loginSubmit.classList.remove("is-loading");
    loginButtonLabel.textContent = "Войти";
  }
});

logoutButton.addEventListener("click", async () => {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    lockApp();
  }
});

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
      credentials: "same-origin",
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
      if (response.status === 401) {
        lockApp("Сессия истекла. Введите пароль снова.");
      }
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

checkSession();
