import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from "react";
import { useTranslation } from "react-i18next";

import {
  api,
  exportUrl,
  type DocumentInfo,
  type Drawer,
  type ExportScope,
  type Requirement,
  type RequirementFlag,
  type RequirementsTable,
} from "../api";
import { useErrorMessage } from "../hooks";
import {
  STATUS_FILTERS,
  applyPatch,
  countRows,
  filterRows,
  matchesFilter,
  moveIndex,
  replaceRows,
  restoreRows,
  markSpans,
  reviewKeyAction,
  type ReviewPatch,
  type ReviewStatus,
  type StatusFilter,
} from "../review";

const POLL_MS = 2000;

type TableUpdate = (update: (table: RequirementsTable) => RequirementsTable) => void;

interface RequirementsProps {
  drawers: Drawer[];
  documents: Record<number, DocumentInfo[]>;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
}

export function Requirements({ drawers, documents, onOpenPage }: RequirementsProps) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const pickerId = useId();
  const [docId, setDocId] = useState<number | null>(null);
  const [table, setTable] = useState<RequirementsTable | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const groups = drawers
    .map((drawer) => ({ drawer, docs: (documents[drawer.id] ?? []).filter((d) => d.status === "ready") }))
    .filter((g) => g.docs.length > 0);
  const selected = groups.flatMap((g) => g.docs).find((d) => d.id === docId) ?? null;

  // Responses for a document that is no longer selected are dropped.
  const selectedRef = useRef<number | null>(null);
  const load = useCallback(
    async (id: number) => {
      try {
        const result = await api.getRequirements(id);
        if (selectedRef.current === id) {
          setTable(result);
          setError(null);
        }
      } catch (e) {
        if (selectedRef.current === id) setError(errorMessage(e));
      }
    },
    [errorMessage],
  );

  useEffect(() => {
    selectedRef.current = docId;
    setTable(null);
    setError(null);
    if (docId === null) return;
    setLoading(true);
    void load(docId).finally(() => setLoading(false));
  }, [docId, load]);

  const running = table?.status === "running" && table.document_id === docId;
  useEffect(() => {
    if (!running || docId === null) return;
    const id = window.setInterval(() => void load(docId), POLL_MS);
    return () => window.clearInterval(id);
  }, [running, docId, load]);

  const extract = async () => {
    if (docId === null) return;
    setError(null);
    try {
      setTable(await api.startExtraction(docId));
    } catch (e) {
      setError(t("requirements.failed", { message: errorMessage(e) }));
    }
  };

  // Review changes apply only to the table of the document they were made on.
  const updateTable = useCallback<TableUpdate>(
    (update) =>
      setTable((current) => (current && current.document_id === selectedRef.current ? update(current) : current)),
    [],
  );

  if (groups.length === 0) {
    return <p className="state-box muted">{t("requirements.noDocuments")}</p>;
  }

  const current = table && table.document_id === docId ? table : null;

  return (
    <div className="requirements">
      <div className="requirements-toolbar">
        <label htmlFor={pickerId}>{t("requirements.document")}</label>
        <select
          id={pickerId}
          value={docId ?? ""}
          onChange={(event) => setDocId(event.target.value ? Number(event.target.value) : null)}
        >
          <option value="">{t("requirements.choose")}</option>
          {groups.map(({ drawer, docs }) => (
            <optgroup key={drawer.id} label={drawer.name}>
              {docs.map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {doc.filename}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button type="button" onClick={() => void extract()} disabled={docId === null || running || loading}>
          {current && current.status !== "idle" ? t("requirements.rerun") : t("requirements.extract")}
        </button>
      </div>

      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">{t("viewer.loading")}</p>}
      {current && selected && (
        <RequirementsBody
          key={selected.id}
          table={current}
          doc={selected}
          onOpenPage={onOpenPage}
          updateTable={updateTable}
        />
      )}
    </div>
  );
}

function RequirementsBody({
  table,
  doc,
  onOpenPage,
  updateTable,
}: {
  table: RequirementsTable;
  doc: DocumentInfo;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
  updateTable: TableUpdate;
}) {
  const { t } = useTranslation();

  if (table.status === "running") {
    const text =
      table.total_chunks === 0
        ? t("requirements.starting")
        : t("requirements.running", { done: table.done_chunks, total: table.total_chunks });
    const elapsed = table.elapsed_seconds ?? 0;
    const remaining =
      table.done_chunks > 0 ? (elapsed / table.done_chunks) * (table.total_chunks - table.done_chunks) : null;
    return (
      <div className="progress" role="status">
        <p>{text}</p>
        <progress max={Math.max(table.total_chunks, 1)} value={table.done_chunks} />
        <p className="muted">
          {t("requirements.elapsed", { time: formatDuration(elapsed) })}
          {remaining !== null && ` · ${t("requirements.remaining", { time: formatDuration(remaining) })}`}
        </p>
      </div>
    );
  }
  if (table.status === "failed") {
    return (
      <p className="inline-error" role="alert">
        {t("requirements.failed", { message: table.error ?? "" })}
      </p>
    );
  }
  if (table.status === "idle") return <p className="muted">{t("requirements.notRun")}</p>;
  return <ReviewTable table={table} doc={doc} onOpenPage={onOpenPage} updateTable={updateTable} />;
}

interface Editing {
  id: number;
  field: "text" | "note";
  value: string;
}

function ReviewTable({
  table,
  doc,
  onOpenPage,
  updateTable,
}: {
  table: RequirementsTable;
  doc: DocumentInfo;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
  updateTable: TableUpdate;
}) {
  const { t, i18n } = useTranslation();
  const errorMessage = useErrorMessage();
  const filterGroupId = useId();
  const exportId = useId();
  const helpId = useId();
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [exportScope, setExportScope] = useState<ExportScope>("accepted");
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>());
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const pendingFocus = useRef<number | null>(null);

  const rows = table.requirements;
  const visible = filterRows(rows, filter, flaggedOnly);
  const counts = countRows(rows);
  const activeId = visible.some((r) => r.id === focusedId) ? focusedId : (visible[0]?.id ?? null);
  const visibleSelected = visible.filter((r) => selected.has(r.id));
  const statusLabel = (status: ReviewStatus) => t(`requirements.review.statuses.${status}`);

  // Move DOM focus once the row it should go to has been rendered.
  useLayoutEffect(() => {
    const id = pendingFocus.current;
    if (id === null) return;
    const element = rowRefs.current.get(id);
    if (element) {
      pendingFocus.current = null;
      element.focus();
    }
  });

  const editingKey = editing ? `${editing.id}:${editing.field}` : null;
  useEffect(() => {
    if (editingKey) editorRef.current?.focus();
  }, [editingKey]);

  const focusRow = (id: number | null) => {
    if (id === null) return;
    setFocusedId(id);
    pendingFocus.current = id;
  };

  /** Show the change at once, send it, and undo it if the request fails. */
  const commit = async (ids: number[], patch: ReviewPatch, message: string) => {
    if (ids.length === 0) return;
    const previous = rows.filter((r) => ids.includes(r.id));
    updateTable((current) => ({
      ...current,
      requirements: applyPatch(current.requirements, ids, patch, new Date()),
    }));
    setAnnouncement(message);
    try {
      if (ids.length === 1) {
        const updated = await api.updateRequirement(ids[0], patch);
        updateTable((current) => ({ ...current, requirements: replaceRows(current.requirements, [updated]) }));
      } else {
        const result = await api.reviewRequirements(doc.id, ids, patch.status ?? "proposed");
        updateTable((current) => ({
          ...current,
          requirements: replaceRows(current.requirements, result.requirements),
        }));
      }
      setReviewError(null);
    } catch (e) {
      updateTable((current) => ({ ...current, requirements: restoreRows(current.requirements, previous) }));
      const text = t("requirements.review.failed", { message: errorMessage(e) });
      setReviewError(text);
      setAnnouncement(text);
    }
  };

  const setStatus = (row: Requirement, status: ReviewStatus) => {
    // When the row leaves the current filter, focus moves on to its neighbour.
    if (!matchesFilter({ status, not_found: row.not_found }, filter)) {
      const index = visible.findIndex((r) => r.id === row.id);
      const next = visible[index + 1] ?? visible[index - 1];
      focusRow(next ? next.id : null);
    }
    void commit(
      [row.id],
      { status },
      t("requirements.review.announce", { code: row.code, status: statusLabel(status) }),
    );
  };

  const bulkStatus = (status: ReviewStatus) => {
    const ids = visibleSelected.map((r) => r.id);
    if (ids.length === 0) return;
    setSelected(new Set());
    void commit(
      ids,
      { status },
      t("requirements.review.announceBulk", { count: ids.length, status: statusLabel(status) }),
    );
  };

  const startEdit = (row: Requirement, field: Editing["field"]) => {
    setFocusedId(row.id);
    setEditError(null);
    setEditing({
      id: row.id,
      field,
      value: field === "text" ? (row.edited_text ?? row.requirement_text) : (row.reviewer_note ?? ""),
    });
  };

  const cancelEdit = () => {
    if (!editing) return;
    const id = editing.id;
    setEditing(null);
    setEditError(null);
    focusRow(id);
  };

  const saveEdit = () => {
    if (!editing) return;
    const row = rows.find((r) => r.id === editing.id);
    if (!row) return;
    const value = editing.value.trim();
    let patch: ReviewPatch;
    if (editing.field === "text") {
      if (!value) {
        setEditError(t("requirements.review.emptyText"));
        return;
      }
      // Writing the original text back is the same as having no edit.
      patch = { edited_text: value === (row.paraphrase || row.quote) ? null : value };
    } else {
      patch = { reviewer_note: value || null };
    }
    setEditing(null);
    setEditError(null);
    focusRow(row.id);
    void commit([row.id], patch, t("requirements.review.announceSaved", { code: row.code }));
  };

  const toggleSelected = (id: number) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAllVisible = () =>
    setSelected((current) => {
      const next = new Set(current);
      const all = visible.length > 0 && visible.every((r) => next.has(r.id));
      for (const row of visible) {
        if (all) next.delete(row.id);
        else next.add(row.id);
      }
      return next;
    });

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const action = reviewKeyAction({
      key: event.key,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      target: event.target as HTMLElement,
    });
    if (!action) return;
    if (action.type === "cancel") {
      if (editing) {
        event.preventDefault();
        cancelEdit();
      } else if (helpOpen) {
        event.preventDefault();
        setHelpOpen(false);
      }
      return;
    }
    if (action.type === "help") {
      event.preventDefault();
      setHelpOpen((open) => !open);
      return;
    }
    const index = visible.findIndex((r) => r.id === activeId);
    if (action.type === "move") {
      event.preventDefault();
      const next = moveIndex(index, action.delta, visible.length);
      focusRow(next >= 0 ? visible[next].id : null);
      return;
    }
    const row = index >= 0 ? visible[index] : null;
    if (!row) return;
    event.preventDefault();
    if (action.type === "status") setStatus(row, action.status);
    else if (action.type === "edit") startEdit(row, "text");
    else if (action.type === "note") startEdit(row, "note");
    else if (action.type === "select") toggleSelected(row.id);
  };

  const reasons = [
    table.rejected_quote_not_found > 0 && t("requirements.rejectedQuote", { count: table.rejected_quote_not_found }),
    table.rejected_not_requirement > 0 &&
      t("requirements.rejectedNotRequirement", { count: table.rejected_not_requirement }),
    table.skipped_sections > 0 && t("requirements.skipped", { count: table.skipped_sections }),
    table.elapsed_seconds !== null && t("requirements.took", { time: formatDuration(table.elapsed_seconds) }),
  ].filter(Boolean);
  const merged = table.kept + table.added + table.no_longer_found > 0;
  const showParaphrase = rows.some((req) => req.paraphrase);
  const allVisibleSelected = visible.length > 0 && visibleSelected.length === visible.length;
  const dateFormat = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" });

  return (
    <>
      <p className="visually-hidden" aria-live="polite" role="status">
        {announcement}
      </p>
      <div className="requirements-summary">
        <p>
          <strong>{rows.length > 0 ? t("requirements.count", { count: rows.length }) : t("requirements.none")}</strong>
          {reasons.map((reason) => (
            <span key={String(reason)} className="muted">
              {" · "}
              {reason}
            </span>
          ))}
        </p>
        {rows.length > 0 && (
          <p className="downloads">
            <label htmlFor={exportId}>{t("requirements.review.exportLabel")}:</label>
            <select
              id={exportId}
              value={exportScope}
              onChange={(event) => setExportScope(event.target.value as ExportScope)}
            >
              <option value="accepted">{t("requirements.review.exportAccepted")}</option>
              <option value="all">{t("requirements.review.exportAll")}</option>
            </select>
            <a href={exportUrl(doc.id, "csv", exportScope)} download>
              {t("requirements.csv")}
            </a>
            <a href={exportUrl(doc.id, "xlsx", exportScope)} download>
              {t("requirements.excel")}
            </a>
          </p>
        )}
      </div>
      {rows.length > 0 && (
        <p className="review-summary">
          {t("requirements.review.summary", {
            total: counts.all,
            proposed: counts.proposed,
            accepted: counts.accepted,
            rejected: counts.rejected,
          })}
          {counts.not_found > 0 && ` · ${t("requirements.review.summaryNotFound", { count: counts.not_found })}`}
          {counts.flagged > 0 && ` · ${t("requirements.flags.summary", { count: counts.flagged })}`}
          {merged && (
            <span className="muted">
              {" · "}
              {t("requirements.review.merge", {
                kept: table.kept,
                added: table.added,
                notFound: table.no_longer_found,
              })}
            </span>
          )}
        </p>
      )}
      {reviewError && (
        <p className="inline-error" role="alert">
          {reviewError}
        </p>
      )}
      {rows.length > 0 && (
        <>
          <div className="review-toolbar">
            <div className="review-filters" role="group" aria-labelledby={filterGroupId}>
              <span id={filterGroupId} className="review-toolbar-label">
                {t("requirements.review.filterLabel")}:
              </span>
              {STATUS_FILTERS.map((value) => (
                <button
                  key={value}
                  type="button"
                  className="filter-chip"
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                >
                  {t(`requirements.review.filters.${value}`)} <span className="filter-count">{counts[value]}</span>
                </button>
              ))}
              <button
                type="button"
                className="filter-chip flag-filter"
                aria-pressed={flaggedOnly}
                onClick={() => setFlaggedOnly((on) => !on)}
              >
                <span aria-hidden="true">⚑</span> {t("requirements.flags.filter")}{" "}
                <span className="filter-count">{counts.flagged}</span>
              </button>
            </div>
            <div className="review-bulk">
              <label className="select-visible">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  ref={(element) => {
                    if (element) element.indeterminate = visibleSelected.length > 0 && !allVisibleSelected;
                  }}
                  onChange={toggleAllVisible}
                  disabled={visible.length === 0}
                />
                {t("requirements.review.selectVisible")}
              </label>
              <span className="muted">{t("requirements.review.selected", { count: visibleSelected.length })}</span>
              <button
                type="button"
                className="quiet review-button"
                onClick={() => bulkStatus("accepted")}
                disabled={visibleSelected.length === 0}
              >
                {t("requirements.review.acceptSelected")}
              </button>
              <button
                type="button"
                className="quiet review-button"
                onClick={() => bulkStatus("rejected")}
                disabled={visibleSelected.length === 0}
              >
                {t("requirements.review.rejectSelected")}
              </button>
              <button
                type="button"
                className="quiet review-button shortcuts-button"
                aria-expanded={helpOpen}
                aria-controls={helpId}
                onClick={() => setHelpOpen((open) => !open)}
                title={t("requirements.review.shortcutsButton")}
              >
                ?<span className="visually-hidden">{t("requirements.review.shortcutsButton")}</span>
              </button>
            </div>
          </div>
          {helpOpen && <ShortcutList id={helpId} onClose={() => setHelpOpen(false)} />}
          {showParaphrase && <p className="muted paraphrase-note">{t("requirements.paraphraseNote")}</p>}
          <div className="table-scroll" onKeyDown={onKeyDown}>
            {visible.length === 0 ? (
              <p className="muted state-box">{t("requirements.review.emptyFilter")}</p>
            ) : (
              <table className="requirements-table">
                <colgroup>
                  <col className="col-select" />
                  <col className="col-id" />
                  <col className="col-kind" />
                  <col className="col-quote" />
                  <col className="col-page" />
                  <col className="col-review" />
                  <col className="col-status" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="visually-hidden">{t("requirements.columns.select")}</span>
                    </th>
                    <th scope="col">{t("requirements.columns.id")}</th>
                    <th scope="col">{t("requirements.columns.kind")}</th>
                    <th scope="col">{t("requirements.columns.requirement")}</th>
                    <th scope="col">{t("requirements.columns.page")}</th>
                    <th scope="col">{t("requirements.columns.review")}</th>
                    <th scope="col">{t("requirements.columns.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((req) => (
                    <ReviewRow
                      key={req.id}
                      req={req}
                      doc={doc}
                      active={req.id === activeId}
                      selected={selected.has(req.id)}
                      editing={editing && editing.id === req.id ? editing : null}
                      editError={editing && editing.id === req.id ? editError : null}
                      dateFormat={dateFormat}
                      rowRef={(element) => {
                        if (element) rowRefs.current.set(req.id, element);
                        else rowRefs.current.delete(req.id);
                      }}
                      editorRef={editorRef}
                      onFocus={() => setFocusedId(req.id)}
                      onToggleSelected={() => toggleSelected(req.id)}
                      onStatus={(status) => setStatus(req, status)}
                      onStartEdit={(field) => startEdit(req, field)}
                      onEditChange={(value) => setEditing((current) => (current ? { ...current, value } : current))}
                      onSaveEdit={saveEdit}
                      onCancelEdit={cancelEdit}
                      onRemoveEdit={() =>
                        void commit(
                          [req.id],
                          { edited_text: null },
                          t("requirements.review.announceSaved", { code: req.code }),
                        )
                      }
                      onOpenPage={onOpenPage}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </>
  );
}

function ReviewRow({
  req,
  doc,
  active,
  selected,
  editing,
  editError,
  dateFormat,
  rowRef,
  editorRef,
  onFocus,
  onToggleSelected,
  onStatus,
  onStartEdit,
  onEditChange,
  onSaveEdit,
  onCancelEdit,
  onRemoveEdit,
  onOpenPage,
}: {
  req: Requirement;
  doc: DocumentInfo;
  active: boolean;
  selected: boolean;
  editing: Editing | null;
  editError: string | null;
  dateFormat: Intl.DateTimeFormat;
  rowRef: (element: HTMLTableRowElement | null) => void;
  editorRef: RefObject<HTMLTextAreaElement | null>;
  onFocus: () => void;
  onToggleSelected: () => void;
  onStatus: (status: ReviewStatus) => void;
  onStartEdit: (field: Editing["field"]) => void;
  onEditChange: (value: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onRemoveEdit: () => void;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
}) {
  const { t } = useTranslation();
  const statusText = t(`requirements.review.statuses.${req.status}`);
  const editorLabel =
    editing?.field === "note"
      ? t("requirements.review.noteLabel", { code: req.code })
      : t("requirements.review.editLabel", { code: req.code });

  const editor = editing && (
    <div className="review-editor">
      <label className="visually-hidden" htmlFor={`editor-${req.id}`}>
        {editorLabel}
      </label>
      <textarea
        id={`editor-${req.id}`}
        ref={editorRef}
        value={editing.value}
        rows={editing.field === "text" ? 3 : 2}
        aria-invalid={editError ? true : undefined}
        onChange={(event) => onEditChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            onSaveEdit();
          }
        }}
      />
      {editError && (
        <p className="inline-error" role="alert">
          {editError}
        </p>
      )}
      <div className="review-actions">
        <button type="button" className="review-button" onClick={onSaveEdit}>
          {t("requirements.review.save")}
        </button>
        <button type="button" className="quiet review-button" onClick={onCancelEdit}>
          {t("requirements.review.cancel")}
        </button>
      </div>
    </div>
  );

  return (
    <tr
      ref={rowRef}
      tabIndex={active ? 0 : -1}
      className={`review-row review-${req.status}${selected ? " is-selected" : ""}${req.not_found ? " is-not-found" : ""}`}
      aria-selected={selected}
      onFocus={onFocus}
    >
      <td className="cell-select" data-label={t("requirements.columns.select")}>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelected}
          aria-label={t("requirements.review.select", { code: req.code })}
        />
      </td>
      <td className="mono cell-id" data-label={t("requirements.columns.id")}>
        {req.code}
      </td>
      <td className="cell-kind" data-label={t("requirements.columns.kind")}>
        <span className={`kind-badge kind-${req.kind}`}>{t(`requirements.kinds.${req.kind}`)}</span>
        <span className="type-label">{t(`requirements.types.${req.type}`, { defaultValue: req.type })}</span>
      </td>
      <td className="cell-quote" data-label={t("requirements.columns.requirement")}>
        {editing?.field === "text" ? (
          editor
        ) : req.edited_text !== null ? (
          <p className="edited-text">
            <span className="edited-tag">{t("requirements.review.edited")}</span>{" "}
            <FlagWords text={req.edited_text} flags={req.flags} source="text" />
          </p>
        ) : null}
        {(req.edited_text !== null || editing?.field === "text") && (
          <p className="original-label">{t("requirements.review.originalQuote")}</p>
        )}
        <q className="quote">
          <FlagWords text={req.quote} flags={req.flags} source="quote" />
        </q>
        {req.paraphrase && (
          <p className="cell-paraphrase">
            {t("requirements.columns.paraphrase")}:{" "}
            {req.edited_text === null ? (
              <FlagWords text={req.paraphrase} flags={req.flags} source="text" />
            ) : (
              req.paraphrase
            )}
          </p>
        )}
        {req.flags.length > 0 && (
          <div className="flag-list">
            {req.flags.map((flag) => (
              <FlagBadge key={`${flag.code}-${flag.source}`} flag={flag} />
            ))}
          </div>
        )}
        {editing?.field === "note"
          ? editor
          : req.reviewer_note && (
              <p className="review-note">
                <span className="note-label">{t("requirements.review.note")}:</span> {req.reviewer_note}
              </p>
            )}
      </td>
      <td className="cell-page" data-label={t("requirements.columns.page")}>
        <button
          type="button"
          className="highlight-tag"
          aria-label={t("requirements.openPage", { page: req.page })}
          onClick={() => onOpenPage(doc, req.page, req.quote)}
        >
          {t("requirements.pageChip", { page: req.page })}
        </button>
      </td>
      <td className="cell-review" data-label={t("requirements.columns.review")}>
        <span
          className={`review-tag review-tag-${req.status}`}
          title={
            req.reviewed_at
              ? t("requirements.review.reviewedAt", { time: formatTime(req.reviewed_at, dateFormat) })
              : undefined
          }
        >
          {statusText}
        </span>
        {req.not_found && (
          <span className="not-found-tag" title={t("requirements.review.notFoundHelp")}>
            {t("requirements.review.notFoundTag")}
          </span>
        )}
        <div className="review-actions">
          <button
            type="button"
            className="quiet review-button"
            aria-pressed={req.status === "accepted"}
            onClick={() => onStatus("accepted")}
          >
            {t("requirements.review.accept")}
          </button>
          <button
            type="button"
            className="quiet review-button"
            aria-pressed={req.status === "rejected"}
            onClick={() => onStatus("rejected")}
          >
            {t("requirements.review.reject")}
          </button>
          <button
            type="button"
            className="quiet review-button"
            onClick={() => onStartEdit("text")}
            disabled={editing !== null}
          >
            {t("requirements.review.edit")}
          </button>
          <button
            type="button"
            className="quiet review-button"
            onClick={() => onStartEdit("note")}
            disabled={editing !== null}
          >
            {t("requirements.review.note")}
          </button>
          {req.edited_text !== null && editing === null && (
            <button type="button" className="quiet review-button" onClick={onRemoveEdit}>
              {t("requirements.review.removeEdit")}
            </button>
          )}
          {req.status !== "proposed" && (
            <button
              type="button"
              className="quiet review-button"
              aria-label={t("requirements.review.resetLabel", { code: req.code })}
              onClick={() => onStatus("proposed")}
            >
              {t("requirements.review.reset")}
            </button>
          )}
        </div>
      </td>
      <td className="cell-status" data-label={t("requirements.columns.status")}>
        {req.verified && <span className="stamp">{t("requirements.verified")}</span>}
      </td>
    </tr>
  );
}

/** `text` with the words that set the flags from `source` marked. */
function FlagWords({
  text,
  flags,
  source,
}: {
  text: string;
  flags: RequirementFlag[];
  source: RequirementFlag["source"];
}) {
  const spans = flags.filter((flag) => flag.source === source).flatMap((flag) => flag.words);
  if (spans.length === 0) return <>{text}</>;
  return (
    <>
      {markSpans(text, spans).map((piece, index) =>
        piece.marked ? (
          <mark key={index} className="flag-word">
            {piece.text}
          </mark>
        ) : (
          <span key={index}>{piece.text}</span>
        ),
      )}
    </>
  );
}

/** A flag marker whose explanation opens on hover, on keyboard focus and on a tap or click. */
function FlagBadge({ flag }: { flag: RequirementFlag }) {
  const { t } = useTranslation();
  const tipId = useId();
  const [open, setOpen] = useState(false);
  const words = flag.words.map((word) => `“${word.text}”`).join(", ");
  return (
    // Hover opens it only for a real mouse: a tap also fires emulated mouse events, which
    // would open it just before the click toggles it shut again.
    <span
      className="flag"
      onPointerEnter={(event) => event.pointerType === "mouse" && setOpen(true)}
      onPointerLeave={(event) => event.pointerType === "mouse" && setOpen(false)}
    >
      <button
        type="button"
        className="flag-badge"
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        aria-label={t("requirements.flags.badgeLabel", { flag: t(`requirements.flags.${flag.code}.label`) })}
        onClick={() => setOpen((value) => !value)}
        // Keyboard focus opens the explanation; a mouse click or a tap toggles it instead.
        onFocus={(event) => event.currentTarget.matches(":focus-visible") && setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        <span aria-hidden="true">⚑</span> {t(`requirements.flags.${flag.code}.label`)}
      </button>
      {open && (
        <span role="tooltip" id={tipId} className="flag-tip">
          {t(`requirements.flags.${flag.code}.explanation`)}{" "}
          <span className="flag-tip-words">{t(`requirements.flags.source.${flag.source}`, { words })}</span>
        </span>
      )}
    </span>
  );
}

const SHORTCUTS: [string, string][] = [
  ["↑ ↓ / K J", "move"],
  ["A", "accept"],
  ["R", "reject"],
  ["E", "edit"],
  ["N", "note"],
  ["U", "reset"],
  ["Space", "select"],
  ["Esc", "cancel"],
  ["?", "help"],
];

function ShortcutList({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <section id={id} className="shortcut-list" aria-label={t("requirements.review.shortcuts")}>
      <div className="shortcut-head">
        <h3>{t("requirements.review.shortcuts")}</h3>
        <button type="button" className="quiet review-button" onClick={onClose}>
          {t("requirements.review.close")}
        </button>
      </div>
      <p className="muted">{t("requirements.review.shortcutsHint")}</p>
      <dl>
        {SHORTCUTS.map(([keys, action]) => (
          <div key={action}>
            <dt>
              <kbd>{keys}</kbd>
            </dt>
            <dd>{t(`requirements.review.keys.${action}`)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function formatTime(iso: string, format: Intl.DateTimeFormat): string {
  // The API sends UTC without a zone suffix.
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? iso : format.format(date);
}

/** "42 s", "3 min 05 s" */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
}
