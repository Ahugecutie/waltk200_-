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
  refreshBtn2: document.getElementById("refreshBtn2"),
  clearBtn: document.getElementById("clearBtn"),
  kospiValue: document.getElementById("kospiValue"),
  kospiSub: document.getElementById("kospiSub"),
  kosdaqValue: document.getElementById("kosdaqValue"),
  kosdaqSub: document.getElementById("kosdaqSub"),
  themesEmpty: document.getElementById("themesEmpty"),
  themesList: document.getElementById("themesList"),
  stocksTbody: document.getElementById("stocksTbody"),
  modal: document.getElementById("modal"),
  mTitle: document.getElementById("mTitle"),
  mSub: document.getElementById("mSub"),
  mPills: document.getElementById("mPills"),
  mLink: document.getElementById("mLink"),
  mAi: document.getElementById("mAi"),
  mClose: document.getElementById("mClose"),
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

function fmtNum(n) {
  if (n === null || n === undefined) return "-";
  try { return Number(n).toLocaleString("ko-KR"); } catch { return String(n); }
}

function fmtPct(n) {
  if (n === null || n === undefined) return "-";
  const v = Number(n);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function setIndexBox(prefix, q) {
  if (!q) {
    els[`${prefix}Value`].textContent = "-";
    els[`${prefix}Sub`].textContent = "-";
    return;
  }
  els[`${prefix}Value`].textContent = fmtNum(q.value);
  const ch = Number(q.change || 0);
  const sign = ch > 0 ? "+" : "";
  els[`${prefix}Sub`].textContent = `${sign}${fmtNum(ch)} · ${fmtPct(q.change_pct || 0)}`;
  els[`${prefix}Sub`].classList.remove("up", "down");
  els[`${prefix}Sub`].classList.add(ch >= 0 ? "up" : "down");
}

function renderThemes(themes) {
  if (!Array.isArray(themes) || themes.length === 0) {
    els.themesEmpty.hidden = false;
    els.themesList.hidden = true;
    els.themesList.innerHTML = "";
    return;
  }
  els.themesEmpty.hidden = true;
  els.themesList.hidden = false;
  els.themesList.innerHTML = themes.slice(0, 5).map((t) => `<li>${t.name ?? t}</li>`).join("");
}

function openModal(stock) {
  els.mTitle.textContent = `${stock.name} (${stock.code})`;
  els.mSub.textContent = `${stock.market ?? ""} · 현재가 ${fmtNum(stock.price)}원 · ${fmtPct(stock.change_pct)}`;
  els.mPills.innerHTML = "";
  const pills = [
    `거래대금 ${fmtNum(stock.trade_value)}`,
    `거래량 ${fmtNum(stock.volume)}`,
    `Score ${fmtNum(stock.score)}`,
  ];
  for (const p of pills) {
    const div = document.createElement("div");
    div.className = "pill";
    div.textContent = p;
    els.mPills.appendChild(div);
  }
  els.mLink.href = stock.link || "#";
  els.mAi.textContent = "추후 기존 EXE 로직과 동일하게 연결됩니다.";
  if (typeof els.modal.showModal === "function") els.modal.showModal();
}

function renderStocks(stocks) {
  if (!Array.isArray(stocks) || stocks.length === 0) {
    els.stocksTbody.innerHTML = `<tr><td colspan="5" class="muted">데이터가 없습니다.</td></tr>`;
    return;
  }
  els.stocksTbody.innerHTML = "";
  stocks.slice(0, 20).forEach((s) => {
    const tr = document.createElement("tr");
    tr.className = "clickable";
    const pct = Number(s.change_pct || 0);
    const pctCls = pct >= 0 ? "up" : "down";
    tr.innerHTML = `
      <td>${s.name}</td>
      <td class="right">${fmtNum(s.price)}</td>
      <td class="right ${pctCls}">${fmtPct(pct)}</td>
      <td class="right">${fmtNum(s.trade_value)}</td>
      <td class="right">${fmtNum(s.score)}</td>
    `;
    tr.addEventListener("click", () => openModal(s));
    els.stocksTbody.appendChild(tr);
  });
}

function renderSnapshot(obj) {
  // Keep debug json
  els.lastPayload.textContent = JSON.stringify(obj, null, 2);
  const data = obj?.data || {};
  const indices = Array.isArray(data.indices) ? data.indices : [];
  const kospi = indices.find((x) => (x.name || "").toUpperCase() === "KOSPI");
  const kosdaq = indices.find((x) => (x.name || "").toUpperCase() === "KOSDAQ");
  setIndexBox("kospi", kospi);
  setIndexBox("kosdaq", kosdaq);
  renderThemes(data.themes);
  renderStocks(data.stocks);
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
    renderSnapshot(obj);
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

els.refreshBtn2?.addEventListener("click", () => {
  triggerRefresh();
});

els.mClose?.addEventListener("click", () => {
  try { els.modal.close(); } catch {}
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

// Ask SW to activate new versions immediately (helps PWA update).
if (navigator.serviceWorker && navigator.serviceWorker.controller) {
  try {
    navigator.serviceWorker.controller.postMessage({ type: "SKIP_WAITING" });
  } catch {}
}


