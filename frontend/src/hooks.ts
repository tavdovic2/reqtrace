import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError, type DocumentInfo, type Drawer, type Health } from "./api";

const PROCESSING_POLL_MS = 2500;
const IDLE_POLL_MS = 60_000;
const HEALTH_POLL_MS = 15_000;

/** Turns any thrown value into a message in the current language where possible. */
export function useErrorMessage(): (error: unknown) => string {
  const { t } = useTranslation();
  return useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 0) return t("errors.network");
      return error instanceof Error ? error.message : String(error);
    },
    [t],
  );
}

export interface CabinetState {
  drawers: Drawer[] | null;
  documents: Record<number, DocumentInfo[]>;
  error: unknown;
  refresh: () => Promise<void>;
}

/** Drawers with their documents; polls quickly while any document is still being read. */
export function useCabinet(): CabinetState {
  const [drawers, setDrawers] = useState<Drawer[] | null>(null);
  const [documents, setDocuments] = useState<Record<number, DocumentInfo[]>>({});
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listDrawers();
      const perDrawer = await Promise.all(list.map((d) => api.listDocuments(d.id)));
      setDrawers(list);
      setDocuments(Object.fromEntries(list.map((d, i) => [d.id, perDrawer[i]])));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  const processing = Object.values(documents).some((docs) => docs.some((d) => d.status === "processing"));

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => void refresh(), processing ? PROCESSING_POLL_MS : IDLE_POLL_MS);
    return () => window.clearInterval(id);
  }, [processing, refresh]);

  return { drawers, documents, error, refresh };
}

/** Backend health, or null while unknown; `reachable` is false when the API itself is down. */
export function useHealth(): { health: Health | null; reachable: boolean } {
  const [health, setHealth] = useState<Health | null>(null);
  const [reachable, setReachable] = useState(true);

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const result = await api.health();
        if (active) {
          setHealth(result);
          setReachable(true);
        }
      } catch {
        if (active) setReachable(false);
      }
    };
    void check();
    const id = window.setInterval(() => void check(), HEALTH_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  return { health, reachable };
}
