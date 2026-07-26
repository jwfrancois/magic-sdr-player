// Magic SDR Player — remote web UI

const $ = (id) => document.getElementById(id);

let state = {
  freq_hz: 96900000,
  modulation: "WFM_ST",
  signal_level_db: -120,
  is_recording: false,
  label: "",
  band: "FM Broadcast",
  gqrx_connected: false,
};

let audioCtx = null;
let audioWs = null;
let spectrumWs = null;
let eventsWs = null;
let currentBandFilter = "all";
let bookmarks = [];
let discovered = [];

// ----------------------------- audio -----------------------------
function enableAudio() {
  if (audioCtx) return;
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: 48000,
    });
    connectAudioWs();
    $("audio-hint").classList.add("hidden");
  } catch (e) {
    console.error("AudioContext failed:", e);
  }
}

document.addEventListener("click", enableAudio, { once: true });

function connectAudioWs() {
  if (audioWs) audioWs.close();
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  audioWs = new WebSocket(`${proto}//${location.host}/ws/audio`);
  audioWs.binaryType = "arraybuffer";
  audioWs.onmessage = (ev) => {
    if (!audioCtx) return;
    try {
      const payload = JSON.parse(ev.data);
      const bytes = atob(payload.data);
      const buf = new Int16Array(bytes.length / 2);
      for (let i = 0; i < buf.length; i++) {
        buf[i] = (bytes.charCodeAt(i * 2 + 1) << 8) | bytes.charCodeAt(i * 2);
      }
      // Convert to float
      const floats = new Float32Array(buf.length);
      for (let i = 0; i < buf.length; i++) floats[i] = buf[i] / 32768.0;
      const channels = payload.channels || 2;
      const frames = floats.length / channels;
      const audioBuf = audioCtx.createBuffer(channels, frames, payload.sample_rate);
      for (let ch = 0; ch < channels; ch++) {
        const chanData = audioBuf.getChannelData(ch);
        for (let i = 0; i < frames; i++) {
          chanData[i] = floats[i * channels + ch];
        }
      }
      const src = audioCtx.createBufferSource();
      src.buffer = audioBuf;
      src.connect(audioCtx.destination);
      src.start();
    } catch (e) {
      console.error("audio decode failed:", e);
    }
  };
  audioWs.onclose = () => {
    setTimeout(connectAudioWs, 2000);
  };
}

// ----------------------------- spectrum / waterfall -----------------------------
const canvas = $("waterfall");
const ctx = canvas.getContext("2d");
const WF_HEIGHT = canvas.height;
const WF_WIDTH = canvas.width;
let wfImgData = ctx.createImageData(WF_WIDTH, WF_HEIGHT);
let lastBins = null;

// Turbo-ish colormap (256 entries) — precomputed
const colormap = (() => {
  const map = new Uint8ClampedArray(256 * 4);
  // Simple interpolation: black → blue → cyan → green → yellow → red → white
  const stops = [
    [0,   0,   0,   0],
    [40,  0,   0,  80],
    [80,  0,  80, 200],
    [120, 0, 200, 200],
    [160, 80, 220, 80],
    [200, 220, 220, 0],
    [230, 220, 80,  0],
    [255, 255, 255, 255],
  ];
  for (let i = 0; i < 256; i++) {
    let lo = stops[0], hi = stops[stops.length - 1];
    for (let j = 0; j < stops.length - 1; j++) {
      if (i >= stops[j][0] && i <= stops[j+1][0]) { lo = stops[j]; hi = stops[j+1]; break; }
    }
    const t = (i - lo[0]) / Math.max(1, hi[0] - lo[0]);
    map[i*4]   = lo[1] + (hi[1] - lo[1]) * t;
    map[i*4+1] = lo[2] + (hi[2] - lo[2]) * t;
    map[i*4+2] = lo[3] + (hi[3] - lo[3]) * t;
    map[i*4+3] = 255;
  }
  return map;
})();

function drawSpectrumRow(bins, centerHz, spanHz) {
  if (!bins || bins.length === 0) return;
  // Shift existing rows down by 1
  const shifted = ctx.createImageData(WF_WIDTH, WF_HEIGHT);
  shifted.data.set(wfImgData.data);
  // Place a new row at the top
  const row = new Uint8ClampedArray(WF_WIDTH * 4);
  for (let x = 0; x < WF_WIDTH; x++) {
    const binIdx = Math.floor((x / WF_WIDTH) * bins.length);
    const db = bins[binIdx];
    // Map -80..-10 dBFS to 0..255
    const v = Math.max(0, Math.min(255, Math.floor((db + 80) / 70 * 255)));
    row[x*4]   = colormap[v*4];
    row[x*4+1] = colormap[v*4+1];
    row[x*4+2] = colormap[v*4+2];
    row[x*4+3] = 255;
  }
  // Compose: top row = new, below = shifted
  wfImgData.data.set(row, 0);
  for (let y = 1; y < WF_HEIGHT; y++) {
    for (let x = 0; x < WF_WIDTH * 4; x++) {
      wfImgData.data[y * WF_WIDTH * 4 + x] = shifted.data[(y - 1) * WF_WIDTH * 4 + x];
    }
  }
  ctx.putImageData(wfImgData, 0, 0);

  // Draw tuned-frequency marker line
  const fStart = centerHz - spanHz / 2;
  const fEnd = centerHz + spanHz / 2;
  const tuneX = Math.floor((state.freq_hz - fStart) / (fEnd - fStart) * WF_WIDTH);
  if (tuneX >= 0 && tuneX < WF_WIDTH) {
    ctx.strokeStyle = "rgba(255, 92, 138, 0.9)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(tuneX, 0); ctx.lineTo(tuneX, WF_HEIGHT); ctx.stroke();
  }
  lastBins = { bins, centerHz, spanHz };
}

function connectSpectrumWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  spectrumWs = new WebSocket(`${proto}//${location.host}/ws/spectrum`);
  spectrumWs.onmessage = (ev) => {
    try {
      const payload = JSON.parse(ev.data);
      drawSpectrumRow(payload.bins, payload.center_hz, payload.span_hz);
      $("spectrum-hint").textContent = `Center: ${(payload.center_hz/1e6).toFixed(3)} MHz · Span: ${(payload.span_hz/1e6).toFixed(2)} MHz`;
    } catch (e) { /* ignore */ }
  };
  spectrumWs.onclose = () => setTimeout(connectSpectrumWs, 2000);
}

// click-to-tune on waterfall
canvas.addEventListener("click", (e) => {
  if (!lastBins) return;
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const fStart = lastBins.centerHz - lastBins.spanHz / 2;
  const fEnd = lastBins.centerHz + lastBins.spanHz / 2;
  const freq = Math.round(fStart + x * (fEnd - fStart));
  tuneTo(freq);
});

// ----------------------------- state -----------------------------
function connectEventsWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  eventsWs = new WebSocket(`${proto}//${location.host}/ws/events`);
  eventsWs.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") updateState(msg.state);
      else if (msg.type === "volume") $("volume").value = Math.round(msg.value * 100);
      else if (msg.type === "mute") $("mute-btn").textContent = msg.value ? "🔇" : "🔊";
    } catch (e) { /* ignore */ }
  };
  eventsWs.onclose = () => setTimeout(connectEventsWs, 2000);
}

async function pollState() {
  try {
    const r = await fetch("/api/state");
    const s = await r.json();
    updateState(s);
  } catch (e) { /* ignore */ }
}

function updateState(s) {
  state = { ...state, ...s };
  $("freq-value").textContent = (s.freq_hz / 1e6).toFixed(6);
  $("freq-label").textContent = `${s.band || "Custom"} · ${s.modulation || ""}${s.label ? " · " + s.label : ""}`;
  $("modulation").value = s.modulation || "WFM_ST";
  if (s.signal_level_db !== undefined) {
    const lvl = s.signal_level_db;
    $("signal-db").textContent = `${lvl.toFixed(1)} dB`;
    const pct = Math.max(0, Math.min(100, (lvl + 100) / 100 * 100));
    $("signal-fill").style.width = `${pct}%`;
  }
  if (s.is_recording !== undefined) {
    $("record-btn").classList.toggle("recording", s.is_recording);
    $("record-btn").textContent = s.is_recording ? "■ Stop" : "● Rec";
  }
  $("conn-status").className = "dot " + (s.gqrx_connected ? "dot-on" : "dot-off");
  $("conn-text").textContent = s.gqrx_connected ? "Connected to Gqrx" : "Gqrx offline";
}

// ----------------------------- tuning -----------------------------
async function tuneTo(freqHz, modulation) {
  await fetch("/api/tune", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ freq_hz: freqHz, modulation }),
  });
  pollState();
}

document.querySelectorAll(".tune-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const step = parseInt(btn.dataset.step, 10);
    tuneTo(state.freq_hz + step);
  });
});

$("freq-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const mhz = parseFloat($("freq-input").value);
    if (!isNaN(mhz)) tuneTo(Math.round(mhz * 1e6));
  }
});

$("modulation").addEventListener("change", (e) => {
  fetch("/api/modulation", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modulation: e.target.value }),
  }).then(pollState);
});

$("volume").addEventListener("input", (e) => {
  fetch("/api/volume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume: parseInt(e.target.value, 10) / 100 }),
  });
});

$("mute-btn").addEventListener("click", () => {
  const muted = $("mute-btn").textContent === "🔊";
  $("mute-btn").textContent = muted ? "🔇" : "🔊";
  fetch("/api/mute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ muted }),
  });
});

$("record-btn").addEventListener("click", () => {
  const action = state.is_recording ? "stop" : "start";
  fetch("/api/record", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  }).then(pollState);
});

// ----------------------------- bookmarks -----------------------------
async function loadBookmarks() {
  const r = await fetch("/api/bookmarks");
  bookmarks = await r.json();
  renderBookmarks();
}

function renderBookmarks() {
  const list = $("bookmark-list");
  const q = $("bookmark-search").value.toLowerCase();
  const filtered = bookmarks.filter((b) => {
    if (currentBandFilter !== "all" && b.band !== currentBandFilter) return false;
    if (q && !(
      b.label.toLowerCase().includes(q) ||
      b.band.toLowerCase().includes(q) ||
      (b.ai_tag || "").toLowerCase().includes(q)
    )) return false;
    return true;
  });
  list.innerHTML = filtered.map((b) => `
    <li data-freq="${b.freq_hz}">
      <div class="bm-freq">${(b.freq_hz/1e6).toFixed(4)} MHz</div>
      <div class="bm-label">${b.label || "Unknown"}</div>
      <div class="bm-meta">${b.band} · ${b.modulation}</div>
      ${b.ai_tag ? `<div class="bm-ai-tag">AI: ${b.ai_tag}</div>` : ""}
    </li>
  `).join("");
  list.querySelectorAll("li").forEach((li) => {
    li.addEventListener("click", () => {
      const f = parseInt(li.dataset.freq, 10);
      tuneTo(f);
    });
  });
}

document.querySelectorAll(".band-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".band-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentBandFilter = tab.dataset.band;
    renderBookmarks();
  });
});

$("bookmark-search").addEventListener("input", renderBookmarks);

// ----------------------------- scanner -----------------------------
document.querySelectorAll(".scan-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const band = btn.dataset.band;
    $("scan-status").textContent = `Scanning ${band}…`;
    $("scan-stop-btn").disabled = false;
    await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ band }),
    });
  });
});

$("scan-stop-btn").addEventListener("click", async () => {
  await fetch("/api/scan/stop", { method: "POST" });
  $("scan-status").textContent = "Stopped";
  $("scan-stop-btn").disabled = true;
});

// ----------------------------- recordings -----------------------------
async function loadRecordings() {
  const r = await fetch("/api/recordings");
  const recs = await r.json();
  const list = $("recording-list");
  list.innerHTML = recs.slice(0, 20).map((r) => `
    <li>
      <div class="bm-label">${r.label || "Unknown"}</div>
      <div class="bm-meta">${(r.freq_mhz || 0).toFixed(4)} MHz · ${r.modulation} · ${new Date(r.start_ts * 1000).toLocaleString()}</div>
      <div class="bm-meta">${(r.duration_s || 0).toFixed(1)}s · peak ${r.peak_level_db?.toFixed(1) || "?"} dB</div>
      ${r.ai_tag ? `<div class="bm-ai-tag">AI: ${r.ai_tag}</div>` : ""}
      <a class="bm-meta" href="/api/recordings/file?path=${encodeURIComponent(r.path)}" target="_blank">Download WAV</a>
    </li>
  `).join("");
}

$("refresh-recordings").addEventListener("click", loadRecordings);

// ----------------------------- init -----------------------------
function init() {
  connectEventsWs();
  connectSpectrumWs();
  pollState();
  setInterval(pollState, 1000);
  loadBookmarks();
  loadRecordings();
}

init();
