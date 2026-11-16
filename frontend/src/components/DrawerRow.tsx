import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, type DocumentInfo, type Drawer } from "../api";
import { useErrorMessage } from "../hooks";
import { UploadDrop } from "./UploadDrop";

interface DrawerRowProps {
  drawer: Drawer;
  documents: DocumentInfo[];
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onOpenDocument: (doc: DocumentInfo) => void;
}

export function DrawerRow({ drawer, documents, open, onToggle, onChanged, onOpenDocument }: DrawerRowProps) {
  const { t } = useTranslation();
  const bodyId = `drawer-body-${drawer.id}`;
  const className = drawer.is_temporary ? "drawer drawer-temporary" : "drawer";

  return (
    <li className={`${className}${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="drawer-front"
        aria-expanded={open}
        aria-controls={bodyId}
        aria-label={t(open ? "drawer.close" : "drawer.open", { name: drawer.name })}
        onClick={onToggle}
      >
        <span className="drawer-label">
          <span className="drawer-name">{drawer.name}</span>
          <span className="drawer-count">{t("drawer.documents", { count: drawer.document_count })}</span>
        </span>
        <span className="drawer-handle" aria-hidden="true" />
      </button>
      {drawer.is_temporary && <p className="temporary-note">{t("temporary.note")}</p>}
      <div id={bodyId} className="drawer-body" hidden={!open}>
        {open && (
          <DrawerContents drawer={drawer} documents={documents} onChanged={onChanged} onOpenDocument={onOpenDocument} />
        )}
      </div>
    </li>
  );
}

interface DrawerContentsProps {
  drawer: Drawer;
  documents: DocumentInfo[];
  onChanged: () => void;
  onOpenDocument: (doc: DocumentInfo) => void;
}

function DrawerContents({ drawer, documents, onChanged, onOpenDocument }: DrawerContentsProps) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(drawer.name);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    }
  };

  const rename = async (event: FormEvent) => {
    event.preventDefault();
    if (await run(() => api.renameDrawer(drawer.id, name))) setRenaming(false);
  };

  const removeDrawer = () => {
    if (window.confirm(t("drawer.confirmDelete", { name: drawer.name }))) void run(() => api.deleteDrawer(drawer.id));
  };

  const removeDocument = (doc: DocumentInfo) => {
    if (window.confirm(t("document.confirmDelete", { name: doc.filename }))) void run(() => api.deleteDocument(doc.id));
  };

  return (
    <>
      {documents.length === 0 ? (
        <p className="muted">{t("drawer.empty")}</p>
      ) : (
        <ul className="folder-tabs">
          {documents.map((doc) => (
            <FolderTab key={doc.id} doc={doc} onOpen={() => onOpenDocument(doc)} onDelete={() => removeDocument(doc)} />
          ))}
        </ul>
      )}
      <UploadDrop drawerId={drawer.id} onUploaded={onChanged} />
      {renaming ? (
        <form className="rename-form" onSubmit={rename}>
          <label htmlFor={`rename-${drawer.id}`}>{t("drawer.nameLabel")}</label>
          <input
            id={`rename-${drawer.id}`}
            value={name}
            maxLength={120}
            required
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit">{t("drawer.save")}</button>
          <button type="button" className="quiet" onClick={() => setRenaming(false)}>
            {t("drawer.cancel")}
          </button>
        </form>
      ) : (
        <div className="drawer-actions">
          <button type="button" className="quiet" onClick={() => setRenaming(true)}>
            {t("drawer.rename")}
          </button>
          <button type="button" className="quiet danger" onClick={removeDrawer}>
            {t("drawer.delete")}
          </button>
        </div>
      )}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

interface FolderTabProps {
  doc: DocumentInfo;
  onOpen: () => void;
  onDelete: () => void;
}

function FolderTab({ doc, onOpen, onDelete }: FolderTabProps) {
  const { t, i18n } = useTranslation();
  const status =
    doc.status === "processing"
      ? doc.ingest_pages_done >= doc.page_count && doc.page_count > 0
        ? t("document.indexing")
        : t("document.reading", { done: doc.ingest_pages_done, total: doc.page_count })
      : doc.status === "failed"
        ? t("document.failed")
        : t("document.pages", { count: doc.page_count });
  const expires = doc.expires_at
    ? t("document.expires", {
        time: new Date(`${doc.expires_at}Z`).toLocaleString(i18n.language, {
          dateStyle: "short",
          timeStyle: "short",
        }),
      })
    : null;

  return (
    <li className={`folder-tab status-${doc.status}`}>
      <button type="button" className="folder-open" onClick={onOpen} disabled={doc.status !== "ready"}>
        <span className="folder-name">{doc.filename}</span>
        <span className="folder-meta">
          {status}
          {expires && ` · ${expires}`}
        </span>
      </button>
      <button
        type="button"
        className="folder-delete"
        aria-label={t("document.delete", { name: doc.filename })}
        onClick={onDelete}
      >
        ×
      </button>
      {doc.status === "failed" && doc.error && <p className="folder-error">{doc.error}</p>}
    </li>
  );
}
