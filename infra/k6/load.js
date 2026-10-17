// Yük testi: sentetik demo hastası ve hekimi gerçekçi bir okuma/yazma karışımı üretir.
//   docker compose --profile loadtest run --rm k6 run /scripts/load.js
//   (ayrıntı ve kapasite ölçümü için istek sınırlarının gevşetilmesi: docs/yuk-testi.md)
// Ortam: VUS (eşzamanlı kullanıcı, 20), DURATION (60s), WRITE_RATIO (0.1)
import http from "k6/http";
import { check, sleep } from "k6";
import { auth, BASE, login, track, uuid } from "./lib.js";

const VUS = Number(__ENV.VUS || 20);
export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "10s", target: VUS },
        { duration: __ENV.DURATION || "60s", target: VUS },
        { duration: "5s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:read}": ["p(95)<500"],
    "http_req_duration{kind:write}": ["p(95)<800"],
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export function setup() {
  const patient = login("hasta@demo.local");
  const clinician = login("hekim@demo.local");
  const me = http.get(`${BASE}/v1/auth/me`, auth(patient)).json();
  return { patient, clinician, pid: me.patient_id };
}

export default function (ctx) {
  const p = auth(ctx.patient);
  const r = Math.random();
  const writeRatio = Number(__ENV.WRITE_RATIO || 0.1);
  if (r < writeRatio) {
    const res = track(http.post(`${BASE}/v1/patients/${ctx.pid}/measurements`,
      // note "k6": testin yazdığı ölçümler sonradan silinebilsin (docs/yuk-testi.md "Temizlik")
      JSON.stringify({ type: "glucose_fasting", value: 100 + Math.round(Math.random() * 40), note: "k6" }),
      { ...auth(ctx.patient, { "Content-Type": "application/json", "Idempotency-Key": uuid() }), tags: { kind: "write" } }));
    check(res, { "ölçüm 201": (x) => x.status === 201 });
  } else if (r < writeRatio + 0.1) {
    const res = track(http.get(`${BASE}/v1/patients`, { ...auth(ctx.clinician), tags: { kind: "read" } }));
    check(res, { "hasta listesi 200": (x) => x.status === 200 });
  } else {
    const urls = [
      `/v1/patients/${ctx.pid}/doses/today`,
      // sayfalı okuma: test yazdıkça yanıt büyümesin (aksi halde koşular birbirini etkiler)
      `/v1/patients/${ctx.pid}/measurements?days=30&limit=100`,
      `/v1/patients/${ctx.pid}/adherence?days=7`,
      `/v1/medications?patient_id=${ctx.pid}`,
      `/v1/auth/me`,
    ];
    const res = track(http.get(BASE + urls[Math.floor(Math.random() * urls.length)], { ...p, tags: { kind: "read" } }));
    check(res, { "okuma 200": (x) => x.status === 200 });
  }
  sleep(Number(__ENV.THINK || 0.2));
}
