// ============================================================
//  SAHAYAK — AIIMS OPD Assistant  |  app.js
//  Connects to FastAPI backend at http://127.0.0.1:8000/chat
// ============================================================

const BACKEND_URL = "http://127.0.0.1:8000/chat";

// ── DOM elements ──
const chatArea     = document.getElementById("chatArea");
const chatInput    = document.getElementById("chatInput");
const sendBtn      = document.getElementById("sendBtn");
const voiceBtn     = document.getElementById("voiceBtn");
const voiceOverlay = document.getElementById("voiceOverlay");
const cancelVoice  = document.getElementById("cancelVoice");

// ── Conversation history ──
let conversationHistory = [];

// ============================================================
//  1.  FILL INPUT FROM CHIP
// ============================================================

function fillInput(text) {
  chatInput.value = text;
  chatInput.focus();
}

// ============================================================
//  2.  SEND MESSAGE
// ============================================================

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  appendMessage("user", text);
  chatInput.value = "";

  conversationHistory.push({ role: "user", content: text });
  showTyping(true);
  sendBtn.disabled = true;

  try {
    const response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        history: conversationHistory
      })
    });

    if (!response.ok) throw new Error(`Server error: ${response.status}`);

    const data = await response.json();
    const reply = data.reply || "Sorry, I couldn't understand that.";

    conversationHistory.push({ role: "assistant", content: reply });

    showTyping(false);
    appendMessage("bot", reply);
    speakText(reply);

  } catch (err) {
    showTyping(false);
    const errMsg = err.message.includes("Failed to fetch")
      ? "⚠️ Cannot reach the server. Make sure your backend is running."
      : `⚠️ Error: ${err.message}`;
    appendMessage("bot", errMsg, true);
  }

  sendBtn.disabled = false;
  chatInput.focus();
}

// ============================================================
//  3.  APPEND MESSAGE BUBBLE
// ============================================================

function appendMessage(role, text, isHtml = false) {
  if (role === "user") {
    const row = document.createElement("div");
    row.classList.add("user-row");
    const bubble = document.createElement("div");
    bubble.classList.add("user-bubble");
    bubble.innerHTML = isHtml ? text : formatText(text);
    row.appendChild(bubble);
    chatArea.appendChild(row);
  } else {
    const row = document.createElement("div");
    row.classList.add("bot-row");
    const avatar = document.createElement("div");
    avatar.classList.add("avatar-sm");
    avatar.textContent = "🩺";
    const bubble = document.createElement("div");
    bubble.classList.add("bot-bubble");
    bubble.innerHTML = isHtml ? text : formatText(text);
    row.appendChild(avatar);
    row.appendChild(bubble);
    chatArea.appendChild(row);
  }

  chatArea.scrollTop = chatArea.scrollHeight;
}

// ── Basic text formatter ──
function formatText(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

// ============================================================
//  4.  TYPING INDICATOR
// ============================================================

function showTyping(visible) {
  const existing = document.getElementById("typingIndicator");
  if (existing) existing.remove();

  if (visible) {
    const row = document.createElement("div");
    row.classList.add("bot-row");
    row.id = "typingIndicator";
    const avatar = document.createElement("div");
    avatar.classList.add("avatar-sm");
    avatar.textContent = "🩺";
    const bubble = document.createElement("div");
    bubble.classList.add("bot-bubble");
    bubble.innerHTML = "<em style='color:#aaa;font-size:12px'>सोच रहा हूँ… · Thinking…</em>";
    row.appendChild(avatar);
    row.appendChild(bubble);
    chatArea.appendChild(row);
    chatArea.scrollTop = chatArea.scrollHeight;
  }
}

// ============================================================
//  5.  VOICE INPUT  (Web Speech API)
// ============================================================

let recognition = null;
let isRecording = false;

function setupVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    voiceBtn.style.opacity = "0.4";
    voiceBtn.disabled = true;
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "hi-IN";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    chatInput.value = transcript;
    stopRecording();
    sendMessage();
  };

  recognition.onerror = (event) => {
    console.error("Speech error:", event.error);
    stopRecording();
    if (event.error === "not-allowed") {
      appendMessage("bot", "⚠️ Microphone access denied. Please allow microphone in browser settings.");
    }
  };

  recognition.onend = () => stopRecording();
}

function startRecording() {
  if (!recognition) return;
  // Stop Sahayak voice immediately when mic is tapped
  window.speechSynthesis.cancel();
  isRecording = true;
  voiceOverlay.classList.add("active");
  recognition.start();
}

function stopRecording() {
  if (!recognition) return;
  isRecording = false;
  voiceOverlay.classList.remove("active");
  try { recognition.stop(); } catch (_) {}
}

voiceBtn.addEventListener("click", () => {
  if (isRecording) stopRecording();
  else startRecording();
});

cancelVoice.addEventListener("click", () => stopRecording());

// ============================================================
//  6.  VOICE OUTPUT
// ============================================================

function speakText(text) {
  if (!window.speechSynthesis) return;
  const clean = text.replace(/<[^>]+>/g, "");
  if (!clean.trim()) return;

  window.speechSynthesis.cancel();

  const trySpeak = () => {
    const voices = window.speechSynthesis.getVoices();
    const preferred =
      voices.find(v => v.name === "Google हिन्दी") ||
      voices.find(v => v.lang === "hi-IN")          ||
      voices.find(v => v.lang === "en-IN")          ||
      voices[0];

    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.voice  = preferred;
    utterance.lang   = "hi-IN";
    utterance.rate   = 0.85;
    utterance.pitch  = 1.0;
    utterance.volume = 1.0;

    window.speechSynthesis.speak(utterance);
  };

  if (window.speechSynthesis.getVoices().length === 0) {
    window.speechSynthesis.addEventListener("voiceschanged", trySpeak, { once: true });
  } else {
    trySpeak();
  }
}

// ============================================================
//  7.  SEND ON ENTER KEY
// ============================================================

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener("click", sendMessage);

// ============================================================
//  8.  INIT
// ============================================================

setupVoiceInput();
chatInput.focus();