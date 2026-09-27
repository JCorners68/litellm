import { useEffect, useRef, useState } from "react";

import { cacheLeakageKeysCall } from "@/components/networking";
import type { KeyActivityRow } from "@/components/UsagePage/dailyActivityApi";
import type { DailyActivityRange } from "./useDailyActivityRange";

interface CacheLeakageKeysResult {
  rows: KeyActivityRow[];
  loading: boolean;
  failed: boolean;
}

interface SettledKeys {
  key: string;
  rows: KeyActivityRow[];
  failed: boolean;
}

export const useCacheLeakageKeys = (range: DailyActivityRange, enabled: boolean): CacheLeakageKeysResult => {
  const { accessToken, startTime, endTime, userId, apiKey } = range.scope;
  const [settled, setSettled] = useState<SettledKeys | null>(null);
  const requestIdRef = useRef(0);

  const scopeReady = enabled && !!accessToken && !!startTime && !!endTime;
  const scopeKey = scopeReady ? JSON.stringify([accessToken, startTime, endTime, userId, apiKey]) : null;

  useEffect(() => {
    if (!scopeKey || !accessToken || !startTime || !endTime) return;

    const requestId = ++requestIdRef.current;
    const isStale = () => requestIdRef.current !== requestId;

    cacheLeakageKeysCall({
      accessToken,
      startTime,
      endTime,
      entityIds: userId ? [userId] : null,
      apiKey,
    })
      .then((response) => {
        if (isStale()) return;
        setSettled({ key: scopeKey, rows: response.api_keys, failed: false });
      })
      .catch((error) => {
        if (isStale()) return;
        console.error("Failed to fetch cache leakage keys:", error);
        setSettled({ key: scopeKey, rows: [], failed: true });
      });

    return () => {
      requestIdRef.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scopeKey serializes the scope
  }, [scopeKey]);

  const current = scopeKey !== null && settled?.key === scopeKey ? settled : null;
  return {
    rows: current?.rows ?? [],
    loading: scopeKey !== null && current === null,
    failed: current?.failed ?? false,
  };
};
