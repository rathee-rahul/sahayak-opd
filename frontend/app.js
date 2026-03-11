// ─── CONFIG ───────────────────────────────────────────────────────────────────
const BACKEND_URL = "https://sahayak-opd.onrender.com/chat";
const BACKEND_BASE = BACKEND_URL.replace(/\/chat$/, "");
 
// ─── STATE ────────────────────────────────────────────────────────────────────
let conversationHistory = [];
let isRecording = false;
let recognition = null;
let activeIntent = null;   // tracks which tile the user activated: 'doctor_schedule' | null

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
  const input = document.getElementById("chatInput");
  const text  = messageText || input.value.trim();
  if (!text) return;
 
  input.value = "";
  appendMessage("user", text);
  conversationHistory.push({ role: "user", content: text });
  // Cap history to avoid unbounded memory growth in long sessions
  if (conversationHistory.length > 30) conversationHistory = conversationHistory.slice(-30);
 
  const typingEl = showTypingIndicator();

  // If user is in doctor-search mode, ensure backend treats input as a name query.
  // Prepend 'Dr.' only for plain name inputs — not for resolved disambiguation
  // messages (which contain a comma e.g. 'Dr. Rahul Yadav, Dental Surgery')
  // and not if the prefix is already present.
  let messageToSend = text;
  const isResolvedDisambig = text.includes(',');
  if (activeIntent === 'doctor_schedule' && !text.toLowerCase().startsWith('dr') && !isResolvedDisambig) {
    messageToSend = 'Dr. ' + text;
  }
 
  try {
    const res = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: messageToSend,
        history: conversationHistory.slice(-10),
        active_intent: activeIntent || "",
      }),
    });
 
    const data = await res.json();
    removeTypingIndicator(typingEl);

    // Reset intent after every response — user must click a tile again to re-activate
    if (data.intent && data.intent !== 'doctor_schedule') activeIntent = null;
 
    const msgWrapper = appendMessage("bot", data.reply);
    if (data.show_advisory) appendAdvisoryNote(msgWrapper);
 
    if (data.doctor_query && data.doctor_results && data.doctor_results.length > 0) {
      if (data.ambiguous) {
        renderAmbiguousResults(data.doctor_query, data.doctor_results, msgWrapper);
      } else {
        renderNameSearchResults(data.doctor_query, data.doctor_results, msgWrapper);
      }
    }
 
    // Department doctors
    if (!data.doctor_query && data.department && data.doctors && data.doctors.length > 0) {
      renderDeptDoctors(data.department, data.doctors, msgWrapper, data.sub_specialty);
    }
 
    conversationHistory.push({ role: "assistant", content: data.reply });
 
    speakText(data.reply);
 
  } catch (err) {
    removeTypingIndicator(typingEl);
    appendMessage("bot", "माफ करें, कोई error आई। थोड़ी देर बाद try करें।");
    console.error(err);
  }
}
 
// ─── FILL INPUT (for quick chips) ─────────────────────────────────────────────
function fillInput(text) {
  const input = document.getElementById("chatInput");
  if (input) { input.value = text; input.focus(); }
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
 
// ─── RENDER: AMBIGUOUS DOCTOR ────────────────────────────────────────────────
// Uses addEventListener instead of inline onclick to avoid ReferenceError
// when doctor names contain special characters or single words like "rahul"
function renderAmbiguousResults(query, results, containerEl) {
  const wrapper = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  // Build header
  const header = document.createElement("div");
  header.className = "doctor-cards-header ambiguous-header";
  header.innerHTML = `
    <span class="dch-icon">⚠️</span>
    <span class="dch-title">Multiple doctors found for <strong>"${query}"</strong> — please confirm department:</span>
  `;
  wrapper.appendChild(header);

  // Build disambig list using DOM — no inline JS, no escaping issues
  const list = document.createElement("div");
  list.className = "disambig-list";

  results.forEach((r) => {
    const btn = document.createElement("button");
    btn.className = "disambig-btn";
    btn.innerHTML = `
      <span class="disambig-dept">${r.dept}</span>
      <span class="disambig-name">${r.doctor.name}</span>
      <span class="disambig-days">${r.doctor.opd_days || "Days TBC"}</span>
    `;
    // Safe click handler — no string injection, no encodeURIComponent needed
    btn.addEventListener("click", () => {
      activeIntent = "doctor_schedule";
      sendMessage(`${r.doctor.name}, ${r.dept}`);
    });
    list.appendChild(btn);
  });

  wrapper.appendChild(list);
  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}
 
// ─── RENDER: DEPARTMENT DOCTORS ──────────────────────────────────────────────
function renderDeptDoctors(department, doctors, containerEl, sub_specialty) {
  const todayDocs = doctors.filter(d => isDoctorAvailableToday(d.opd_days));
  const otherDocs = doctors.filter(d => !isDoctorAvailableToday(d.opd_days));
  const sorted    = [...todayDocs, ...otherDocs];
  const wrapper   = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  const subSpecLine = sub_specialty
    ? `<div class="dch-subspecialty">🔎 Filtered for: <strong>${sub_specialty}</strong></div>`
    : "";

  wrapper.innerHTML = `
    <div class="doctor-cards-header">
      <span class="dch-icon">🩺</span>
      <span class="dch-title"><strong>${department}</strong></span>
      <span class="dch-badges">
        <span class="badge-total">${doctors.length} doctors</span>
        ${todayDocs.length > 0 ? `<span class="badge-today">🟢 ${todayDocs.length} today (${TODAY_NAME})</span>` : ""}
      </span>
    </div>
    ${subSpecLine}
    <div class="doctor-cards-scroll">
      ${sorted.map(doc => buildCard(doc, department)).join("")}
    </div>
  `;
  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}
 
// ─── XSS HELPER ──────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ─── BUILD SINGLE DOCTOR CARD ────────────────────────────────────────────────
function buildCard(doc, dept) {
  const isToday = isDoctorAvailableToday(doc.opd_days);
  const initials = doc.name
    .replace(/^(Dr\.|Prof\.)\s*/i, "")
    .split(" ").filter(Boolean).slice(0, 2)
    .map(w => w[0] || "").join("").toUpperCase() || "DR";
  const conditions = doc.conditions
    ? doc.conditions.split(",").slice(0, 4).map(c => `<span class="cond-chip">${escapeHtml(c.trim())}</span>`).join("") : "";
  const subSpec    = doc.sub_specialty ? `<span class="tag tag-blue">${escapeHtml(doc.sub_specialty)}</span>` : "";
  const preferred  = doc.preferred_for ? `<span class="tag tag-green">${escapeHtml(doc.preferred_for)}</span>` : "";
  const isJPNATC   = (doc.center || "").toUpperCase() === "JPNATC";
  const centerLine = doc.center   ? `<div class="cd-row"><span>🏥</span><span>${escapeHtml(doc.center)}</span></div>` : "";
  const locLine    = doc.location ? `<div class="cd-row"><span>📍</span><span>${escapeHtml(doc.location)}</span></div>` : "";
  const roomLine   = doc.room     ? `<div class="cd-row"><span>🚪</span><span>${escapeHtml(doc.room)}</span></div>` : "";
  const notesLine  = doc.notes    ? `<div class="cd-row cd-notes"><span>📝</span><span>${escapeHtml(doc.notes)}</span></div>` : "";
  const deptTag    = dept         ? `<div class="card-dept-label">${escapeHtml(dept)}</div>` : "";
  return `
    <div class="doctor-card ${isToday ? "card-today" : ""} ${isJPNATC ? "card-jpnatc" : ""}">
      ${isJPNATC ? '<div class="jpnatc-banner">⚠️ JPNATC — Trauma Centre Only · Walk-in OPD नहीं है</div>' : ""}
      ${isToday ? '<div class="today-ribbon">Available Today</div>' : ""}
      <div class="card-top">
        <div class="doc-avatar ${isToday ? "avatar-today" : ""}">${escapeHtml(initials)}</div>
        <div class="doc-meta">
          <div class="doc-name">${escapeHtml(doc.name)}</div>
          <div class="doc-desig">${escapeHtml(doc.designation)}</div>
          <div class="doc-unit">${escapeHtml(doc.unit)}</div>
          ${deptTag}
        </div>
      </div>
      <div class="card-schedule">
        <div class="cd-row"><span>📅</span><span>${escapeHtml(doc.opd_days) || "—"}</span></div>
        <div class="cd-row"><span>🕐</span><span>${escapeHtml(doc.opd_timing) || "—"}</span></div>
        ${roomLine}${locLine}${centerLine}${notesLine}
      </div>
      ${subSpec || preferred ? `<div class="card-tags">${subSpec}${preferred}</div>` : ""}
      ${conditions ? `<div class="card-conditions">${conditions}</div>` : ""}
    </div>
  `;
}
 
// ─── SOFT ADVISORY NOTE ──────────────────────────────────────────────────────
function appendAdvisoryNote(containerEl) {
  const el = document.createElement("div");
  el.className = "advisory-note";
  el.innerHTML = `
    <span class="advisory-icon">ℹ️</span>
    <span>Agar takleef achanak bahut badh jaaye ya saans lene mein dikkat ho — <strong>Casualty bhi 24×7 available hai.</strong></span>
  `;
  containerEl.appendChild(el);
}
 
// ─── CHAT HELPERS ─────────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const chat = document.getElementById("chatArea");
  const wrapper = document.createElement("div");
  wrapper.className = role === "user" ? "user-row" : "bot-row";
  const bubble = document.createElement("div");
  bubble.className = role === "user" ? "user-bubble" : "bot-bubble";
  bubble.innerHTML = formatMessage(text);
  wrapper.appendChild(bubble);
  chat.appendChild(wrapper);
  chat.scrollTop = chat.scrollHeight;
  return wrapper;
}
 
function formatMessage(text) {
  // Escape HTML first to prevent XSS, then apply safe markdown transforms
  const safe = escapeHtml(text);
  return safe
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");
}
 
function showTypingIndicator() {
  const chat = document.getElementById("chatArea");
  const el = document.createElement("div");
  el.className = "bot-row typing-wrap";
  el.innerHTML = `
    <div class="avatar-sm">🩺</div>
    <div class="bot-bubble typing-indicator">
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
    appendMessage("bot", "⚠️ Voice input is not supported in this browser. Please use Chrome or Edge, or type your message.");
    return;
  }
  if (isRecording) { recognition?.stop(); return; }
 
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  // "en-IN" gives Hinglish — English medical words stay English ("breast lump",
  // "chest pain") while Hindi words also work ("bukhar", "dard", "pet mein").
  // "hi-IN" forced pure Devanagari which broke matching for English loanwords
  // like ब्रेस्ट→bresta, लैंप→lainpa, हार्ट→harta etc.
  recognition.lang = "en-IN";
  recognition.interimResults = false;
 
  const overlay = document.getElementById("voiceOverlay");
 
  recognition.onstart = () => {
    isRecording = true;
    if (overlay) overlay.style.display = "flex";
    document.getElementById("voiceBtn")?.classList.add("recording");
    window.speechSynthesis?.cancel();
  };
  recognition.onresult = (e) => {
    const t = e.results[0][0].transcript;
    document.getElementById("chatInput").value = t;
    if (overlay) overlay.style.display = "none";
    sendMessage(t);
  };
  recognition.onend = () => {
    isRecording = false;
    if (overlay) overlay.style.display = "none";
    document.getElementById("voiceBtn")?.classList.remove("recording");
  };
  recognition.onerror = (e) => {
    isRecording = false;
    if (overlay) overlay.style.display = "none";
    document.getElementById("voiceBtn")?.classList.remove("recording");
    console.error("Speech error:", e.error);
  };
  recognition.start();
}
 
// ─── HINDI NUMBER CONVERTER ───────────────────────────────────────────────────
function convertNumbersToHindi(text) {
  const ones = [
    "", "ek", "do", "teen", "chaar", "paanch",
    "chhah", "saat", "aath", "nau", "das",
    "gyarah", "barah", "terah", "chaudah", "pandrah",
    "solah", "satrah", "atharah", "unnis", "bees",
    "ikkees", "baais", "teis", "chaubees", "pachchees",
    "chhabees", "sattaees", "atthaees", "untees", "tees",
    "ikattees", "battees", "taintees", "chauntees", "paintees",
    "chattees", "saintees", "artees", "untaalees", "chaalees",
    "ikataalees", "bayaalees", "taintaalees", "chauvaalees", "paintaalees",
    "chiyaalees", "saintaalees", "artaalees", "unchaas", "pachaas",
    "ikaavan", "baavan", "tirpan", "chauvan", "pachpan",
    "chhappan", "sattavan", "attavan", "unsath", "saath",
    "iksath", "baasath", "tirsath", "chavsath", "painsath",
    "chhiyasath", "sarsath", "arsath", "unhattar", "sattar",
    "ikhattar", "bahattar", "tihattar", "chauhattar", "pachhattar",
    "chhihattar", "sathattar", "athhattar", "unnasi", "assi",
    "ikyaasi", "byaasi", "tiraasi", "chauraasi", "pachaasi",
    "chiyaasi", "sataasi", "ataasi", "navasi", "nabbe",
    "ikyaanave", "baanave", "tiraanave", "chauraanave", "pachaanave",
    "chhiyaanave", "sataanave", "ataanave", "ninyaanave"
  ];

  function numToHindi(n) {
    n = parseInt(n, 10);
    if (isNaN(n)) return String(n);
    if (n === 0) return "shunya";
    if (n < 100) return ones[n] || String(n);
    if (n < 1000) {
      const h = Math.floor(n / 100);
      const r = n % 100;
      const prefix = h === 1 ? "ek sau" : ones[h] + " sau";
      return r === 0 ? prefix : prefix + " " + ones[r];
    }
    return String(n);
  }

  return text.replace(/\b(\d+)\b/g, (match) => numToHindi(match));
}

// ─── VOICE OUTPUT ─────────────────────────────────────────────────────────────
async function speakText(text) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();

  const plain = convertNumbersToHindi(
    text
      .replace(/[*_`#]/g, "")
      .replace(/<[^>]+>/g, "")
      .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")
      .replace(/\s+/g, " ")
      .trim()
  );

  if (!plain) return;

  const utt = new SpeechSynthesisUtterance(plain);
  utt.lang  = "hi-IN";

  let voices = window.speechSynthesis.getVoices();
  if (!voices.length) {
    await new Promise(r => setTimeout(r, 1000));
    voices = window.speechSynthesis.getVoices();
  }

  const voice =
    voices.find(v => v.lang === "hi-IN" && /google/i.test(v.name)) ||
    voices.find(v => v.lang === "hi-IN" && /female|woman/i.test(v.name)) ||
    voices.find(v => v.lang === "hi-IN") ||
    voices.find(v => v.lang === "en-IN" && /female|woman/i.test(v.name)) ||
    voices.find(v => v.lang === "en-IN");

  if (voice) utt.voice = voice;

  utt.pitch  = 1.0;
  utt.rate   = 0.88;
  utt.volume = 1.0;

  window.speechSynthesis.speak(utt);
}
 

// ── TILE 1: Symptom Flow ──────────────────────────────────────
function startSymptomFlow() {
  activeIntent = null;
  appendMessage('bot', '<span class="hi">अपनी तकलीफ बताइए — जैसे सिरदर्द, बुखार, पेट दर्द आदि।</span><span class="en">Please describe your symptoms.</span>');
  const input = document.getElementById('chatInput');
  input.placeholder = 'अपने लक्षण लिखें… · Type your symptoms…';
  input.focus();
}

// ── TILE 2: Doctor Search ─────────────────────────────────────
function startDoctorSearch() {
  activeIntent = 'doctor_schedule';
  appendMessage('bot', '<span class="hi">डॉक्टर का नाम लिखें — जैसे "Dr. Anita Dhar" या "Dr. Sharma"</span><span class="en">Type the doctor name to find their OPD schedule.</span>');
  const input = document.getElementById('chatInput');
  input.placeholder = 'डॉक्टर का नाम लिखें… · Type doctor name…';
  input.focus();
}

// ── TILE 3: Department Picker ─────────────────────────────────
function showDeptPicker() {
  activeIntent = null;  // clear doctor search mode when user opens dept picker
  const overlay = document.getElementById('deptPickerOverlay');
  const grid = document.getElementById('deptPickerGrid');

  // Show loading state while fetching
  grid.innerHTML = '<div class="dept-picker-loading">⏳ Loading departments…</div>';
  overlay.classList.add('active');

  fetch(`${BACKEND_BASE}/departments`)
    .then(r => r.json())
    .then(data => {
      const depts = data.departments || [];
      grid.innerHTML = depts.map((d, i) => `
        <button class="dept-chip-btn" data-dept="${escapeHtml(d.name)}">
          <span>${escapeHtml(d.name)}</span>
          <span class="dept-chip-today">${d.available_today > 0 ? '🟢 ' + d.available_today + ' today' : ''}</span>
        </button>
      `).join('');
      // Attach click handlers via addEventListener — no inline JS, safe from XSS
      grid.querySelectorAll('.dept-chip-btn').forEach(btn => {
        btn.addEventListener('click', () => selectDepartment(btn.dataset.dept));
      });
    })
    .catch(() => {
      grid.innerHTML = '<div class="dept-picker-loading">❌ Could not load departments. Please try again.</div>';
    });
}

function closeDeptPicker(e) {
  if (!e || e.target === document.getElementById('deptPickerOverlay')) {
    document.getElementById('deptPickerOverlay').classList.remove('active');
  }
}

function selectDepartment(dept) {
  document.getElementById('deptPickerOverlay').classList.remove('active');
  activeIntent = null;  // ensure browse is not treated as doctor search
  const msg = dept + ' mein kaun se doctors hain?';
  document.getElementById('chatInput').value = msg;
  sendMessage(msg);
}

// ── TILE 4: Emergency Direct (no backend wait) ────────────────
function showEmergencyDirect() {
  const chat = document.getElementById('chatArea');

  const userRow = document.createElement('div');
  userRow.className = 'user-row';
  userRow.innerHTML = '<div class="user-bubble">🚨 Emergency & Helpline</div>';
  chat.appendChild(userRow);

  const el = document.createElement('div');
  el.className = 'emergency-alert';
  el.innerHTML = `
    <div class="emergency-icon">🚨</div>
    <div>
      <strong>AIIMS Emergency — 24×7</strong><br>
      <b>Casualty Block, AIIMS New Delhi</b><br><br>
      📞 <b>011-26588500</b> — Main Hospital<br>
      📞 <b>011-26588700</b> — Emergency<br>
      📞 <b>102</b> — Ambulance (Free)<br>
      📞 <b>112</b> — Police / Fire / Medical<br><br>
      <small>🏥 Casualty OPD: Gate No. 1, 24×7 uplabdh | Always open</small>
    </div>
  `;
  chat.appendChild(el);

  const botRow = document.createElement('div');
  botRow.className = 'bot-row';
  botRow.innerHTML = `
    <div class="avatar-sm">🩺</div>
    <div class="bot-bubble">
      <span class="hi">अगर यह emergency है — तुरंत Casualty Block जाएं या 102 call करें। 🙏</span>
      <span class="en">If this is an emergency, go to Casualty immediately or call 102.</span>
    </div>`;
  chat.appendChild(botRow);
  chat.scrollTop = chat.scrollHeight;
}

// ─── INIT ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  window.speechSynthesis?.getVoices();
  window.speechSynthesis?.addEventListener("voiceschanged", () => window.speechSynthesis.getVoices());
  setTimeout(() => window.speechSynthesis?.getVoices(), 1000);
 
  document.getElementById("sendBtn")?.addEventListener("click", () => sendMessage());
  document.getElementById("chatInput")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  document.getElementById("voiceBtn")?.addEventListener("click", toggleVoice);
  document.getElementById("cancelVoice")?.addEventListener("click", () => {
    recognition?.stop();
    document.getElementById("voiceOverlay").style.display = "none";
  });
});