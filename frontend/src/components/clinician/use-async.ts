"use client";

import { useCallback, useEffect, useState, type DependencyList } from "react";
import { errorMessage } from "./format";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Basit veri çekme kancası. `reload()` mevcut veriyi koruyarak yeniden çeker,
 * böylece yenileme sırasında ekran boşalmaz.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList) {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((e: unknown) => {
        if (!cancelled) setState((s) => ({ data: s.data, error: errorMessage(e), loading: false }));
      });
    return () => {
      cancelled = true;
    };
    // fn her render'da yeniden oluşur; bağımlılıkları çağıran taraf `deps` ile verir.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const setData = useCallback((updater: (prev: T | null) => T | null) => {
    setState((s) => ({ ...s, data: updater(s.data) }));
  }, []);

  return { ...state, reload, setData };
}
