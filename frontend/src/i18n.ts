import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import bs from "./locales/bs.json";
import en from "./locales/en.json";

export const LANGUAGES = ["en", "bs"] as const;
export type Language = (typeof LANGUAGES)[number];

const STORAGE_KEY = "reqtrace.language";

function storedLanguage(): Language {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "bs" ? "bs" : "en";
  } catch {
    return "en";
  }
}

/** Switch the UI language and remember the choice for the next visit. */
export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Storage can be unavailable (private mode); the switch still applies to this visit.
  }
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, bs: { translation: bs } },
  lng: storedLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

i18n.on("languageChanged", (language) => {
  document.documentElement.lang = language;
});
document.documentElement.lang = i18n.language;

export default i18n;
