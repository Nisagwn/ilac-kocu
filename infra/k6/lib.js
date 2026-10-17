// Ortak yardımcılar: giriş, kimlikli istek, X-Upstream sayımı.
import http from "k6/http";
import { Counter } from "k6/metrics";

export const BASE = __ENV.BASE_URL || "http://localhost:8000";
export const upstreamHits = new Counter("upstream_hits");

export function login(email, password = "demo12345") {
  const res = http.post(`${BASE}/v1/auth/login`, { username: email, password });
  if (res.status !== 200) throw new Error(`Giriş başarısız (${email}): ${res.status} ${res.body}`);
  return res.json("access_token");
}

export function auth(token, extra = {}) {
  return { headers: { Authorization: `Bearer ${token}`, ...extra } };
}

export function track(res) {
  const up = res.headers["X-Upstream"];
  if (up) upstreamHits.add(1, { upstream: up.split(",").pop().trim() });
  return res;
}

export function uuid() {
  return "k6-" + `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
