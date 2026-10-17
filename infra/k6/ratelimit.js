// İstek sınırı doğrulaması (varsayılan ayarlarla), iki katman:
//  1) nginx IP sınırı (20 r/sn + 40 patlama): tek IP'den hızlı patlama -> 429 + Retry-After (X-RateLimit-* yok)
//  2) API token bucket (kullanıcı başına 120 jeton, 2/sn): nginx sınırının altında (15 r/sn) sürekli istek ->
//     kova boşalınca 429 + Retry-After + X-RateLimit-Remaining: 0
//   docker compose --profile loadtest run --rm k6 run /scripts/ratelimit.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";
import { auth, BASE, login } from "./lib.js";

const nginx429 = new Counter("nginx_429");
const app429 = new Counter("app_429");

export const options = {
  scenarios: { probe: { executor: "per-vu-iterations", vus: 1, iterations: 1, maxDuration: "3m" } },
  thresholds: { nginx_429: ["count>0"], app_429: ["count>0"], checks: ["rate==1"] },
};

function classify(res) {
  if (res.status !== 429) return null;
  check(res, { "429'da Retry-After": (x) => Number(x.headers["Retry-After"]) >= 1 });
  check(res, { "429 gövdesi JSON": (x) => typeof x.json("detail") === "string" });
  return res.headers["X-Ratelimit-Limit"] ? "app" : "nginx";
}

export default function () {
  const token = login("hasta@demo.local");

  // 1) patlama: 100 isteği beklemeden gönder
  const burst = http.batch(Array.from({ length: 100 }, () => ["GET", `${BASE}/v1/auth/me`, null, auth(token)]));
  burst.forEach((r) => classify(r) === "nginx" && nginx429.add(1));
  sleep(5); // nginx kovası dolsun

  // 2) nginx sınırının altında, API kovası boşalana kadar
  let firstApp = null;
  for (let i = 0; i < 400 && firstApp === null; i++) {
    const res = http.get(`${BASE}/v1/auth/me`, auth(token));
    check(res, { "200'de X-RateLimit-Limit": (x) => x.status !== 200 || x.headers["X-Ratelimit-Limit"] === "120" });
    const kind = classify(res);
    if (kind === "app") {
      app429.add(1);
      firstApp = i;
      check(res, { "API 429'da kalan 0": (x) => x.headers["X-Ratelimit-Remaining"] === "0" });
    } else if (kind === "nginx") {
      nginx429.add(1);
    }
    sleep(1 / 15);
  }
  console.log(`Patlamada nginx 429: ${burst.filter((r) => r.status === 429).length}/100; ` +
              `API kovası ${firstApp} istekte boşaldı (15 r/sn)`);
}
