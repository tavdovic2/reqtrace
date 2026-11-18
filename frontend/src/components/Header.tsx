import { useTranslation } from "react-i18next";

import type { Health } from "../api";
import { setDevMode, useDevMode } from "../devMode";
import { LANGUAGES, setLanguage, type Language } from "../i18n";

interface HeaderProps {
  health: Health | null;
  apiReachable: boolean;
}

function healthProblem(health: Health | null, apiReachable: boolean): { key: string; model?: string } | null {
  if (!apiReachable) return { key: "health.apiDown" };
  if (!health) return null;
  if (!health.qdrant) return { key: "health.qdrantDown" };
  if (!health.ollama) return { key: "health.ollamaDown" };
  if (!health.llm_model_available) return { key: "health.modelMissing", model: health.llm_model };
  return null;
}

export function Header({ health, apiReachable }: HeaderProps) {
  const { t, i18n } = useTranslation();
  const problem = healthProblem(health, apiReachable);
  const devMode = useDevMode();

  return (
    <header className="topbar">
      <a className="skip-link" href="#question-input">
        {t("app.skipToQuestion")}
      </a>
      <div className="brand">
        <span className="brand-name">reqTrace</span>
        <span className="brand-tagline">{t("app.tagline")}</span>
      </div>
      <div className="topbar-right">
        {health && !problem && (
          <span className="health-ok" role="status">
            {t("health.ready", { model: health.llm_model })}
          </span>
        )}
        <label className="dev-mode-switch">
          <input type="checkbox" checked={devMode} onChange={(event) => setDevMode(event.target.checked)} />
          {t("devMode.label")}
        </label>
        <div className="language-switch" role="group" aria-label={t("language.label")}>
          {LANGUAGES.map((language: Language) => (
            <button
              key={language}
              type="button"
              lang={language}
              aria-pressed={i18n.language === language}
              aria-label={t(`language.${language}Name`)}
              onClick={() => setLanguage(language)}
            >
              {t(`language.${language}`)}
            </button>
          ))}
        </div>
      </div>
      {problem && (
        <p className="health-banner" role="alert">
          {t(problem.key, { model: problem.model })}
        </p>
      )}
    </header>
  );
}
