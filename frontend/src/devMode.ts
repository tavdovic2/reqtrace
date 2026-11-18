// Developer mode: shows the "Why?" panel under "not in the documents" answers and asks
// the API for the debug details it needs. Off by default; remembered in localStorage.
import { useSyncExternalStore } from "react";

export const DEV_MODE_KEY = "reqtrace.developerMode";

type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): KeyValueStore | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** The stored choice; anything but an explicit "on" (or no storage at all) means off. */
export function readDevMode(storage: KeyValueStore | null): boolean {
  try {
    return storage?.getItem(DEV_MODE_KEY) === "on";
  } catch {
    return false;
  }
}

export function writeDevMode(storage: KeyValueStore | null, on: boolean): void {
  try {
    storage?.setItem(DEV_MODE_KEY, on ? "on" : "off");
  } catch {
    // Storage can be unavailable (private mode); the switch still applies to this visit.
  }
}

/** The JSON body of POST /ask; the debug field is sent only in developer mode. */
export function askBody(
  question: string,
  drawerId: number | null,
  devMode: boolean,
): { question: string; drawer_id: number | null; debug?: true } {
  return devMode ? { question, drawer_id: drawerId, debug: true } : { question, drawer_id: drawerId };
}

let current = readDevMode(browserStorage());
const listeners = new Set<() => void>();

export function setDevMode(on: boolean): void {
  current = on;
  writeDevMode(browserStorage(), on);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The current developer-mode setting; components re-render when it changes. */
export function useDevMode(): boolean {
  return useSyncExternalStore(subscribe, () => current);
}
