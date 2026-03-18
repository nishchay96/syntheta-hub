/* ============================================================
   SYNTHETA OMEGA V3 — Frontend JavaScript
   Handles: WebSocket, Orb states, Chat, Terminal, Health
   ============================================================ */

// ── CONSTANTS ────────────────────────────────────────────
const WS_URL = `ws://${window.location.host}/ws/sat_0`;
const VITALS_INTERVAL_MS = 5000; // Poll health every 5s

// ── STATE ────────────────────────────────────────────────
let ws = null;
let isChatMode = false;
let currentOrbState = 'idle';
let reconnectTimer = null;

// ── DOM ──────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const orb           = $('syntheta-orb');
const orbLabel      = $('orb-label');
const orbSublabel   = $('orb-sublabel');
const viewOrb       = $('view-orb');
const viewChat      = $('view-chat');
const chatHistory   = $('chat-history');
const chatInputArea = $('chat-input-area');
const chatInput     = $('chat-input');
const btnSend       = $('btn-send');
const btnChatToggle = $('btn-chat-toggle');
const btnTerminal   = $('btn-terminal');
const btnHealth     = $('btn-health');
const btnTheme      = $('btn-theme');
const connDot       = $('conn-dot');
const connLabel     = $('conn-label');
const termDrawer    = $('terminal-drawer');
const termLogs      = $('terminal-logs');
const closeTerminal = $('close-terminal');
const overlayHealth = $('overlay-health');
const closeHealth   = $('close-health');

// ── WEBSOCKET ────────────────────────────────────────────
function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  updateConnectionStatus('connecting');
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    clearTimeout(reconnectTimer);
    updateConnectionStatus('online');
    logToTerminal('WebSocket connected to Syntheta Engine.', 'success');
    setOrbState('idle', 'READY', 'Engine connected — awaiting input.');
  };

  ws.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); }
    catch { return; }
    handleServerMessage(data);
  };

  ws.onclose = () => {
    updateConnectionStatus('offline');
    setOrbState('idle', 'OFFLINE', 'Connection lost. Reconnecting...');
    logToTerminal('Connection closed. Retrying in 4s...', 'error');
    reconnectTimer = setTimeout(connect, 4000);
  };

  ws.onerror = () => {
    logToTerminal('WebSocket error encountered.', 'error');
  };
}

function handleServerMessage(data) {
  switch (data.type) {
    case 'engine_state':
      handleEngineState(data.state);
      break;

    case 'syntheta_response':
      appendChatMessage(data.content, 'system');
      setOrbState('speaking', 'SPEAKING', formatEllipsis(data.content, 60));
      // Reset to idle after a speech animation window
      setTimeout(() => {
        if (currentOrbState === 'speaking') {
          setOrbState('idle', 'READY', 'Engine connected — awaiting input.');
        }
      }, Math.min(5000, data.content.length * 60));
      break;

    case 'engine_log':
      logToTerminal(data.content, getLogType(data.level));
      break;

    case 'vitals_update':
      updateVitals(data);
      break;
  }
}

function handleEngineState(state) {
  switch (state) {
    case 'processing':
      setOrbState('processing', 'THINKING', 'Processing your request...');
      break;
    case 'web_search':
      setOrbState('web_search', 'SEARCHING', 'Fetching live data from the web...');
      break;
    case 'speaking':
      setOrbState('speaking', 'SPEAKING', 'Generating response...');
      break;
    case 'idle':
    default:
      setOrbState('idle', 'READY', 'Engine connected — awaiting input.');
  }
}

// ── AUDIO RHYTHM SIMULATOR ───────────────────────────────
let animationFrameId = null;
let currentVolume = 0;
let targetVolume = 0;

function startVibration() {
  if (animationFrameId) return;
  
  const tick = () => {
    if (currentOrbState !== 'speaking') {
      orb.style.transform = '';
      orb.style.boxShadow = '';
      animationFrameId = null;
      return;
    }
    
    // Smoothly interpolate to target volume
    currentVolume += (targetVolume - currentVolume) * 0.15;
    
    // Randomly change target to simulate vocal syllables and pauses
    if (Math.random() < 0.12) {
      targetVolume = Math.random() * 0.8 + 0.2; // Syllable burst
    } else if (Math.random() < 0.05) {
      targetVolume = 0; // Micro-pause
    }
    
    // Apply scaling and slight chaotic translation for realism
    const scale = 1.05 + (currentVolume * 0.09); 
    const x = (Math.random() - 0.5) * currentVolume * 5;
    const y = (Math.random() - 0.5) * currentVolume * 5;
    
    orb.style.transform = `scale(${scale}) translate(${x}px, ${y}px) translateZ(0)`;
    
    // Pulse the neon glow
    const shadowSize = 30 + currentVolume * 50;
    const shadowAlpha = 0.3 + currentVolume * 0.5;
    orb.style.boxShadow = `0 0 ${shadowSize}px rgba(52,217,154,${shadowAlpha})`;

    animationFrameId = requestAnimationFrame(tick);
  };
  
  tick();
}

// ── ORB STATE ────────────────────────────────────────────
function setOrbState(state, label, sublabel = '') {
  currentOrbState = state;
  orb.className = `orb state-${state}`;
  orbLabel.textContent = label;
  orbSublabel.textContent = sublabel;
  
  if (state === 'speaking') {
    startVibration();
  } else {
    // Clean up inline styles when leaving speaking mode
    orb.style.transform = '';
    orb.style.boxShadow = '';
  }
}

// ── CHAT ─────────────────────────────────────────────────
function appendChatMessage(text, sender) {
  const isUser = sender === 'user';
  const wrapper = document.createElement('div');
  wrapper.className = `chat-msg ${isUser ? 'user' : 'system'}`;

  const avatar = document.createElement('div');
  avatar.className = `msg-avatar ${isUser ? 'user-avatar' : 'sys-avatar'}`;
  avatar.innerHTML = isUser
    ? '<i class="fa-regular fa-user"></i>'
    : '<i class="fa-solid fa-brain"></i>';

  const bubble = document.createElement('div');
  bubble.className = `msg-bubble ${isUser ? 'user-bubble' : 'sys-bubble'}`;
  bubble.textContent = text;

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  chatHistory.appendChild(wrapper);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function submitMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    logToTerminal('Not connected — cannot send.', 'error');
    return;
  }

  appendChatMessage(text, 'user');
  chatInput.value = '';
  setOrbState('processing', 'ROUTING', 'Analysing your input...');

  ws.send(JSON.stringify({ type: 'user_input', content: text }));
}

// ── TERMINAL ─────────────────────────────────────────────
function logToTerminal(message, type = 'info') {
  const now = new Date().toLocaleTimeString('en-GB', { hour12: false });
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  line.textContent = `${now}  ${message}`;
  termLogs.appendChild(line);
  termLogs.scrollTop = termLogs.scrollHeight;

  // Trim logs so they don't grow unbounded
  while (termLogs.children.length > 200) {
    termLogs.removeChild(termLogs.firstChild);
  }
}

function getLogType(level = '') {
  const l = level.toLowerCase();
  if (l === 'error') return 'error';
  if (l === 'warn' || l === 'warning') return 'warn';
  if (l === 'info') return 'info';
  if (l === 'success') return 'success';
  return 'default';
}

// ── HEALTH / VITALS ───────────────────────────────────────
function updateVitals(data) {
  if (data.vram !== undefined) {
    $('stat-vram').textContent = data.vram;
    const pct = parseFloat(data.vram_pct) || 0;
    $('bar-vram').style.width = `${pct}%`;
  }
  if (data.ram !== undefined) {
    $('stat-ram').textContent = data.ram;
    const pct = parseFloat(data.ram_pct) || 0;
    $('bar-ram').style.width = `${pct}%`;
  }
  if (data.network !== undefined) {
    $('stat-net').textContent = data.network;
  }
}

// Poll RAM/VRAM via HTTP API for actual live data
async function pollVitals() {
  try {
    const res = await fetch('/api/vitals');
    if (!res.ok) return;
    const data = await res.json();
    updateVitals(data);
  } catch { /* server may not expose this yet */ }
}

// ── CONNECTION STATUS ─────────────────────────────────────
function updateConnectionStatus(state) {
  connDot.className = 'status-dot';
  if (state === 'online') {
    connDot.classList.add('online');
    connLabel.textContent = 'Engine Online';
  } else if (state === 'offline') {
    connDot.classList.add('error');
    connLabel.textContent = 'Offline';
  } else {
    connLabel.textContent = 'Connecting...';
  }
}

// ── UI INTERACTIONS ───────────────────────────────────────

// Theme Toggle
btnTheme.addEventListener('click', () => {
  const isLight = document.body.classList.toggle('theme-light');
  document.body.classList.toggle('theme-dark', !isLight);
  btnTheme.innerHTML = isLight
    ? '<i class="fa-solid fa-sun"></i>'
    : '<i class="fa-solid fa-moon"></i>';
});

// Chat Mode Toggle (switch between Orb view and Chat view)
btnChatToggle.addEventListener('click', () => {
  isChatMode = !isChatMode;

  if (isChatMode) {
    viewOrb.classList.add('hidden');
    viewChat.classList.remove('hidden');
    chatInputArea.classList.remove('hidden');
    btnChatToggle.classList.add('pill-active');
    chatInput.focus();
    logToTerminal('Switched to chat mode.', 'info');
  } else {
    viewChat.classList.add('hidden');
    viewOrb.classList.remove('hidden');
    chatInputArea.classList.add('hidden');
    btnChatToggle.classList.remove('pill-active');
    logToTerminal('Switched to orb mode.', 'info');
  }
});

// Terminal Drawer
btnTerminal.addEventListener('click', () => {
  termDrawer.classList.add('open');
  termLogs.scrollTop = termLogs.scrollHeight;
});
closeTerminal.addEventListener('click', () => {
  termDrawer.classList.remove('open');
});
// Close terminal on backdrop click
termDrawer.addEventListener('click', (e) => {
  if (e.target === termDrawer) termDrawer.classList.remove('open');
});

// Health Overlay
btnHealth.addEventListener('click', () => {
  overlayHealth.classList.remove('hidden');
  pollVitals();
});
closeHealth.addEventListener('click', () => {
  overlayHealth.classList.add('hidden');
});
overlayHealth.addEventListener('click', (e) => {
  if (e.target === overlayHealth) overlayHealth.classList.add('hidden');
});

// Chat Send
btnSend.addEventListener('click', submitMessage);
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitMessage();
  }
});

// ── HELPERS ───────────────────────────────────────────────
function formatEllipsis(text, maxLen) {
  return text.length > maxLen ? text.slice(0, maxLen).trimEnd() + '…' : text;
}

// ── INIT ──────────────────────────────────────────────────
setOrbState('idle', 'OFFLINE', 'Connecting to engine...');
updateConnectionStatus('connecting');
setInterval(pollVitals, VITALS_INTERVAL_MS);
connect();
