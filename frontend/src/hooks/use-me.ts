"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

/** Oturumdaki kullanıcı. Hasta ekranları `me.patient_id` ile kendi verisini çeker. */
export function useMe() {
  const [me, setMe] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.me().then(setMe).catch((e) => setError(e.message));
  }, []);
  return { me, error };
}
