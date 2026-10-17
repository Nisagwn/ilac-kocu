// Kopya durdurma testi: sabit hızda okuma trafiği sürerken bir API kopyası durdurulur.
//   Terminal 1: docker compose --profile loadtest run --rm k6 run /scripts/failover.js
//   Terminal 2 (~20. saniyede): docker stop $(docker compose ps -q api | head -1)
// Beklenen: düzgün kapanma + nginx'in diğer kopyaya aktarması sayesinde hata oranı ~0;
// upstream_hits sayacında durdurulan kopyanın payı sıfıra iner.
import http from "k6/http";
import { check } from "k6";
import { auth, BASE, login, track } from "./lib.js";

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 40),
      timeUnit: "1s",
      duration: __ENV.DURATION || "60s",
      preAllocatedVUs: 20,
      maxVUs: 60,
    },
  },
  thresholds: { http_req_failed: ["rate<0.01"] },
};

export function setup() {
  const token = login("hekim@demo.local");
  return { token };
}

export default function (ctx) {
  const res = track(http.get(`${BASE}/v1/patients`, auth(ctx.token)));
  check(res, { "200": (x) => x.status === 200 });
}
