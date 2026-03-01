// ─── CONFIG ───────────────────────────────────────────────────────────────────
const BACKEND_URL = "https://sahayak-opd.onrender.com/chat";

// ─── STATE ────────────────────────────────────────────────────────────────────
let conversationHistory = [];
let isRecording = false;
let recognition = null;

// ─── TODAY DETECTION ──────────────────────────────────────────────────────────
const TODAY_NAME = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"][new Date().getDay()];
const TODAY_VARIANTS = {
  Monday:    ["mon", "monday"],
  Tuesday:   ["tue", "tuesday"],
  Wednesday: ["wed", "wednesday"],
  Thursday:  ["thu", "thursday"],
  Friday:    ["fri", "friday"],
  Saturday:  ["sat", "saturday"],
  Sunday:    ["sun", "sunday"],
};

function isDoctorAvailableToday(opd_days) {
  if (!opd_days) return false;
  const lower = opd_days.toLowerCase();
  return (TODAY_VARIANTS[TODAY_NAME] || []).some(v => lower.includes(v));
}

// ─── SEND MESSAGE ─────────────────────────────────────────────────────────────
async function sendMessage(messageText) {
  const input = document.getElementById("userInput");
  const text  = messageText || input.value.trim();
  if (!text) return;

  input.value = "";
  appendMessage("user", text);
  conversationHistory.push({ role: "user", content: text });

  const typingEl = showTypingIndicator();

  try {
    const res = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: conversationHistory.slice(-10) }),
    });

    const data = await res.json();
    removeTypingIndicator(typingEl);

    // Emergency banner
    if (data.is_emergency) appendEmergencyAlert();

    // Bot reply bubble
    const msgWrapper = appendMessage("bot", data.reply);

    // ── Doctor name search result ─────────────────────────────────────────────
    if (data.doctor_query && data.doctor_results && data.doctor_results.length > 0) {
      if (data.ambiguous) {
        renderAmbiguousResults(data.doctor_query, data.doctor_results, msgWrapper);
      } else {
        renderNameSearchResults(data.doctor_query, data.doctor_results, msgWrapper);
      }
    }

    // ── Symptom-based department doctors ─────────────────────────────────────
    if (!data.doctor_query && data.department && data.doctors && data.doctors.length > 0) {
      renderDeptDoctors(data.department, data.doctors, msgWrapper);
    }

    conversationHistory.push({ role: "assistant", content: data.reply });

    if (document.getElementById("voiceToggle")?.checked) speakText(data.reply);

  } catch (err) {
    removeTypingIndicator(typingEl);
    appendMessage("bot", "माफ करें, कोई error आई। थोड़ी देर बाद try करें।");
    console.error(err);
  }
}

// ─── RENDER: SPECIFIC DOCTOR NAME SEARCH ──────────────────────────────────────
function renderNameSearchResults(query, results, containerEl) {
  const wrapper = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  const todayCount = results.filter(r => isDoctorAvailableToday(r.doctor.opd_days)).length;

  wrapper.innerHTML = `
    <div class="doctor-cards-header">
      <span class="dch-icon">🔍</span>
      <span class="dch-title">Results for <strong>"${query}"</strong></span>
      <span class="dch-badges">
        <span class="badge-total">${results.length} found</span>
        ${todayCount > 0 ? `<span class="badge-today">🟢 ${todayCount} today</span>` : ""}
      </span>
    </div>
    <div class="doctor-cards-scroll">
      ${[...results]
          .sort((a,b) => isDoctorAvailableToday(b.doctor.opd_days) - isDoctorAvailableToday(a.doctor.opd_days))
          .map(r => buildCard(r.doctor, r.dept)).join("")}
    </div>
  `;

  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}

// ─── RENDER: AMBIGUOUS DOCTOR (SAME NAME, DIFFERENT DEPTS) ───────────────────
function renderAmbiguousResults(query, results, containerEl) {
  const wrapper = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  wrapper.innerHTML = `
    <div class="doctor-cards-header ambiguous-header">
      <span class="dch-icon">⚠️</span>
      <span class="dch-title">Multiple doctors found for <strong>"${query}"</strong> — please confirm department:</span>
    </div>
    <div class="disambig-list">
      ${results.map((r, i) => `
        <button class="disambig-btn" onclick="resolveDoctor(${i}, '${encodeURIComponent(JSON.stringify(results))}')">
          <span class="disambig-dept">${r.dept}</span>
          <span class="disambig-name">${r.doctor.name}</span>
          <span class="disambig-days">${r.doctor.opd_days || "Days TBC"}</span>
        </button>
      `).join("")}
    </div>
  `;

  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}

// Called when user taps a disambiguation option
function resolveDoctor(index, encodedResults) {
  const results = JSON.parse(decodeURIComponent(encodedResults));
  const chosen  = results[index];
  const msg     = `${chosen.doctor.name} from ${chosen.dept}`;
  sendMessage(`${chosen.doctor.name}, ${chosen.dept}`);
}

// ─── RENDER: DEPARTMENT DOCTORS (SYMPTOM ROUTING) ────────────────────────────
function renderDeptDoctors(department, doctors, containerEl) {
  const todayDocs  = doctors.filter(d => isDoctorAvailableToday(d.opd_days));
  const otherDocs  = doctors.filter(d => !isDoctorAvailableToday(d.opd_days));
  const sorted     = [...todayDocs, ...otherDocs];

  const wrapper = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  wrapper.innerHTML = `
    <div class="doctor-cards-header">
      <span class="dch-icon">🩺</span>
      <span class="dch-title"><strong>${department}</strong></span>
      <span class="dch-badges">
        <span class="badge-total">${doctors.length} doctors</span>
        ${todayDocs.length > 0 ? `<span class="badge-today">🟢 ${todayDocs.length} today (${TODAY_NAME})</span>` : ""}
      </span>
    </div>
    <div class="doctor-cards-scroll">
      ${sorted.map(doc => buildCard(doc, department)).join("")}
    </div>
  `;

  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}

// ─── BUILD SINGLE DOCTOR CARD ────────────────────────────────────────────────
function buildCard(doc, dept) {
  const isToday = isDoctorAvailableToday(doc.opd_days);

  const initials = doc.name
    .replace(/^(Dr\.|Prof\.)\s*/i, "")
    .split(" ").filter(Boolean).slice(0, 2)
    .map(w => w[0] || "").join("").toUpperCase() || "DR";

  const conditions = doc.conditions
    ? doc.conditions.split(",").slice(0, 4)
        .map(c => `<span class="cond-chip">${c.trim()}</span>`).join("")
    : "";

  const subSpec   = doc.sub_specialty ? `<span class="tag tag-blue">${doc.sub_specialty}</span>` : "";
  const preferred = doc.preferred_for  ? `<span class="tag tag-green">${doc.preferred_for}</span>` : "";
  // Show center only if it's a named sub-centre (not empty)
  const centerLine = doc.center
    ? `<div class="cd-row"><span>🏥</span><span>${doc.center}</span></div>` : "";
  const locLine    = doc.location ? `<div class="cd-row"><span>📍</span><span>${doc.location}</span></div>` : "";
  const roomLine   = doc.room     ? `<div class="cd-row"><span>🚪</span><span>${doc.room}</span></div>` : "";
  const notesLine  = doc.notes    ? `<div class="cd-row cd-notes"><span>📝</span><span>${doc.notes}</span></div>` : "";
  const deptTag    = dept         ? `<div class="card-dept-label">${dept}</div>` : "";

  return `
    <div class="doctor-card ${isToday ? "card-today" : ""}">
      ${isToday ? '<div class="today-ribbon">Available Today</div>' : ""}
      <div class="card-top">
        <div class="doc-avatar ${isToday ? "avatar-today" : ""}">${initials}</div>
        <div class="doc-meta">
          <div class="doc-name">${doc.name}</div>
          <div class="doc-desig">${doc.designation}</div>
          <div class="doc-unit">${doc.unit}</div>
          ${deptTag}
        </div>
      </div>
      <div class="card-schedule">
        <div class="cd-row"><span>📅</span><span>${doc.opd_days || "—"}</span></div>
        <div class="cd-row"><span>🕐</span><span>${doc.opd_timing || "—"}</span></div>
        ${roomLine}${locLine}${centerLine}${notesLine}
      </div>
      ${subSpec || preferred ? `<div class="card-tags">${subSpec}${preferred}</div>` : ""}
      ${conditions ? `<div class="card-conditions">${conditions}</div>` : ""}
    </div>
  `;
}

// ─── EMERGENCY ALERT ─────────────────────────────────────────────────────────
function appendEmergencyAlert() {
  const chat = document.getElementById("chatContainer");
  const el = document.createElement("div");
  el.className = "emergency-alert";
  el.innerHTML = `
    <div class="emergency-icon">🚨</div>
    <div>
      <strong>EMERGENCY — तुरंत जाएं!</strong><br>
      Casualty / Emergency Block, New Delhi<br>
      <small>24×7 उपलब्ध | Always open</small>
    </div>
  `;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}

// ─── CHAT HELPERS ─────────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const chat = document.getElementById("chatContainer");
  const wrapper = document.createElement("div");
  wrapper.className = `msg-wrapper ${role}`;
  const bubble = document.createElement("div");
  bubble.className = `message ${role}`;
  bubble.innerHTML = formatMessage(text);
  wrapper.appendChild(bubble);
  chat.appendChild(wrapper);
  chat.scrollTop = chat.scrollHeight;
  return wrapper;
}

function formatMessage(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");
}

function showTypingIndicator() {
  const chat = document.getElementById("chatContainer");
  const el = document.createElement("div");
  el.className = "msg-wrapper bot typing-wrap";
  el.innerHTML = `
    <div class="message bot typing-indicator">
      <span>सोच रही हूँ</span>
      <span class="dots"><span>.</span><span>.</span><span>.</span></span>
    </div>`;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function removeTypingIndicator(el) { el?.remove(); }

// ─── VOICE INPUT ─────────────────────────────────────────────────────────────
function toggleVoice() {
  if (!("webkitSpeechRecognition" in window || "SpeechRecognition" in window)) {
    alert("Voice input not supported. Please use Chrome.");
    return;
  }
  if (isRecording) { recognition?.stop(); return; }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = "hi-IN";
  recognition.interimResults = false;

  recognition.onstart = () => {
    isRecording = true;
    document.getElementById("micBtn")?.classList.add("recording");
    window.speechSynthesis?.cancel();
  };
  recognition.onresult = (e) => {
    const t = e.results[0][0].transcript;
    document.getElementById("userInput").value = t;
    sendMessage(t);
  };
  recognition.onend = () => {
    isRecording = false;
    document.getElementById("micBtn")?.classList.remove("recording");
  };
  recognition.onerror = (e) => {
    isRecording = false;
    document.getElementById("micBtn")?.classList.remove("recording");
    console.error("Speech error:", e.error);
  };
  recognition.start();
}

// ─── VOICE OUTPUT ─────────────────────────────────────────────────────────────
function speakText(text) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const plain = text.replace(/[*_`#]/g, "").replace(/<[^>]+>/g, "");
  const utt   = new SpeechSynthesisUtterance(plain);
  utt.lang    = "hi-IN";

  const voices    = window.speechSynthesis.getVoices();
  // Prefer a female Hindi voice
  const femaleHindi = voices.find(v =>
    v.lang === "hi-IN" && /female|woman|girl/i.test(v.name)
  ) || voices.find(v =>
    v.lang === "hi-IN" && /google/i.test(v.name)
  ) || voices.find(v => v.lang === "hi-IN");

  if (femaleHindi) utt.voice = femaleHindi;
  utt.pitch = 1.15;   // slightly higher pitch → more feminine
  utt.rate  = 0.95;

  window.speechSynthesis.speak(utt);
}

// ─── QUICK CHIPS ─────────────────────────────────────────────────────────────
function sendQuickChip(text) { sendMessage(text); }

// ─── INIT ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("sendBtn")?.addEventListener("click", () => sendMessage());
  document.getElementById("userInput")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  document.getElementById("micBtn")?.addEventListener("click", toggleVoice);
  window.speechSynthesis?.addEventListener("voiceschanged", () => window.speechSynthesis.getVoices());
});
