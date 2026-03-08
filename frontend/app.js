// ─── CONFIG ───────────────────────────────────────────────────────────────────
const BACKEND_URL = "https://sahayak-opd.onrender.com/chat";
 
// ─── STATE ────────────────────────────────────────────────────────────────────
let conversationHistory = [];
let isRecording = false;
let recognition = null;
let activeIntent = null;   // tracks which tile the user activated
 
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
 
  const typingEl = showTypingIndicator();
 
  try {
    // If user is in doctor-search mode, ensure backend treats input as a name query.
  // Prepend 'Dr.' only for plain name inputs — not for resolved disambiguation
  // messages (which contain a comma) and not if prefix already present.
  let messageToSend = text;
  const isResolvedDisambig = text.includes(',');  // e.g. 'Dr. Rahul Yadav, Dental Surgery'
  if (activeIntent === 'doctor_schedule' && !text.toLowerCase().startsWith('dr') && !isResolvedDisambig) {
    messageToSend = 'Dr. ' + text;
  }

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
 
    if (data.is_emergency) appendEmergencyAlert();
 
    const msgWrapper = appendMessage("bot", data.reply);
 
    if (data.doctor_query && data.doctor_results && data.doctor_results.length > 0) {
      if (data.ambiguous) {
        renderAmbiguousResults(data.doctor_query, data.doctor_results, msgWrapper);
      } else {
        renderNameSearchResults(data.doctor_query, data.doctor_results, msgWrapper);
      }
    }
 
    // Cross-department condition matches (e.g. hyperactivity found in Paediatrics + Psychiatry)
    if (!data.doctor_query && data.condition_matches && data.condition_matches.length > 0) {
      renderConditionMatches(data.condition_matches, data.sub_specialty, msgWrapper);
    } else if (!data.doctor_query && data.department && data.doctors && data.doctors.length > 0) {
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
 
function resolveDoctor(index, encodedResults) {
  const results = JSON.parse(decodeURIComponent(encodedResults));
  const chosen  = results[index];
  activeIntent = 'doctor_schedule';  // keep intent so backend routes as doctor search
  sendMessage(`${chosen.doctor.name}, ${chosen.dept}`);
}
 
// ─── RENDER: CROSS-DEPARTMENT CONDITION MATCHES ──────────────────────────────
function renderConditionMatches(matches, sub_specialty, containerEl) {
  // Group matches by department
  const byDept = {};
  matches.forEach(m => {
    if (!byDept[m.dept]) byDept[m.dept] = [];
    byDept[m.dept].push(m.doctor);
  });

  const deptNames = Object.keys(byDept);
  const totalDocs = matches.length;
  const todayCount = matches.filter(m => isDoctorAvailableToday(m.doctor.opd_days)).length;

  const wrapper = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  const subSpecLine = sub_specialty
    ? `<div class="dch-subspecialty">🔎 Matching doctors for: <strong>${sub_specialty}</strong></div>`
    : "";

  // Build cards grouped by department
  let cardsHtml = "";
  deptNames.forEach(dept => {
    const docs = byDept[dept];
    const sorted = [...docs].sort((a,b) =>
      isDoctorAvailableToday(b.opd_days) - isDoctorAvailableToday(a.opd_days)
    );
    cardsHtml += `<div class="dept-group-label">🏥 ${dept}</div>`;
    cardsHtml += sorted.map(doc => buildCard(doc, dept)).join("");
  });

  wrapper.innerHTML = `
    <div class="doctor-cards-header">
      <span class="dch-icon">🔍</span>
      <span class="dch-title">Matching Specialists — <strong>${deptNames.length} departments</strong></span>
      <span class="dch-badges">
        <span class="badge-total">${totalDocs} doctors</span>
        ${todayCount > 0 ? `<span class="badge-today">🟢 ${todayCount} today</span>` : ""}
      </span>
    </div>
    ${subSpecLine}
    <div class="doctor-cards-scroll">${cardsHtml}</div>
  `;
  containerEl.appendChild(wrapper);
  requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add("visible")));
}

// ─── RENDER: DEPARTMENT DOCTORS ──────────────────────────────────────────────
// ── KEY FIX: accepts optional sub_specialty to show filtered context in header ──
function renderDeptDoctors(department, doctors, containerEl, sub_specialty) {
  const todayDocs = doctors.filter(d => isDoctorAvailableToday(d.opd_days));
  const otherDocs = doctors.filter(d => !isDoctorAvailableToday(d.opd_days));
  const sorted    = [...todayDocs, ...otherDocs];
  const wrapper   = document.createElement("div");
  wrapper.className = "doctor-cards-wrapper";

  // If sub_specialty filtered results, show a subtitle line
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
 
// ─── BUILD SINGLE DOCTOR CARD ────────────────────────────────────────────────
function buildCard(doc, dept) {
  const isToday = isDoctorAvailableToday(doc.opd_days);
  const initials = doc.name
    .replace(/^(Dr\.|Prof\.)\s*/i, "")
    .split(" ").filter(Boolean).slice(0, 2)
    .map(w => w[0] || "").join("").toUpperCase() || "DR";
  const conditions = doc.conditions
    ? doc.conditions.split(",").slice(0, 4).map(c => `<span class="cond-chip">${c.trim()}</span>`).join("") : "";
  const subSpec    = doc.sub_specialty ? `<span class="tag tag-blue">${doc.sub_specialty}</span>` : "";
  const preferred  = doc.preferred_for ? `<span class="tag tag-green">${doc.preferred_for}</span>` : "";
  const isJPNATC   = (doc.center || "").toUpperCase() === "JPNATC";
  const centerLine = doc.center   ? `<div class="cd-row"><span>🏥</span><span>${doc.center}</span></div>` : "";
  const locLine    = doc.location ? `<div class="cd-row"><span>📍</span><span>${doc.location}</span></div>` : "";
  const roomLine   = doc.room     ? `<div class="cd-row"><span>🚪</span><span>${doc.room}</span></div>` : "";
  const notesLine  = doc.notes    ? `<div class="cd-row cd-notes"><span>📝</span><span>${doc.notes}</span></div>` : "";
  const deptTag    = dept         ? `<div class="card-dept-label">${dept}</div>` : "";
  return `
    <div class="doctor-card ${isToday ? "card-today" : ""} ${isJPNATC ? "card-jpnatc" : ""}">
      ${isJPNATC ? '<div class="jpnatc-banner">⚠️ JPNATC — Trauma Centre Only · Walk-in OPD नहीं है</div>' : ""}
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
  const chat = document.getElementById("chatArea");
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
  return text
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
    alert("Voice input not supported. Please use Chrome.");
    return;
  }
  if (isRecording) { recognition?.stop(); return; }
 
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = "hi-IN";
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
// Converts Arabic numerals (0–999) in a string to Hindi spoken words,
// so TTS says "das saal" instead of "ten saal", "pachaas" instead of "fifty", etc.
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
    // For larger numbers just return as-is (rare in medical context)
    return String(n);
  }

  // Replace standalone numbers (not inside words) with Hindi equivalents
  return text.replace(/\b(\d+)\b/g, (match) => numToHindi(match));
}

// ─── VOICE OUTPUT ─────────────────────────────────────────────────────────────
async function speakText(text) {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();

  // Clean text: remove markdown, HTML tags, emoji, and extra whitespace
  // Then convert any remaining Arabic numerals to Hindi spoken words
  const plain = convertNumbersToHindi(
    text
      .replace(/[*_`#]/g, "")
      .replace(/<[^>]+>/g, "")
      .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")  // strip emojis
      .replace(/\s+/g, " ")
      .trim()
  );

  if (!plain) return;

  const utt = new SpeechSynthesisUtterance(plain);
  utt.lang  = "hi-IN";

  // Wait for voices to load if not ready yet
  let voices = window.speechSynthesis.getVoices();
  if (!voices.length) {
    await new Promise(r => setTimeout(r, 1000));
    voices = window.speechSynthesis.getVoices();
  }

  // Voice priority:
  // 1. Google हिन्दी (best quality, available on Chrome/Android)
  // 2. Any hi-IN female voice
  // 3. Any hi-IN voice
  // 4. Fallback: en-IN female (sounds more natural than robotic en-US for Hinglish)
  const voice =
    voices.find(v => v.lang === "hi-IN" && /google/i.test(v.name)) ||
    voices.find(v => v.lang === "hi-IN" && /female|woman/i.test(v.name)) ||
    voices.find(v => v.lang === "hi-IN") ||
    voices.find(v => v.lang === "en-IN" && /female|woman/i.test(v.name)) ||
    voices.find(v => v.lang === "en-IN");

  if (voice) utt.voice = voice;

  // Confident, clear, professional tone:
  // pitch 1.0 = neutral (not high/childlike), rate 0.88 = slightly slower = authoritative
  utt.pitch  = 1.0;   // was 1.15 — lower = more confident, less chirpy
  utt.rate   = 0.88;  // was 0.95 — slower = clearer, more professional
  utt.volume = 1.0;   // full volume

  window.speechSynthesis.speak(utt);
}
 

// ── TILE 1: Symptom Flow ──────────────────────────────────────
function startSymptomFlow() {
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
const DEPARTMENTS = [
  "Medicine (General)", "Paediatrics (Children)", "Surgery (General)",
  "Obstetrics & Gynaecology", "Orthopaedics (Bones & Joints)",
  "Dermatology & Venereology (Skin)", "Otorhinolaryngology - ENT",
  "Psychiatry (Mental Health)", "Urology (Kidney & Urinary)",
  "Gastroenterology (Stomach & Digestion)", "G.I. Surgery (Stomach Surgery)",
  "Nephrology (Kidney Disease)", "Endocrinology (Diabetes & Hormones)",
  "Geriatric Medicine (Elderly Care)", "Rheumatology (Joint & Autoimmune)",
  "Physical Medicine & Rehabilitation", "Haematology (Blood Disorders)",
  "Burns & Plastic Surgery", "Paediatric Surgery (Children Surgery)",
  "Cardiology (Heart)", "Cardiothoracic & Vascular Surgery (Heart Surgery)",
  "Neurology (Brain & Nerves)", "Neurosurgery (Brain Surgery)",
  "Ophthalmology (Eyes)", "Dental Surgery", "Oncology (Cancer)",
  "Pulmonary Medicine", "Casualty / Emergency"
];

function showDeptPicker() {
  activeIntent = null;  // clear doctor search mode when user opens dept picker
  const overlay = document.getElementById('deptPickerOverlay');
  const grid = document.getElementById('deptPickerGrid');
  grid.innerHTML = DEPARTMENTS.map((dept, i) => `
    <button class="dept-chip-btn" onclick="selectDepartment(DEPARTMENTS[${i}])">
      <span>${dept}</span>
      <span class="dept-chip-today" id="today-${dept.replace(/[^a-zA-Z]/g,'')}"></span>
    </button>
  `).join('');
  overlay.classList.add('active');
  // Load today counts from backend
  fetch('/departments').then(r => r.json()).then(data => {
    data.departments.forEach(d => {
      const el = document.getElementById('today-' + d.name.replace(/[^a-zA-Z]/g,''));
      if (el && d.available_today > 0) el.textContent = '🟢 ' + d.available_today + ' today';
    });
  }).catch(() => {});
}

function closeDeptPicker(e) {
  if (!e || e.target === document.getElementById('deptPickerOverlay')) {
    document.getElementById('deptPickerOverlay').classList.remove('active');
  }
}

function selectDepartment(dept) {
  document.getElementById('deptPickerOverlay').classList.remove('active');
  activeIntent = null;  // clear any previous tile intent before browse
  const msg = dept + ' mein kaun se doctors hain?';
  document.getElementById('chatInput').value = msg;
  sendMessage(msg);
}

// ── TILE 4: Emergency Direct (no backend wait) ────────────────
function showEmergencyDirect() {
  const chat = document.getElementById('chatArea');

  // User bubble
  const userRow = document.createElement('div');
  userRow.className = 'user-row';
  userRow.innerHTML = '<div class="user-bubble">🚨 Emergency & Helpline</div>';
  chat.appendChild(userRow);

  // Emergency alert card — instant, no API call
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

  // Bot follow-up message
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
  // Preload voices immediately so they're ready when needed
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