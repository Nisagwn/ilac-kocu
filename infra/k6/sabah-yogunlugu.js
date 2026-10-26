// Sabah yoğunluğu senaryosu (k6) — docs/yuk-testi.md
//
//   hastalar  : giriş → bugünün dozları → "aldım" → açlık kan şekeri ekleme → son 30 gün ölçümler
//   hekimler  : giriş → hasta listesi yenileme (uyum skorları) → açık uyarılar
//
// LLM uçları (/coach/*) bilinçli olarak YOK: p95 < 300 ms eşiği LLM hariç API içindir; koç gecikmesi
// sağlayıcıya bağlıdır ve ayrı ölçülür (llm_request_duration_seconds).
//
// Ortam değişkenleri:
//   TARGET     gateway (varsayılan): nginx:8081 → api×N, Bearer token   | web: nginx:80 → Next.js → api, çerez
//   PATIENTS   yuk-hasta-001..N hesap sayısı (backend/scripts/seed_load_test.py). 0 → demo hesapları
//              (hasta@demo.local / hekim@demo.local; e-posta başına giriş sınırı nedeniyle yalnız duman testi)
//   CLINICIANS yuk-hekim-01..M      PEAK_VUS en yüksek sanal hasta sayısı      DURATION tepe süresi (ör. 2m)
//   LABEL      sonuç dosyası öneki (results/<LABEL>-summary.json)   THINK düşünme süresi çarpanı (vars. 1)
//
// Giriş: gateway modunda her sentetik hesap setup() içinde BİR KEZ giriş yapar (bcrypt yük ölçümünün dışında
// kalır); sanal kullanıcılar bu token'ı kullanır. Token süresi dolmak üzereyse ya da 401 gelirse yalnız o VU
// kendi başına yeniden giriş yapar (yenileme token'ı aynı hesabı paylaşan VU'lar arasında döndürülmez: iptal
// edilmiş token'ın tekrar kullanımı tüm oturumları düşürürdü). web modunda çerezler VU başına olduğundan her VU
// ilk iterasyonunda bir kez giriş yapar.
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const TARGET = __ENV.TARGET || "gateway";
const BASE = __ENV.BASE_URL || (TARGET === "web" ? "http://nginx" : "http://nginx:8081");
const PREFIX = TARGET === "web" ? "/api/proxy/v1" : "/v1";
const PATIENTS = parseInt(__ENV.PATIENTS || "50", 10);
const CLINICIANS = parseInt(__ENV.CLINICIANS || "5", 10);
const PEAK_VUS = parseInt(__ENV.PEAK_VUS || "50", 10);
const DURATION = __ENV.DURATION || "2m";
const LABEL = __ENV.LABEL || "run";
const THINK = parseFloat(__ENV.THINK || "1"); // düşünme süresi çarpanı (0.1 → 10 kat yoğun trafik)
const PASSWORD = "demo12345"; // sentetik hesaplar

function pause(seconds) {
  if (THINK > 0) sleep(seconds * THINK);
}

const steps = {
  login: new Trend("step_login", true),
  today: new Trend("step_doses_today", true),
  dose: new Trend("step_dose_log", true),
  measurement: new Trend("step_measurement_add", true),
  measurements: new Trend("step_measurements_list", true),
  patients: new Trend("step_patient_list", true),
  alerts: new Trend("step_alerts", true),
};
const rateLimited = new Rate("rate_limited_429");
// istek/sn setup() girişleri hariç hesaplanır (aksi halde setup süresi oranı seyreltir)
const scenarioReqs = new Counter("scenario_reqs");
const setupMs = new Trend("setup_duration_ms");

export const options = {
  scenarios: {
    hastalar: {
      executor: "ramping-vus",
      exec: "patientFlow",
      startVUs: 0,
      stages: [
        { duration: "30s", target: PEAK_VUS }, // 07:55 → 08:00 alarmı
        { duration: DURATION, target: PEAK_VUS },
        { duration: "15s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
    hekimler: {
      executor: "constant-vus",
      exec: "clinicianFlow",
      vus: Math.max(1, CLINICIANS),
      duration: `${30 + parseDuration(DURATION) + 15}s`,
    },
  },
  thresholds: {
    // LLM hariç tüm API istekleri (girişteki bcrypt ayrı izlenir)
    "http_req_duration{kind:api}": ["p(95)<300"],
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:login}": ["p(95)<2000"],
    checks: ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
  setupTimeout: "180s", // her hesap için bir bcrypt girişi
};

function parseDuration(d) {
  const m = /^(\d+)(s|m)$/.exec(d);
  if (!m) return 120;
  return parseInt(m[1], 10) * (m[2] === "m" ? 60 : 1);
}

function pad(n, w) {
  return String(n).padStart(w, "0");
}

function patientEmail() {
  if (PATIENTS <= 0) return "hasta@demo.local";
  return `yuk-hasta-${pad(((__VU - 1) % PATIENTS) + 1, 3)}@demo.local`;
}

function clinicianEmail() {
  if (PATIENTS <= 0) return "hekim@demo.local";
  return `yuk-hekim-${pad(((__VU - 1) % Math.max(1, CLINICIANS)) + 1, 2)}@demo.local`;
}

// VU başına oturum (k6'da her VU ayrı JS ortamıdır; çerez kavanozu da VU başınadır).
// gateway: Bearer erişim token'ı; web: Next.js'in httpOnly çerezleri (k6 kavanozu otomatik gönderir).
const session = { loggedIn: false, token: null, patientId: null, expiresAt: 0, usedSetup: false };
const RENEW_MARGIN_MS = 60 * 1000; // token bitmeden 1 dk önce yenilenir

function authHeaders(extra) {
  const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
  if (session.token) h.Authorization = `Bearer ${session.token}`;
  return h;
}

// Tek bir girişi yapar; başarılıysa { token, patientId, expiresAt } döner (web modunda token null).
function doLogin(email) {
  let res;
  if (TARGET === "web") {
    res = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email, password: PASSWORD }), {
      headers: { "Content-Type": "application/json" },
      tags: { kind: "login", name: "login" },
    });
  } else {
    res = http.post(`${BASE}/v1/auth/login`, { username: email, password: PASSWORD }, {
      tags: { kind: "login", name: "login" },
    });
  }
  steps.login.add(res.timings.duration);
  rateLimited.add(res.status === 429);
  if (!check(res, { "giriş 200": (r) => r.status === 200 })) return null;
  const token = TARGET !== "web" ? res.json("access_token") : null;
  const expiresIn = TARGET !== "web" ? res.json("expires_in") || 900 : 900;
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const me = http.get(`${BASE}${PREFIX}/auth/me`, { headers, tags: { kind: "api", name: "auth/me" } });
  if (!check(me, { "me 200": (r) => r.status === 200 })) return null;
  return { token, patientId: me.json("patient_id"), expiresAt: Date.now() + expiresIn * 1000 };
}

function useSession(s) {
  session.token = s.token;
  session.patientId = s.patientId;
  session.expiresAt = s.expiresAt;
  session.loggedIn = true;
}

function login(email) {
  const s = doLogin(email);
  if (!s) return false;
  useSession(s);
  return true;
}

// gateway: her hesap için bir kez giriş, token'lar VU'lara dağıtılır (k6 setup() verisi her VU'ya kopyalanır)
export function setup() {
  if (TARGET === "web") return { sessions: {} };
  const emails = [];
  if (PATIENTS <= 0) {
    emails.push("hasta@demo.local", "hekim@demo.local");
  } else {
    for (let i = 1; i <= PATIENTS; i++) emails.push(`yuk-hasta-${pad(i, 3)}@demo.local`);
    for (let i = 1; i <= Math.max(1, CLINICIANS); i++) emails.push(`yuk-hekim-${pad(i, 2)}@demo.local`);
  }
  const t0 = Date.now();
  const sessions = {};
  for (const email of emails) {
    const s = doLogin(email);
    if (s) sessions[email] = s;
  }
  setupMs.add(Date.now() - t0);
  return { sessions };
}

function api(method, path, body, name, extraHeaders) {
  const params = { headers: authHeaders(extraHeaders), tags: { kind: "api", name } };
  const url = `${BASE}${PREFIX}${path}`;
  const res = http.request(method, url, body === null ? null : JSON.stringify(body), params);
  scenarioReqs.add(1);
  rateLimited.add(res.status === 429);
  if (res.status === 401) session.loggedIn = false; // oturum düştü → bir sonraki turda yeniden giriş
  return res;
}

function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function ensureLogin(email, data) {
  if (session.loggedIn && Date.now() < session.expiresAt - RENEW_MARGIN_MS) return true;
  // İlk iterasyon: setup() token'ı (hâlâ geçerliyse) kullanılır; aksi halde bu VU kendisi giriş yapar
  const pre = !session.usedSetup && data && data.sessions ? data.sessions[email] : null;
  session.usedSetup = true; // 401/süre dolumu sonrası setup token'ına geri dönülmez
  if (pre && Date.now() < pre.expiresAt - RENEW_MARGIN_MS) {
    useSession(pre);
    return true;
  }
  scenarioReqs.add(2); // giriş + /auth/me
  return login(email);
}

export function patientFlow(data) {
  if (!ensureLogin(patientEmail(), data)) {
    pause(5);
    return;
  }
  const pid = session.patientId;

  let res = api("GET", `/patients/${pid}/doses/today`, null, "doses/today");
  steps.today.add(res.timings.duration);
  check(res, { "bugün 200": (r) => r.status === 200 });
  const doses = res.status === 200 ? res.json() : [];
  pause(Math.random() * 2 + 1);

  if (doses.length > 0) {
    const d = doses[Math.floor(Math.random() * doses.length)];
    const today = new Date().toISOString().slice(0, 10); // UTC tarihi (Europe/Istanbul bugününe ≤)
    res = api("PUT", `/patients/${pid}/doses`, { schedule_id: d.schedule_id, scheduled_date: today, status: "taken" },
      "doses/put", { "Idempotency-Key": uuid() });
    steps.dose.add(res.timings.duration);
    check(res, { "doz 200": (r) => r.status === 200 });
    pause(Math.random() * 2 + 1);
  }

  res = api("POST", `/patients/${pid}/measurements`,
    { type: "glucose_fasting", value: 95 + Math.round(Math.random() * 40) }, "measurements/post",
    { "Idempotency-Key": uuid() });
  steps.measurement.add(res.timings.duration);
  check(res, { "ölçüm 201": (r) => r.status === 201 });
  pause(Math.random() * 2 + 1);

  res = api("GET", `/patients/${pid}/measurements?days=30`, null, "measurements/list");
  steps.measurements.add(res.timings.duration);
  check(res, { "ölçümler 200": (r) => r.status === 200 });
  pause(Math.random() * 3 + 2);
}

export function clinicianFlow(data) {
  if (!ensureLogin(clinicianEmail(), data)) {
    pause(5);
    return;
  }
  let res = api("GET", "/patients", null, "patients/list");
  steps.patients.add(res.timings.duration);
  check(res, { "hasta listesi 200": (r) => r.status === 200 });
  res = api("GET", "/alerts", null, "alerts/list");
  steps.alerts.add(res.timings.duration);
  check(res, { "uyarılar 200": (r) => r.status === 200 });
  pause(5); // panel yenileme aralığı
}

// --- Özet: kısa tablo (stdout) + JSON (results/<LABEL>-summary.json) ---
function ms(v) {
  return v === undefined ? "-" : `${v.toFixed(1)}`;
}

export function handleSummary(data) {
  const m = data.metrics;
  const rows = [
    ["Adım", "ort (ms)", "p95 (ms)", "p99 (ms)", "maks (ms)"],
  ];
  for (const [label, key] of [
    ["giriş (bcrypt)", "step_login"], ["bugünün dozları", "step_doses_today"], ["doz kaydı", "step_dose_log"],
    ["ölçüm ekleme", "step_measurement_add"], ["ölçüm listesi", "step_measurements_list"],
    ["hekim hasta listesi", "step_patient_list"], ["hekim uyarılar", "step_alerts"],
  ]) {
    const t = m[key];
    if (!t) continue;
    rows.push([label, ms(t.values.avg), ms(t.values["p(95)"]), ms(t.values["p(99)"]),
      ms(t.values.max)]);
  }
  const apiDur = m["http_req_duration{kind:api}"];
  const failed = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  const reqs = m.http_reqs ? m.http_reqs.values : { count: 0, rate: 0 };
  const setupSec = m.setup_duration_ms ? m.setup_duration_ms.values.max / 1000 : 0;
  const scenSec = data.state.testRunDurationMs / 1000 - setupSec;
  const scenCount = m.scenario_reqs ? m.scenario_reqs.values.count : 0;
  const lim = m.rate_limited_429 ? m.rate_limited_429.values.rate : 0;
  const thr = Object.entries(m)
    .filter(([, v]) => v.thresholds)
    .map(([k, v]) => `${Object.values(v.thresholds).every((t) => t.ok) ? "GEÇTİ" : "KALDI"}  ${k} ${Object.keys(v.thresholds).join(", ")}`);
  const lines = [
    "",
    `=== k6 özeti: ${LABEL} (hedef: ${TARGET}, tepe ${PEAK_VUS} sanal hasta + ${CLINICIANS} hekim) ===`,
    `toplam istek: ${reqs.count}  (${reqs.rate.toFixed(1)} istek/sn)   giriş sayısı: ${m.step_login ? m.step_login.values.count : 0}`,
    `senaryo: ${scenCount} istek / ${scenSec.toFixed(0)} sn = ${(scenSec > 0 ? scenCount / scenSec : 0).toFixed(1)} istek/sn (setup ${setupSec.toFixed(1)} sn hariç)`,
    `API p95 (LLM ve giriş hariç): ${apiDur ? ms(apiDur.values["p(95)"]) : "-"} ms   hata oranı: ${(failed * 100).toFixed(2)}%   429 oranı: ${(lim * 100).toFixed(2)}%`,
    "",
    ...rows.map((r) => r.map((c, i) => (i === 0 ? c.padEnd(22) : c.padStart(9))).join(" ")),
    "",
    "Eşikler:",
    ...thr.map((t) => "  " + t),
    "",
  ];
  return {
    stdout: lines.join("\n"),
    [`/scripts/results/${LABEL}-summary.json`]: JSON.stringify(data, null, 2),
  };
}
