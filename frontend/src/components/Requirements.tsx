import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, exportUrl, type DocumentInfo, type Drawer, type RequirementsTable } from "../api";
import { useErrorMessage } from "../hooks";

const POLL_MS = 2000;

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
      {current && selected && <RequirementsBody table={current} doc={selected} onOpenPage={onOpenPage} />}
    </div>
  );
}

function RequirementsBody({
  table,
  doc,
  onOpenPage,
}: {
  table: RequirementsTable;
  doc: DocumentInfo;
  onOpenPage: (doc: DocumentInfo, page: number, highlight?: string | null) => void;
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

  const reasons = [
    table.rejected_quote_not_found > 0 && t("requirements.rejectedQuote", { count: table.rejected_quote_not_found }),
    table.rejected_not_requirement > 0 &&
      t("requirements.rejectedNotRequirement", { count: table.rejected_not_requirement }),
    table.skipped_sections > 0 && t("requirements.skipped", { count: table.skipped_sections }),
    table.elapsed_seconds !== null && t("requirements.took", { time: formatDuration(table.elapsed_seconds) }),
  ].filter(Boolean);
  const showParaphrase = table.requirements.some((req) => req.paraphrase);

  return (
    <>
      <div className="requirements-summary">
        <p>
          <strong>
            {table.requirements.length > 0
              ? t("requirements.count", { count: table.requirements.length })
              : t("requirements.none")}
          </strong>
          {reasons.map((reason) => (
            <span key={String(reason)} className="muted">
              {" · "}
              {reason}
            </span>
          ))}
        </p>
        {table.requirements.length > 0 && (
          <p className="downloads">
            <span>{t("requirements.download")}:</span>
            <a href={exportUrl(doc.id, "csv")} download>
              {t("requirements.csv")}
            </a>
            <a href={exportUrl(doc.id, "xlsx")} download>
              {t("requirements.excel")}
            </a>
          </p>
        )}
      </div>
      {table.requirements.length > 0 && (
        <>
          {showParaphrase && <p className="muted paraphrase-note">{t("requirements.paraphraseNote")}</p>}
          <div className="table-scroll">
            <table className="requirements-table">
              <colgroup>
                <col className="col-id" />
                <col className="col-kind" />
                <col className="col-quote" />
                <col className="col-page" />
                {showParaphrase && <col className="col-paraphrase" />}
                <col className="col-status" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">{t("requirements.columns.id")}</th>
                  <th scope="col">{t("requirements.columns.kind")}</th>
                  <th scope="col">{t("requirements.columns.quote")}</th>
                  <th scope="col">{t("requirements.columns.page")}</th>
                  {showParaphrase && <th scope="col">{t("requirements.columns.paraphrase")}</th>}
                  <th scope="col">{t("requirements.columns.status")}</th>
                </tr>
              </thead>
              <tbody>
                {table.requirements.map((req) => (
                  <tr key={req.code}>
                    <td className="mono" data-label={t("requirements.columns.id")}>
                      {req.code}
                    </td>
                    <td className="cell-kind" data-label={t("requirements.columns.kind")}>
                      <span className={`kind-badge kind-${req.kind}`}>{t(`requirements.kinds.${req.kind}`)}</span>
                      <span className="type-label">
                        {t(`requirements.types.${req.type}`, { defaultValue: req.type })}
                      </span>
                    </td>
                    <td className="cell-quote" data-label={t("requirements.columns.quote")}>
                      <q className="quote">{req.quote}</q>
                    </td>
                    <td data-label={t("requirements.columns.page")}>
                      <button
                        type="button"
                        className="highlight-tag"
                        aria-label={t("requirements.openPage", { page: req.page })}
                        onClick={() => onOpenPage(doc, req.page, req.quote)}
                      >
                        {t("requirements.pageChip", { page: req.page })}
                      </button>
                    </td>
                    {showParaphrase && (
                      <td className="cell-paraphrase" data-label={t("requirements.columns.paraphrase")}>
                        {req.paraphrase || <span title={t("requirements.paraphraseRemoved")}>—</span>}
                      </td>
                    )}
                    <td data-label={t("requirements.columns.status")}>
                      {req.verified && <span className="stamp">{t("requirements.verified")}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

/** "42 s", "3 min 05 s" */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
}
