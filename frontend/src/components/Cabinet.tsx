import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, type DocumentInfo, type Drawer } from "../api";
import { useErrorMessage, type CabinetState } from "../hooks";
import { DrawerRow } from "./DrawerRow";

interface CabinetProps {
  cabinet: CabinetState;
  openDrawerId: number | null;
  onOpenDrawer: (id: number | null) => void;
  onOpenDocument: (doc: DocumentInfo) => void;
}

/** Drawers matching the search, each with only its matching documents (all when the name matches). */
function filterCabinet(drawers: Drawer[], documents: Record<number, DocumentInfo[]>, query: string) {
  const q = query.trim().toLowerCase();
  return drawers
    .map((drawer) => {
      const docs = documents[drawer.id] ?? [];
      if (!q || drawer.name.toLowerCase().includes(q)) return { drawer, docs, matchedDocs: false };
      const matching = docs.filter((d) => d.filename.toLowerCase().includes(q));
      return matching.length ? { drawer, docs: matching, matchedDocs: true } : null;
    })
    .filter((entry) => entry !== null);
}

export function Cabinet({ cabinet, openDrawerId, onOpenDrawer, onOpenDocument }: CabinetProps) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const searchId = useId();
  const [query, setQuery] = useState("");
  const { drawers, documents, error, refresh } = cabinet;

  const changed = () => void refresh();

  let content;
  if (drawers === null && error) {
    content = (
      <div className="state-box" role="alert">
        <p>{t("cabinet.loadError", { message: errorMessage(error) })}</p>
        <button type="button" onClick={changed}>
          {t("cabinet.retry")}
        </button>
      </div>
    );
  } else if (drawers === null) {
    content = (
      <p className="state-box" role="status">
        {t("cabinet.loading")}
      </p>
    );
  } else {
    const entries = filterCabinet(drawers, documents, query);
    const renderRow = ({ drawer, docs, matchedDocs }: (typeof entries)[number]) => (
      <DrawerRow
        key={drawer.id}
        drawer={drawer}
        documents={docs}
        open={openDrawerId === drawer.id || matchedDocs}
        onToggle={() => onOpenDrawer(openDrawerId === drawer.id ? null : drawer.id)}
        onChanged={changed}
        onOpenDocument={onOpenDocument}
      />
    );
    const temporary = entries.filter((e) => e.drawer.is_temporary);
    const permanent = entries.filter((e) => !e.drawer.is_temporary);
    content = (
      <>
        {temporary.length > 0 && (
          <section className="tray" aria-label={t("temporary.title")}>
            <ul className="drawer-list">{temporary.map(renderRow)}</ul>
          </section>
        )}
        <div className="cabinet-body">
          {drawers.length === 0 && <p className="muted cabinet-empty">{t("cabinet.empty")}</p>}
          {drawers.length > 0 && entries.length === 0 && (
            <p className="muted cabinet-empty">{t("cabinet.noMatches", { query })}</p>
          )}
          <ul className="drawer-list">{permanent.map(renderRow)}</ul>
          <NewDrawer
            onCreated={(drawer) => {
              onOpenDrawer(drawer.id);
              changed();
            }}
          />
        </div>
      </>
    );
  }

  return (
    <aside className="cabinet" aria-labelledby="cabinet-title">
      <h2 id="cabinet-title" className="column-title">
        {t("cabinet.title")}
      </h2>
      <label htmlFor={searchId} className="visually-hidden">
        {t("cabinet.search")}
      </label>
      <input
        id={searchId}
        type="search"
        className="cabinet-search"
        placeholder={t("cabinet.search")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="cabinet-scroll">{content}</div>
    </aside>
  );
}

function NewDrawer({ onCreated }: { onCreated: (drawer: Drawer) => void }) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const nameId = useId();
  const tempId = useId();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [temporary, setTemporary] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      const drawer = await api.createDrawer(name.trim(), temporary);
      setName("");
      setTemporary(false);
      setEditing(false);
      onCreated(drawer);
    } catch (e) {
      setError(t("newDrawer.failed", { message: errorMessage(e) }));
    }
  };

  if (!editing) {
    return (
      <button type="button" className="new-drawer" onClick={() => setEditing(true)}>
        {t("newDrawer.button")}
      </button>
    );
  }

  return (
    <form className="new-drawer-form" onSubmit={submit}>
      <label htmlFor={nameId}>{t("drawer.nameLabel")}</label>
      <input
        id={nameId}
        value={name}
        maxLength={120}
        required
        autoFocus
        onChange={(event) => setName(event.target.value)}
      />
      <div className="checkbox-row">
        <input
          id={tempId}
          type="checkbox"
          checked={temporary}
          onChange={(event) => setTemporary(event.target.checked)}
        />
        <label htmlFor={tempId}>{t("newDrawer.temporary")}</label>
      </div>
      <div className="form-buttons">
        <button type="submit">{t("newDrawer.create")}</button>
        <button type="button" className="quiet" onClick={() => setEditing(false)}>
          {t("drawer.cancel")}
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
