import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import type { DocumentInfo, Drawer, Source } from "../api";
import { Conversation } from "./Conversation";
import { Requirements } from "./Requirements";

type Tab = "conversation" | "requirements";
const TABS: Tab[] = ["conversation", "requirements"];

interface NotepadProps {
  drawers: Drawer[];
  documents: Record<number, DocumentInfo[]>;
  openDrawer: Drawer | null;
  onOpenSource: (source: Source) => void;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
}

export function Notepad({ drawers, documents, openDrawer, onOpenSource, onOpenPage }: NotepadProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("conversation");

  // Arrow keys move between tabs, as in the WAI-ARIA tabs pattern.
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const next = TABS[(TABS.indexOf(tab) + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    setTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  };

  return (
    <main className="notepad">
      <div className="notepad-tabs" role="tablist" aria-label={t("notepad.tabs")} onKeyDown={onKeyDown}>
        {TABS.map((name) => (
          <button
            key={name}
            id={`tab-${name}`}
            type="button"
            role="tab"
            aria-selected={tab === name}
            aria-controls={`panel-${name}`}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)}
          >
            {t(`notepad.${name}`)}
          </button>
        ))}
      </div>
      <section
        id="panel-conversation"
        role="tabpanel"
        aria-labelledby="tab-conversation"
        className="notepad-page"
        hidden={tab !== "conversation"}
      >
        <Conversation openDrawer={openDrawer} onOpenSource={onOpenSource} />
      </section>
      <section
        id="panel-requirements"
        role="tabpanel"
        aria-labelledby="tab-requirements"
        className="notepad-page"
        hidden={tab !== "requirements"}
      >
        <Requirements drawers={drawers} documents={documents} onOpenPage={onOpenPage} />
      </section>
    </main>
  );
}
