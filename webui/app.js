/* ============================================================
   SYNTHETA OMEGA V3 — Frontend JavaScript
   Handles: WebSocket, WebGL Orb, Chat, Terminal, Health, Network Matrix
   ============================================================ */

// ── CONSTANTS ────────────────────────────────────────────
const urlParams = new URLSearchParams(window.location.search);
const activeSatId = urlParams.get('sat_id') || '0'; 
const API_PORT = 8001; 

const hostname = window.location.hostname || 'localhost';
const WS_URL = `ws://${hostname}:${API_PORT}/ws/sat_${activeSatId}`;
const VITALS_INTERVAL_MS = 5000;

// ── STATE ────────────────────────────────────────────────
let ws = null;
let isChatMode = false;
let currentOrbState = 'idle';
let reconnectTimer = null;

// ── DOM ──────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const orb           = $('syntheta-orb');
const glcanvas      = $('glcanvas');
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
const btnNetwork    = $('btn-network');
const connDot       = $('conn-dot');
const connLabel     = $('conn-label');
const termDrawer    = $('terminal-drawer');
const termLogs      = $('terminal-logs');
const closeTerminal = $('close-terminal');
const overlayHealth = $('overlay-health');
const closeHealth   = $('close-health');
const netDrawer     = $('network-drawer');
const closeNet      = $('close-network');
const netCanvas     = $('networkCanvas');
const netTitle      = $('network-title');

// ── WEBSOCKET ────────────────────────────────────────────
function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  updateConnectionStatus('connecting');
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    clearTimeout(reconnectTimer);
    updateConnectionStatus('online');
    logToTerminal(`WebSocket connected to Syntheta Engine (Sat ${activeSatId}).`, 'success');
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
    case 'stt_transcription':
      appendChatMessage(data.content, 'user');
      break;

    case 'engine_state':
      handleEngineState(data.state);
      break;

    case 'syntheta_response':
      appendChatMessage(data.content, 'system');
      setOrbState('speaking', 'SPEAKING', formatEllipsis(data.content, 60));
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
      
    case 'profile_loaded':
      if (window.networkVisualizer) {
          // 🟢 FIX: Extract the payload from the content wrapper
          const payload = data.content; 
          netTitle.textContent = `${payload.user.toUpperCase()} MATRIX`;
          btnNetwork.classList.remove('glow-once');
          void btnNetwork.offsetWidth; // trigger reflow to restart animation
          btnNetwork.classList.add('glow-once');
          window.networkVisualizer.loadProfile(payload.user, payload.data);
          
          // 🔵 AUTO-OPEN: Show the network drawer immediately on load
          netDrawer.classList.add('open');
          window.networkVisualizer.start();
      }
      break;

    default:
      console.log('Unknown event type:', data.type);
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

// ── ORB STATE ────────────────────────────────────────────
function setOrbState(state, label, sublabel = '') {
  currentOrbState = state;
  orb.className = `orb state-${state}`;
  orbLabel.textContent = label;
  orbSublabel.textContent = sublabel;
  
  // Directly control the WebGL multiplier instead of CSS vibration
  if (state === 'speaking' || state === 'processing' || state === 'web_search') {
      targetSpeedMultiplier = 3.5;
  } else {
      targetSpeedMultiplier = 1.0;
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

  ws.send(JSON.stringify({ 
      type: 'user_input', 
      sat_id: activeSatId,
      content: text 
  }));
}

// ── TERMINAL ─────────────────────────────────────────────
function logToTerminal(message, type = 'info') {
  const now = new Date().toLocaleTimeString('en-GB', { hour12: false });
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  line.textContent = `${now}  ${message}`;
  termLogs.appendChild(line);
  termLogs.scrollTop = termLogs.scrollHeight;

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

async function pollVitals() {
  try {
    const res = await fetch(`http://${hostname}:${API_PORT}/api/vitals`);
    if (!res.ok) return;
    const data = await res.json();
    updateVitals(data);
  } catch { }
}

// ── CONNECTION STATUS ─────────────────────────────────────
function updateConnectionStatus(state) {
  connDot.className = 'status-dot';
  if (state === 'online') {
    connDot.classList.add('online');
    connLabel.textContent = `Online (Sat ${activeSatId})`;
  } else if (state === 'offline') {
    connDot.classList.add('error');
    connLabel.textContent = 'Offline';
  } else {
    connLabel.textContent = 'Connecting...';
  }
}

// ── UI INTERACTIONS ───────────────────────────────────────
btnTheme.addEventListener('click', () => {
  const isLight = document.body.classList.toggle('theme-light');
  document.body.classList.toggle('theme-dark', !isLight);
  btnTheme.innerHTML = isLight
    ? '<i class="fa-solid fa-sun"></i>'
    : '<i class="fa-solid fa-moon"></i>';
});

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

btnTerminal.addEventListener('click', () => {
  termDrawer.classList.add('open');
  termLogs.scrollTop = termLogs.scrollHeight;
});
closeTerminal.addEventListener('click', () => { termDrawer.classList.remove('open'); });
termDrawer.addEventListener('click', (e) => { if (e.target === termDrawer) termDrawer.classList.remove('open'); });

btnHealth.addEventListener('click', () => {
  overlayHealth.classList.remove('hidden');
  pollVitals();
});
closeHealth.addEventListener('click', () => { overlayHealth.classList.add('hidden'); });
overlayHealth.addEventListener('click', (e) => { if (e.target === overlayHealth) overlayHealth.classList.add('hidden'); });

btnSend.addEventListener('click', submitMessage);
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitMessage();
  }
});

function formatEllipsis(text, maxLen) {
  return text.length > maxLen ? text.slice(0, maxLen).trimEnd() + '…' : text;
}


// ── WEBGL ORB LOGIC (OCEAN WAVES) ────────────────────────
const gl = glcanvas ? glcanvas.getContext('webgl', { alpha: true, premultipliedAlpha: false }) : null;

let targetSpeedMultiplier = 1.0;
let baseTime = 0;
let previousTime = 0;
let currentThinkingMult = 1.0;
let programInfo;
let positionBuffer;
let shaderProgram;

if (gl) {
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const vsSource = `
        attribute vec4 aVertexPosition;
        void main() { gl_Position = aVertexPosition; }
    `;

    const fsSource = `
        precision highp float;
        uniform vec2 u_resolution;
        uniform float u_time;
        uniform float u_thinking;

        vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
        vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

        float snoise(vec3 v) {
            const vec2  C = vec2(1.0/6.0, 1.0/3.0) ;
            const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);
            vec3 i  = floor(v + dot(v, C.yyy) );
            vec3 x0 = v - i + dot(i, C.xxx) ;
            vec3 g = step(x0.yzx, x0.xyz);
            vec3 l = 1.0 - g;
            vec3 i1 = min( g.xyz, l.zxy );
            vec3 i2 = max( g.xyz, l.zxy );
            vec3 x1 = x0 - i1 + C.xxx;
            vec3 x2 = x0 - i2 + C.yyy;
            vec3 x3 = x0 - D.yyy;
            i = mod289(i);
            vec4 p = permute( permute( permute( i.z + vec4(0.0, i1.z, i2.z, 1.0 )) + i.y + vec4(0.0, i1.y, i2.y, 1.0 )) + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
            float n_ = 0.142857142857;
            vec3  ns = n_ * D.wyz - D.xzx;
            vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
            vec4 x_ = floor(j * ns.z);
            vec4 y_ = floor(j - 7.0 * x_ );
            vec4 x = x_ *ns.x + ns.yyyy;
            vec4 y = y_ *ns.x + ns.yyyy;
            vec4 h = 1.0 - abs(x) - abs(y);
            vec4 b0 = vec4( x.xy, y.xy );
            vec4 b1 = vec4( x.zw, y.zw );
            vec4 s0 = floor(b0)*2.0 + 1.0;
            vec4 s1 = floor(b1)*2.0 + 1.0;
            vec4 sh = -step(h, vec4(0.0));
            vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
            vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;
            vec3 p0 = vec3(a0.xy,h.x);
            vec3 p1 = vec3(a0.zw,h.y);
            vec3 p2 = vec3(a1.xy,h.z);
            vec3 p3 = vec3(a1.zw,h.w);
            vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
            p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
            vec4 m = max(0.5 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
            m = m * m;
            return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3) ) );
        }

        void main() {
            vec2 center = 0.5 * u_resolution.xy;
            float minRes = min(u_resolution.x, u_resolution.y);
            float dist = length(gl_FragCoord.xy - center) / minRes;
            
            float pixel = 1.0 / minRes;
            // "Thin and smooth" edges: 1.0 * pixel for a sharper but anti-aliased cutoff
            float alpha = 1.0 - smoothstep(0.5 - 1.0 * pixel, 0.5, dist);
            
            if (alpha <= 0.0) { gl_FragColor = vec4(0.0); return; }

            // Aspect-corrected coordinates for noise (st) and provided uv mapping
            vec2 st = (gl_FragCoord.xy - center) / minRes;
            vec2 uv = gl_FragCoord.xy / u_resolution.xy; 

            float t = u_time * 0.2; 
            float breath = sin(u_time * 1.5) * 0.5 + 0.5; 
            float speedMix = mix(0.5, 1.8, breath) * u_thinking;

            // Wind direction (from provided script)
            float angle = snoise(vec3(0.0, 0.0, t * 0.5)) * 6.28318; 
            vec2 windDir = vec2(cos(angle), sin(angle));
            vec2 windScroll = windDir * t * speedMix * 0.75; 

            // Edge bounce logic
            float edge = smoothstep(0.3, 0.5, dist);
            vec2 centerDir = normalize(vec2(0.5) - uv);
            vec2 randBounce = vec2(
                snoise(vec3(uv * 5.0, t * 4.0)), 
                snoise(vec3(uv * 5.0 + 10.0, t * 4.0))
            );
            vec2 bounce = mix(centerDir, randBounce, 0.7) * edge * 0.4 * u_thinking;

            // Fluid Coordinate Projection
            vec3 p = vec3((uv * 2.0) - windScroll - bounce, t * speedMix);
            vec2 distNoise = vec2(snoise(p), snoise(p + vec3(12.3, 4.5, 0.0)));
            distNoise += 0.4 * vec2(
                snoise(p * 2.2 + vec3(0.0, 0.0, t * 1.5)), 
                snoise(p * 2.2 + vec3(5.1, 2.2, t * 1.5))
            );

            vec2 waveSt = uv + distNoise * 0.2; 
            float diag = waveSt.x * 0.4 + waveSt.y * 0.6;

            // --- FLUID ORB COLOR PALETTE ---
            vec3 blue  = vec3(0.12, 0.52, 1.00);
            vec3 cyan  = vec3(0.40, 0.85, 1.00);
            vec3 white = mix(vec3(0.85, 0.95, 1.0), vec3(1.0), breath);
            vec3 cream = vec3(0.99, 0.98, 0.94);

            vec3 color = mix(blue, cyan, smoothstep(0.1, 0.6, diag));
            color = mix(color, white, smoothstep(0.4, 0.9, diag));

            float cloud = snoise(vec3(uv * 3.0 + distNoise - bounce, t * 2.0)) * 0.5 + 0.5;
            float cloudMask = smoothstep(0.8, 0.1, diag);
            color = mix(color, white, cloud * 0.5 * cloudMask);

            float creamMask = smoothstep(0.6, 0.8, diag) * smoothstep(1.0, 0.8, diag);
            color = mix(color, cream, creamMask * cloud * 0.6);

            // --- REFINED THIN EDGE GLOW ---
            float edgeGlow = smoothstep(0.46, 0.495, dist) * smoothstep(0.51, 0.49, dist);
            color += vec3(0.6, 0.9, 1.0) * edgeGlow * (0.35 + breath * 0.15);
            
            float dither = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
            color += (dither - 0.5) * 0.012;

            gl_FragColor = vec4(color * alpha, alpha);
        }
    `;

    function loadShader(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        return shader;
    }

    shaderProgram = gl.createProgram();
    gl.attachShader(shaderProgram, loadShader(gl, gl.VERTEX_SHADER, vsSource));
    gl.attachShader(shaderProgram, loadShader(gl, gl.FRAGMENT_SHADER, fsSource));
    gl.linkProgram(shaderProgram);

    programInfo = {
        attribLocations: { vertexPosition: gl.getAttribLocation(shaderProgram, 'aVertexPosition') },
        uniformLocations: {
            resolution: gl.getUniformLocation(shaderProgram, 'u_resolution'),
            time: gl.getUniformLocation(shaderProgram, 'u_time'),
            thinking: gl.getUniformLocation(shaderProgram, 'u_thinking')
        },
    };

    positionBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([1, 1, -1, 1, 1, -1, -1, -1]), gl.STATIC_DRAW);

    function resizeGlCanvas() {
        const dpr = window.devicePixelRatio || 1;
        const displayWidth  = glcanvas.clientWidth;
        const displayHeight = glcanvas.clientHeight;
        if (glcanvas.width !== displayWidth * dpr || glcanvas.height !== displayHeight * dpr) {
            glcanvas.width  = displayWidth * dpr;
            glcanvas.height = displayHeight * dpr;
        }
    }

    function renderGl(now) {
        resizeGlCanvas();
        if (previousTime === 0) previousTime = now;
        const deltaTime = (now - previousTime) * 0.001;
        previousTime = now;

        currentThinkingMult += (targetSpeedMultiplier - currentThinkingMult) * 0.05;
        baseTime += deltaTime;

        gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
        gl.clearColor(0.0, 0.0, 0.0, 0.0); 
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(shaderProgram);

        gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
        gl.vertexAttribPointer(programInfo.attribLocations.vertexPosition, 2, gl.FLOAT, false, 0, 0);
        gl.enableVertexAttribArray(programInfo.attribLocations.vertexPosition);

        gl.uniform2f(programInfo.uniformLocations.resolution, gl.canvas.width, gl.canvas.height);
        gl.uniform1f(programInfo.uniformLocations.time, baseTime);
        gl.uniform1f(programInfo.uniformLocations.thinking, currentThinkingMult);

        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        requestAnimationFrame(renderGl);
    }
    requestAnimationFrame(renderGl);
}


// ── LIVING NETWORK D3 ADAPTER ──────────────────────────────
class LivingNetwork {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.nodes = [];
        this.links = [];
        this.view = { offsetX: 0, offsetY: 0, scale: 1.0 };
        this.animationId = null;
        this.isRunning = false;
        this.selectedNode = null;
        this.selectedNodeDisplay = document.getElementById('selected-node-display');
        this.nodeDataOverlay = document.getElementById('net-node-data');
        this.nodeDataContent = document.getElementById('node-data-content');
        
        this.simulation = d3.forceSimulation(this.nodes)
            .force('link', d3.forceLink(this.links).id(d => d.id).distance(120).strength(0.15))
            .force('charge', d3.forceManyBody().strength(-300).theta(0.9))
            .force('center', d3.forceCenter(0, 0))
            .force('collision', d3.forceCollide().radius(d => d.size + 16).strength(0.8))
            .alphaDecay(0.006)
            .velocityDecay(0.15)
            .alphaTarget(0.1);

        this.simulation.force('breathing', (alpha) => {
            const time = performance.now() * 0.001;
            const globalBreath = Math.sin(time * 1.5);
            for (let node of this.nodes) {
                if (node.fx !== undefined || node.fy !== undefined) continue;
                const dist = Math.hypot(node.x, node.y) || 1;
                const dirX = node.x / dist;
                const dirY = node.y / dist;
                const localPhase = (node.x + node.y) * 0.005;
                const localBreath = Math.sin(time * 1.5 + localPhase);
                const strength = (globalBreath * 0.6 + localBreath * 0.4) * 0.25 * alpha;
                node.vx += dirX * strength;
                node.vy += dirY * strength;
            }
        });

        this.bindEvents();
    }

    drawBackground() {
        const w = this.canvas.width;
        const h = this.canvas.height;
        this.ctx.fillStyle = '#0a0f16';
        this.ctx.fillRect(0, 0, w, h);

        // Grid Lines
        this.ctx.strokeStyle = '#2affb6';
        this.ctx.lineWidth = 0.6;
        this.ctx.globalAlpha = 0.08;
        const step = 40;
        
        // Vertical lines with scroll offset
        const offX = this.view.offsetX % step;
        for (let i = offX; i < w; i += step) {
            this.ctx.beginPath();
            this.ctx.moveTo(i, 0);
            this.ctx.lineTo(i, h);
            this.ctx.stroke();
        }
        
        this.ctx.strokeStyle = '#ff71ce';
        const offY = this.view.offsetY % step;
        for (let i = offY; i < h; i += step) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, i);
            this.ctx.lineTo(w, i);
            this.ctx.stroke();
        }
        this.ctx.globalAlpha = 1.0;
    }

    getDescendantIds(rootNode) {
        if (!rootNode) return new Set();
        const ids = new Set([rootNode.id]);
        const stack = [rootNode];
        while (stack.length) {
            const current = stack.pop();
            this.links.forEach(link => {
                const s = link.source;
                const t = link.target;
                if (s.id === current.id && !ids.has(t.id)) {
                    ids.add(t.id);
                    stack.push(t);
                }
            });
        }
        return ids;
    }

    loadProfile(username, memoryGraph) {
        this.nodes.length = 0;
        this.links.length = 0;
        
        const root = { id: username, label: username, type: 'main_dir', size: 32, color1: '#f000ff', color2: '#b000c0', x: 0, y: 0, birthTime: performance.now() };
        this.nodes.push(root);

        if(memoryGraph) {
            Object.entries(memoryGraph).forEach(([bucketName, entities]) => {
                const bucketId = `B_${bucketName}`;
                const bucketNode = { id: bucketId, label: bucketName, type: 'bucket', size: 24, color1: '#ff2a6d', color2: '#d10079', x: (Math.random()-0.5)*100, y: (Math.random()-0.5)*100, birthTime: performance.now() };
                this.nodes.push(bucketNode);
                this.links.push({ source: username, target: bucketId });

                if (Array.isArray(entities)) {
                    entities.forEach(entity => {
                        const entityId = `E_${bucketName}_${entity}`;
                        const fileNode = { id: entityId, label: entity, type: 'key_value', size: 14, color1: '#05ffa1', color2: '#00c8ff', x: bucketNode.x + (Math.random()-0.5)*50, y: bucketNode.y + (Math.random()-0.5)*50, birthTime: performance.now() };
                        this.nodes.push(fileNode);
                        this.links.push({ source: bucketId, target: entityId });
                    });
                }
            });
        }

        this.simulation.nodes(this.nodes);
        this.simulation.force('link').links(this.links);
        this.simulation.alpha(1).restart();
    }

    start() {
        console.log('LivingNetwork: starting animation');
        if (this.isRunning) return;
        this.isRunning = true;
        // Small delay to allow drawer transition to provide dimensions
        setTimeout(() => this.resize(), 50);
        setTimeout(() => this.resize(), 300); // And once more after transition
        this.simulation.restart();
        this.draw();
    }

    stop() {
        console.log('LivingNetwork: stopping animation');
        this.isRunning = false;
        this.simulation.stop();
        if (this.animationId) cancelAnimationFrame(this.animationId);
    }

    resize() {
        const parent = this.canvas.parentElement;
        if (!parent) return;
        const rect = parent.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
            this.canvas.width = rect.width;
            this.canvas.height = rect.height;
            if (this.view.offsetX === 0) {
                this.view.offsetX = rect.width / 2;
                this.view.offsetY = rect.height / 2;
            }
            console.log(`LivingNetwork: Resized to ${rect.width}x${rect.height}`);
        }
    }

    draw() {
        if (!this.isRunning) return;
        
        if (this.canvas.width === 0 || this.canvas.height === 0) {
            this.resize();
        }
        
        const w = this.canvas.width;
        const h = this.canvas.height;
        const now = performance.now();
        const timeSec = now * 0.001;

        if (w === 0 || h === 0) {
            this.animationId = requestAnimationFrame(() => this.draw());
            return;
        }

        this.drawBackground();

        // Subtree highlighting
        const descendantIds = (this.selectedNode && this.selectedNode.type === 'bucket') 
            ? this.getDescendantIds(this.selectedNode) 
            : new Set();

        this.ctx.save();
        this.ctx.translate(this.view.offsetX, this.view.offsetY);
        this.ctx.scale(this.view.scale, this.view.scale);

        // ---- LINKS ----
        this.links.forEach(link => {
            const s = link.source, t = link.target;
            if (!s || !t) return;
            
            const isHighlighted = descendantIds.has(t.id);
            const dist = Math.hypot(t.x - s.x, t.y - s.y);
            const perpX = -(t.y - s.y) / (dist || 1);
            const perpY = (t.x - s.x) / (dist || 1);
            const wiggle = Math.sin(timeSec * 1.8 + (s.x + t.y) * 0.05) * 20 * (dist / 150);
            
            this.ctx.beginPath();
            this.ctx.moveTo(s.x, s.y);
            const cpX = (s.x + t.x) / 2 + perpX * wiggle;
            const cpY = (s.y + t.y) / 2 + perpY * wiggle;
            this.ctx.quadraticCurveTo(cpX, cpY, t.x, t.y);
            
            const grad = this.ctx.createLinearGradient(s.x, s.y, t.x, t.y);
            if (isHighlighted) {
                grad.addColorStop(0, '#fff9b0'); grad.addColorStop(1, '#b0ffff');
                this.ctx.shadowColor = '#fefe66';
                this.ctx.shadowBlur = 30;
                this.ctx.lineWidth = 4 / this.view.scale;
            } else {
                grad.addColorStop(0, '#0ff'); grad.addColorStop(1, '#f0f');
                this.ctx.shadowColor = '#0ff';
                this.ctx.shadowBlur = 12;
                this.ctx.lineWidth = 1.8 / this.view.scale;
            }
            
            this.ctx.strokeStyle = grad;
            this.ctx.stroke();

            // White hot core for highlighted
            if (isHighlighted) {
                this.ctx.strokeStyle = '#ffffffdd';
                this.ctx.lineWidth = 1.2 / this.view.scale;
                this.ctx.stroke();
            }
        });

        // ---- NODES ----
        this.nodes.forEach(node => {
            const age = now - node.birthTime;
            const tGrow = Math.min(1, age / 800);
            const scale = (1 - Math.pow(1 - tGrow, 2));
            const throb = 1.0 + 0.12 * Math.sin(timeSec * 3.0 + node.x * 0.05);
            const r = node.size * scale * throb;

            const grad = this.ctx.createRadialGradient(node.x-2, node.y-2, Math.max(0.1, r*0.2), node.x, node.y, Math.max(0.1, r*1.5));
            grad.addColorStop(0, node.color1); grad.addColorStop(0.8, node.color2); grad.addColorStop(1, '#0a0f16');
            
            this.ctx.shadowColor = node.color1;
            this.ctx.shadowBlur = (this.selectedNode === node) ? 35 : 18;
            this.ctx.beginPath();
            this.ctx.arc(node.x, node.y, Math.max(0, r), 0, 2 * Math.PI);
            this.ctx.fillStyle = grad;
            this.ctx.fill();

            // Selected Ring
            if (this.selectedNode === node) {
                const sPulse = 1 + 0.1 * Math.sin(now * 0.01);
                this.ctx.shadowBlur = 40;
                this.ctx.shadowColor = '#ffffb0';
                this.ctx.beginPath();
                this.ctx.arc(node.x, node.y, Math.max(0, r * sPulse + 4), 0, 2 * Math.PI);
                this.ctx.strokeStyle = '#fefe66';
                this.ctx.lineWidth = 2.5 / this.view.scale;
                this.ctx.stroke();
            }

            this.ctx.shadowBlur = 0;
            this.ctx.fillStyle = (this.selectedNode === node) ? '#fff' : 'rgba(255,255,255,0.7)';
            this.ctx.font = `${Math.max(10, 12/this.view.scale)}px "Inter", monospace`;
            this.ctx.textAlign = 'center';
            this.ctx.fillText(node.label, node.x, node.y + r + 18/this.view.scale);
        });

        this.ctx.restore();
        this.animationId = requestAnimationFrame(() => this.draw());
    }

    updateUI() {
        if (this.selectedNodeDisplay) {
            if (this.selectedNode) {
                const typeLabel = this.selectedNode.type.replace('_', ' ').toUpperCase();
                this.selectedNodeDisplay.textContent = `[${typeLabel}] ${this.selectedNode.label}`;
                
                // Update Corner Info Overlay
                if (this.nodeDataOverlay && this.nodeDataContent) {
                    this.nodeDataContent.innerHTML = `
                        <span class="node-data-type">${typeLabel}</span>
                        <div class="node-data-label">${this.selectedNode.label}</div>
                    `;
                    this.nodeDataOverlay.classList.remove('hidden');
                }
            } else {
                this.selectedNodeDisplay.textContent = '— NULL —';
                if (this.nodeDataOverlay) {
                    this.nodeDataOverlay.classList.add('hidden');
                }
            }
        }
    }

    bindEvents() {
        let isDragging = false, dragNode = null, isPanning = false, lastX, lastY;
        
        this.canvas.addEventListener('mousedown', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left, my = e.clientY - rect.top;
            const wx = (mx - this.view.offsetX) / this.view.scale;
            const wy = (my - this.view.offsetY) / this.view.scale;
            
            dragNode = this.nodes.find(n => Math.hypot(n.x - wx, n.y - wy) < (n.size * 1.5) / this.view.scale);
            
            if (dragNode) {
                this.selectedNode = dragNode;
                this.updateUI();
                isDragging = true;
                dragNode.fx = wx; dragNode.fy = wy;
                this.simulation.alpha(0.5);
            } else {
                this.selectedNode = null;
                this.updateUI();
                isPanning = true; lastX = mx; lastY = my;
            }
        });
        
        this.canvas.addEventListener('mousemove', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left, my = e.clientY - rect.top;
            if (isDragging) {
                dragNode.fx = (mx - this.view.offsetX) / this.view.scale;
                dragNode.fy = (my - this.view.offsetY) / this.view.scale;
                this.simulation.alpha(0.2);
            } else if (isPanning) {
                this.view.offsetX += mx - lastX; this.view.offsetY += my - lastY;
                lastX = mx; lastY = my;
            }
        });
        
        window.addEventListener('mouseup', () => {
            if (isDragging) { dragNode.fx = null; dragNode.fy = null; isDragging = false; dragNode = null; }
            isPanning = false;
        });

        this.canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const rect = this.canvas.getBoundingClientRect();
            const mx = e.clientX - rect.left, my = e.clientY - rect.top;
            const zoom = e.deltaY > 0 ? 0.9 : 1.1;
            const newScale = Math.max(0.2, Math.min(8, this.view.scale * zoom));
            
            const wx = (mx - this.view.offsetX) / this.view.scale;
            const wy = (my - this.view.offsetY) / this.view.scale;
            
            this.view.scale = newScale;
            this.view.offsetX = mx - wx * this.view.scale;
            this.view.offsetY = my - wy * this.view.scale;
        }, { passive: false });
    }
}

// ── UI WIRING ──────────────────────────────
window.networkVisualizer = new LivingNetwork(netCanvas);

btnNetwork.addEventListener('click', () => {
    console.log('UI: Network button clicked');
    if (!netDrawer) console.error('UI: netDrawer is NULL');
    netDrawer.classList.add('open');
    window.networkVisualizer.start();
});

closeNet.addEventListener('click', () => {
    console.log('UI: Network close clicked');
    netDrawer.classList.remove('open');
    window.networkVisualizer.stop(); 
});


// ── INIT ──────────────────────────────────────────────────
setOrbState('idle', 'OFFLINE', 'Connecting to engine...');
updateConnectionStatus('connecting');
setInterval(pollVitals, VITALS_INTERVAL_MS);
connect();