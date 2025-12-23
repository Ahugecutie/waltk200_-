/* Mobile PWA client: shows explicit offline/server-down message when PC is off. */

const OWNER_NAME = "김성훈";

const els = {
  statusBadge: document.getElementById("statusBadge"),
  statusText: document.getElementById("statusText"),
  lastPayload: document.getElementById("lastPayload"),
  serverUrl: document.getElementById("serverUrl"),
  token: document.getElementById("token"),
  saveBtn: document.getElementById("saveBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  clearBtn: document.getElementById("clearBtn"),
};

function setBadge(kind, text) {
  els.statusBadge.classList.remove("badge--ok", "badge--warn", "badge--bad");
  els.statusBadge.classList.add(kind);
  els.statusBadge.textContent = text;
}

function setStatus(text) {
  els.statusText.textContent = text;
}

function loadSettings() {
  const savedUrl = localStorage.getItem("ls_server_url") || "";
  const savedToken = localStorage.getItem("ls_token") || "";
  els.serverUrl.value = savedUrl;
  els.token.value = savedToken;
}

function saveSettings() {
  localStorage.setItem("ls_server_url", (els.serverUrl.value || "").trim());
  localStorage.setItem("ls_token", (els.token.value || "").trim());
}

function clearSettings() {
  localStorage.removeItem("ls_server_url");
  localStorage.removeItem("ls_token");
  loadSettings();
}

function normalizeBaseUrl(input) {
  const v = (input || "").trim();
  if (!v) return `${location.protocol}//${location.host}`;
  // Accept http(s)://host[:port] only; strip trailing slashes.
  return v.replace(/\/+$/, "");
}

function wsUrlFromBase(baseUrl) {
  const u = new URL(baseUrl);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/ws";
  u.search = "";
  const token = (localStorage.getItem("ls_token") || "").trim();
  if (token) u.searchParams.set("token", token);
  return u.toString();
}

function httpUrl(baseUrl, path) {
  const u = new URL(baseUrl);
  u.pathname = path;
  u.search = "";
  return u.toString();
}

let ws = null;
let retryTimer = null;
let retries = 0;
let pollTimer = null;

function stop() {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (ws) {
    try { ws.close(); } catch {}
    ws = null;
  }
}

function scheduleReconnect() {
  const base = 600; // ms
  const max = 8000;
  const delay = Math.min(max, base * Math.pow(1.8, retries));
  const jitter = Math.floor(Math.random() * 250);
  retryTimer = setTimeout(connect, delay + jitter);
}

function showServerDown() {
  setBadge("badge--bad", "연결 끊김");
  setStatus(`${OWNER_NAME}님에 컴퓨터가 꺼져있습니다`);
}

async function fetchSnapshot() {
  const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
  const token = (localStorage.getItem("ls_token") || "").trim();
  const snapUrl = httpUrl(baseUrl, "/snapshot");
  try {
    const res = await fetch(snapUrl, {
      headers: token ? { "X-App-Token": token } : undefined,
      cache: "no-store",
    });
    if (!res.ok) throw new Error(String(res.status));
    const obj = await res.json();
    els.lastPayload.textContent = JSON.stringify(obj, null, 2);
    setBadge("badge--ok", "연결됨");
    setStatus("데이터를 수신했습니다.");
    return true;
  } catch {
    showServerDown();
    return false;
  }
}

async function triggerRefresh() {
  const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
  const token = (localStorage.getItem("ls_token") || "").trim();
  const url = httpUrl(baseUrl, "/refresh");
  try {
    await fetch(url, {
      method: "POST",
      headers: token ? { "X-App-Token": token } : undefined,
    });
  } catch {}
  await fetchSnapshot();
}

function startPolling() {
  // 분단위 자동 갱신(기본 60초) + 화면/네트워크 상태에 따라 자동 재시도
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    fetchSnapshot();
  }, 60_000);
  // immediate
  fetchSnapshot();
}

function connect() {
  stop();
  const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
  const url = wsUrlFromBase(baseUrl);

  setBadge("badge--warn", "연결 시도 중…");
  setStatus("서버에 연결을 시도합니다…");

  try {
    ws = new WebSocket(url);
  } catch (e) {
    retries += 1;
    showServerDown();
    // WebSocket이 막힌 환경일 수 있으니 폴링으로 폴백
    startPolling();
    return;
  }

  ws.onopen = () => {
    retries = 0;
    setBadge("badge--ok", "연결됨");
    setStatus("실시간 데이터를 수신 중입니다.");
  };

  ws.onmessage = (ev) => {
    // Expect JSON payload; fallback to raw text.
    let text = ev.data;
    try {
      const obj = JSON.parse(ev.data);
      text = JSON.stringify(obj, null, 2);
    } catch {}
    els.lastPayload.textContent = text;
  };

  ws.onerror = () => {
    // onclose will handle message.
  };

  ws.onclose = () => {
    retries += 1;
    // For the user: immediately show the explicit down message.
    showServerDown();
    // WS가 끊기면 폴링으로 폴백(슬립/콜드스타트에도 대응)
    startPolling();
  };
}

els.saveBtn.addEventListener("click", () => {
  saveSettings();
  connect();
});

els.refreshBtn.addEventListener("click", () => {
  triggerRefresh();
});

els.clearBtn.addEventListener("click", () => {
  clearSettings();
  connect();
});

window.addEventListener("online", () => connect());
window.addEventListener("offline", () => {
  setBadge("badge--bad", "오프라인");
  setStatus("인터넷 연결이 없습니다.");
});

loadSettings();
connect();


