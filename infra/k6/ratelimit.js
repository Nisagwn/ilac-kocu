// İstek sınırı doğrulaması (varsayılan ayarlarla): tek kullanıcı kovayı (120 jeton, 2/sn) boşaltınca 429 +
// Retry-After almalı; X-RateLimit-* başlıkları her yanıtta olmalı. Ayrıca nginx IP sınırı (20 r/sn, 40 patlama).
//   docker compose --profile loadtest run --rm k6 run /scripts/ratelimit.js
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";
import { auth, BASE, login } from "./lib.js";

const limited = new Counter("limited_429");

export const options = {
  scenarios: { burst: { executor: "per-vu-iterations", vus: 1, iterations: 1 } },
  thresholds: { limited_429: ["count>0"], checks: ["rate==1"] },
};

export default function () {
  const token = login("hasta@demo.local");
  let first429 = null;
  for (let i = 0; i < 200; i++) {
    const res = http.get(`${BASE}/v1/auth/me`, auth(token));
    check(res, { "X-RateLimit-Limit var": (x) => x.status !== 200 || !!x.headers["X-Ratelimit-Limit"] });
    if (res.status === 429) {
      limited.add(1);
      if (first429 === null) {
        first429 = i;
        check(res, { "429'da Retry-After": (x) => Number(x.headers["Retry-After"]) >= 1 });
      }
    }
  }
  console.log(`İlk 429: ${first429}. istekte`);
}
