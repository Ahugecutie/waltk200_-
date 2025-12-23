/* Mobile PWA client: shows explicit offline/server-down message when PC is off. */

const OWNER_NAME = "김성훈";

// Initialize els after DOM is ready
let els = {};

function initElements() {
  els = {
    statusBadge: document.getElementById("statusBadge"),
    statusText: document.getElementById("statusText"),
    lastPayload: document.getElementById("lastPayload"),
    serverUrl: document.getElementById("serverUrl"),
    token: document.getElementById("token"),
    saveBtn: document.getElementById("saveBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    refreshBtn2: document.getElementById("refreshBtn2"),
    clearBtn: document.getElementById("clearBtn"),
    settingsBtn: document.getElementById("settingsBtn"),
    serverConfigCard: document.getElementById("serverConfigCard"),
    serverConfigToggle: document.getElementById("serverConfigToggle"),
    serverConfigContent: document.getElementById("serverConfigContent"),
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
    mSignals: document.getElementById("mSignals"),
    mLink: document.getElementById("mLink"),
  mAi: document.getElementById("mAi"),
  mClose: document.getElementById("mClose"),
  mPivotSection: document.getElementById("mPivotSection"),
  mPivot: document.getElementById("mPivot"),
  mNewsSection: document.getElementById("mNewsSection"),
  mNews: document.getElementById("mNews"),
  mFinancialSection: document.getElementById("mFinancialSection"),
  mFinancial: document.getElementById("mFinancial"),
  mInvestorSection: document.getElementById("mInvestorSection"),
  mInvestor: document.getElementById("mInvestor"),
  };
  
  // Verify critical elements exist
  if (!els.statusBadge || !els.statusText || !els.stocksTbody) {
    console.error("Critical DOM elements not found!");
    return false;
  }
  return true;
}

function setBadge(kind, text) {
  if (!els.statusBadge) return;
  els.statusBadge.classList.remove("badge--ok", "badge--warn", "badge--bad");
  els.statusBadge.classList.add(kind);
  els.statusBadge.textContent = text;
}

function setStatus(text) {
  if (!els.statusText) return;
  els.statusText.textContent = text;
}

function loadSettings() {
  if (!els.serverUrl || !els.token) return;
  const savedUrl = localStorage.getItem("ls_server_url") || "";
  const savedToken = localStorage.getItem("ls_token") || "";
  els.serverUrl.value = savedUrl;
  els.token.value = savedToken;
}

function saveSettings() {
  if (!els.serverUrl || !els.token) return;
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
  if (!v) {
    // Default to current host if no URL is set
    return `${location.protocol}//${location.host}`;
  }
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
let currentStocks = []; // Store current stocks array for modal access

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

async function openModal(stock) {
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
  // signals
  els.mSignals.innerHTML = "";
  const sigs = Array.isArray(stock.signals) ? stock.signals : [];
  for (const s of sigs) {
    const tone = s.tone === "bad" ? "sig--bad" : s.tone === "warn" ? "sig--warn" : s.tone === "ok" ? "sig--ok" : "";
    const div = document.createElement("div");
    div.className = `sig ${tone}`;
    div.innerHTML = `
      <div class="sig__left">
        <div class="sig__title">${s.title ?? ""}</div>
        <div class="sig__desc">${s.desc ?? ""}</div>
      </div>
    `;
    els.mSignals.appendChild(div);
  }

  // Ensure ai_opinion is properly displayed
  const aiText = stock?.ai_opinion || "";
  els.mAi.textContent = aiText || "추후 기존 EXE 로직과 동일하게 연결됩니다.";
  
  // Hide detail sections initially
  if (els.mPivotSection) els.mPivotSection.style.display = "none";
  if (els.mNewsSection) els.mNewsSection.style.display = "none";
  if (els.mFinancialSection) els.mFinancialSection.style.display = "none";
  if (els.mInvestorSection) els.mInvestorSection.style.display = "none";
  
  // Show modal first
  if (typeof els.modal.showModal === "function") els.modal.showModal();
  
  // Fetch detailed information
  try {
    const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
    const token = (localStorage.getItem("ls_token") || "").trim();
    const detailUrl = httpUrl(baseUrl, `/stock/${stock.code}`);
    
    const res = await fetch(detailUrl, {
      headers: token ? { "X-App-Token": token } : undefined,
      cache: "no-store",
    });
    
    if (res.ok) {
      const result = await res.json();
      if (result.ok && result.data) {
        renderStockDetail(result.data);
      }
    }
  } catch (err) {
    console.error("Failed to fetch stock detail:", err);
    // Show error message in AI opinion section
    if (els.mAi) {
      els.mAi.textContent = "상세 정보를 불러오는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
    }
  }
}

function renderStockDetail(detail) {
  
  // Update AI opinion with enhanced detail (if available)
  if (detail.ai_opinion && els.mAi) {
    els.mAi.textContent = detail.ai_opinion;
  } else if (els.mAi) {
    els.mAi.textContent = "분석 중...";
  }
  
  // Pivot points
  if (detail.pivot && detail.pivot.pivot) {
    if (els.mPivot && els.mPivotSection) {
      els.mPivot.innerHTML = `
        <div class="pivotItem pivotItem--resistance">2차 저항: ${fmtNum(detail.pivot.r2)}</div>
        <div class="pivotItem pivotItem--resistance">1차 저항: ${fmtNum(detail.pivot.r1)}</div>
        <div class="pivotItem pivotItem--pivot">Pivot: ${fmtNum(detail.pivot.pivot)}</div>
        <div class="pivotItem pivotItem--support">1차 지지: ${fmtNum(detail.pivot.s1)}</div>
        <div class="pivotItem pivotItem--support">2차 지지: ${fmtNum(detail.pivot.s2)}</div>
      `;
      els.mPivotSection.style.display = "block";
    }
  } else if (els.mPivotSection) {
    els.mPivotSection.style.display = "none";
  }
  
  // News
  if (detail.news && Array.isArray(detail.news) && detail.news.length > 0) {
    if (els.mNews && els.mNewsSection) {
      els.mNews.innerHTML = detail.news.map(n => {
        const title = (n.title || n.name || "").trim();
        const url = n.url || n.link || "#";
        const date = n.date || n.time || "";
        return `
          <div class="newsItem">
            <a href="${url}" target="_blank" rel="noreferrer" class="newsLink">${title}</a>
            ${date ? `<span class="newsDate">${date}</span>` : ""}
          </div>
        `;
      }).join("");
      els.mNewsSection.style.display = "block";
    }
  } else if (els.mNewsSection) {
    els.mNewsSection.style.display = "none";
  }
  
  // Financials
  if (detail.financials && Array.isArray(detail.financials) && detail.financials.length > 0) {
    if (els.mFinancial && els.mFinancialSection) {
      els.mFinancial.innerHTML = `
        <table class="detailTable">
          <thead>
            <tr>
              <th>구분</th>
              <th class="right">매출액</th>
              <th class="right">영업이익</th>
            </tr>
          </thead>
          <tbody>
            ${detail.financials.map(f => {
              const period = f.period || f.date || "Recent";
              const sales = f.sales || f.revenue || 0;
              const profit = f.operating_profit || f.profit || 0;
              return `
                <tr>
                  <td>${period}</td>
                  <td class="right">${fmtNum(sales)}</td>
                  <td class="right">${fmtNum(profit)}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
        <div class="hint">* 단위: 억원 (네이버 금융 기준)</div>
      `;
      els.mFinancialSection.style.display = "block";
    }
  } else if (els.mFinancialSection) {
    els.mFinancialSection.style.display = "none";
  }
  
  // Investor trends
  if (detail.investor_trends && Array.isArray(detail.investor_trends) && detail.investor_trends.length > 0) {
    if (els.mInvestor && els.mInvestorSection) {
      els.mInvestor.innerHTML = `
        <table class="detailTable">
          <thead>
            <tr>
              <th>날짜</th>
              <th class="right">기관</th>
              <th class="right">외국인</th>
            </tr>
          </thead>
          <tbody>
            ${detail.investor_trends.map(t => {
              const date = t.date || t.time || "";
              const institution = t.institution || t.inst || 0;
              const foreigner = t.foreigner || t.foreign || 0;
              return `
                <tr>
                  <td>${date}</td>
                  <td class="right">${fmtNum(institution)}</td>
                  <td class="right">${fmtNum(foreigner)}</td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
        <div class="hint">* 단위: 천주 (순매수 기준)</div>
      `;
      els.mInvestorSection.style.display = "block";
    }
  } else if (els.mInvestorSection) {
    els.mInvestorSection.style.display = "none";
  }
}

function renderStocks(stocks) {
  if (!Array.isArray(stocks) || stocks.length === 0) {
    els.stocksTbody.innerHTML = `<tr><td colspan="5" class="muted">데이터가 없습니다.</td></tr>`;
    currentStocks = [];
    return;
  }
  // Store stocks array for modal access
  currentStocks = stocks.slice(0, 20);
  els.stocksTbody.innerHTML = "";
  currentStocks.forEach((s, idx) => {
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
    tr.addEventListener("click", () => openModal(currentStocks[idx]));
    els.stocksTbody.appendChild(tr);
  });
}

function renderSnapshot(obj) {
  try {
    // Keep debug json
    els.lastPayload.textContent = JSON.stringify(obj, null, 2);
    const data = obj?.data || {};
    
    // Handle empty or error data
    if (obj.type === "empty" || data.error) {
      if (data.error) {
        setStatus(`데이터 수집 중 오류: ${data.error}`);
      }
      // Keep loading state
      return;
    }
    
    const indices = Array.isArray(data.indices) ? data.indices : [];
    const kospi = indices.find((x) => (x.name || "").toUpperCase() === "KOSPI");
    const kosdaq = indices.find((x) => (x.name || "").toUpperCase() === "KOSDAQ");
    setIndexBox("kospi", kospi);
    setIndexBox("kosdaq", kosdaq);
    renderThemes(data.themes);
    renderStocks(data.stocks);
  } catch (err) {
    console.error("renderSnapshot error:", err);
    els.stocksTbody.innerHTML = `<tr><td colspan="5" class="muted">데이터 렌더링 오류가 발생했습니다.</td></tr>`;
  }
}

async function fetchSnapshot() {
  const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
  const token = (localStorage.getItem("ls_token") || "").trim();
  
  const snapUrl = httpUrl(baseUrl, "/snapshot");
  
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);
    
    const res = await fetch(snapUrl, {
      headers: token ? { "X-App-Token": token } : undefined,
      cache: "no-store",
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const obj = await res.json();
    if (!obj) throw new Error("Invalid response format");
    
    // Handle empty data case (initial loading)
    if (obj.type === "empty" || !obj.data) {
      setBadge("badge--warn", "데이터 로딩 중");
      setStatus("서버가 데이터를 수집 중입니다. 잠시 후 다시 시도해주세요.");
      // Retry after 5 seconds
      setTimeout(() => fetchSnapshot(), 5000);
      return false;
    }
    
    renderSnapshot(obj);
    setBadge("badge--ok", "연결됨");
    setStatus("데이터를 수신했습니다.");
    return true;
  } catch (err) {
    if (err.name === "AbortError") {
      console.error("fetchSnapshot timeout");
    } else {
      console.error("fetchSnapshot error:", err);
    }
    showServerDown();
    // Ensure table shows error state
    if (els.stocksTbody) {
      const currentText = els.stocksTbody.textContent || "";
      if (currentText.includes("데이터를 불러오는 중") || currentText.trim() === "") {
        els.stocksTbody.innerHTML = `<tr><td colspan="5" class="muted">서버 연결 실패. 다시 시도해주세요.</td></tr>`;
      }
    }
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
  
  // If no explicit server URL is set, skip WebSocket and go straight to polling
  const savedUrl = localStorage.getItem("ls_server_url") || "";
  if (!savedUrl || savedUrl.trim() === "") {
    setBadge("badge--warn", "설정 필요");
    setStatus("서버 URL을 설정하거나 현재 서버를 사용합니다.");
    // Use current host and go straight to polling
    startPolling();
    return;
  }
  
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

function setupEventListeners() {
  if (els.saveBtn) {
    els.saveBtn.addEventListener("click", () => {
      saveSettings();
      connect();
    });
  }
  
  if (els.refreshBtn) {
    els.refreshBtn.addEventListener("click", () => {
      triggerRefresh();
    });
  }
  
  if (els.refreshBtn2) {
    els.refreshBtn2.addEventListener("click", () => {
      triggerRefresh();
    });
  }
  
  if (els.mClose) {
    els.mClose.addEventListener("click", () => {
      try { els.modal.close(); } catch {}
    });
  }
  
  if (els.clearBtn) {
    els.clearBtn.addEventListener("click", () => {
      clearSettings();
      connect();
    });
  }
  
  // Settings button - show/hide server config card
  if (els.settingsBtn && els.serverConfigCard) {
    els.settingsBtn.addEventListener("click", () => {
      const isVisible = els.serverConfigCard.style.display !== "none";
      if (isVisible) {
        els.serverConfigCard.style.display = "none";
      } else {
        els.serverConfigCard.style.display = "block";
        // Expand content when showing
        if (els.serverConfigContent) {
          els.serverConfigContent.style.display = "block";
        }
        if (els.serverConfigCard) {
          els.serverConfigCard.classList.remove("collapsed");
        }
        // Scroll to config card
        els.serverConfigCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
  }
  
  // Server config toggle (internal expand/collapse)
  if (els.serverConfigToggle && els.serverConfigContent) {
    els.serverConfigToggle.addEventListener("click", () => {
      const isCollapsed = els.serverConfigCard.classList.contains("collapsed");
      if (isCollapsed) {
        els.serverConfigCard.classList.remove("collapsed");
        els.serverConfigContent.style.display = "block";
      } else {
        els.serverConfigCard.classList.add("collapsed");
        els.serverConfigContent.style.display = "none";
      }
    });
  }
  
  // Initially hide server config card if server URL is already set
  const savedUrl = localStorage.getItem("ls_server_url") || "";
  if (savedUrl.trim() && els.serverConfigCard) {
    els.serverConfigCard.style.display = "none";
  }
  
  window.addEventListener("online", () => connect());
  window.addEventListener("offline", () => {
    setBadge("badge--bad", "오프라인");
    setStatus("인터넷 연결이 없습니다.");
  });
}

// Initialize when DOM is ready
function init() {
  if (!initElements()) {
    console.error("Failed to initialize elements");
    return;
  }
  
  setupEventListeners();
  loadSettings();
  connect();
}

// Run initialization
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  // DOM already loaded
  init();
}

// Ask SW to activate new versions immediately (helps PWA update).
if (navigator.serviceWorker && navigator.serviceWorker.controller) {
  try {
    navigator.serviceWorker.controller.postMessage({ type: "SKIP_WAITING" });
  } catch {}
}


