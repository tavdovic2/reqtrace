import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { fileUrl } from "../api";
import { locatePassage, renderItem, type ItemRanges } from "../highlight";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export interface ViewerTarget {
  docId: number;
  filename: string;
  page: number;
  /** Evidence passage to mark on the cited page, as it appears in the page text. */
  highlight: string | null;
}

interface PdfPaneProps {
  target: ViewerTarget;
  onClose: () => void;
}

/** Width of an element, kept up to date as the layout changes. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** The text items of one rendered page, kept so a new passage can be marked without reloading. */
interface PageText {
  key: string;
  texts: string[];
}

export function PdfPane({ target, onClose }: PdfPaneProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(target.page);
  const [shownTarget, setShownTarget] = useState(target);
  // The page count belongs to one document; a stale count from the previous one is ignored.
  const [loaded, setLoaded] = useState<{ docId: number; pages: number } | null>(null);
  const [pageText, setPageText] = useState<PageText | null>(null);
  const [frameRef, width] = useWidth<HTMLDivElement>();

  // Jump to the cited page when a new source is opened. This is done while rendering rather
  // than in an effect: react-pdf suspends while a page loads, and React re-runs effects when a
  // suspended tree is shown again, which would reset the page after every "next".
  if (target !== shownTarget) {
    setShownTarget(target);
    setPage(target.page);
  }

  const pageCount = loaded?.docId === target.docId ? loaded.pages : 0;
  // The passage is marked only on the page it was cited from.
  const passage = page === target.page ? target.highlight : null;
  // The page's text is loaded once per page; the marks are recomputed whenever the passage
  // changes. (Computing them only when the text loads missed a second chip on the same page.)
  const textKey = `${target.docId}:${page}`;
  const texts = pageText?.key === textKey ? pageText.texts : null;
  const ranges: ItemRanges | null = useMemo(
    () => (texts && passage ? locatePassage(texts, passage) : null),
    [texts, passage],
  );

  const renderText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) => renderItem(str, ranges?.get(itemIndex)),
    [ranges],
  );

  const scrollToMark = () => {
    frameRef.current?.querySelector(".evidence-mark")?.scrollIntoView({ block: "center" });
  };

  return (
    <aside className="viewer" aria-labelledby="viewer-title">
      <div className="viewer-header">
        <h2 id="viewer-title" className="column-title">
          {t("viewer.title")}
        </h2>
        <button type="button" className="quiet" onClick={onClose} aria-label={t("viewer.close")}>
          ×
        </button>
      </div>
      <div className="viewer-paper">
        <p className="viewer-file">
          <a href={fileUrl(target.docId)} target="_blank" rel="noreferrer" title={t("viewer.openFile")}>
            {target.filename}
          </a>
        </p>
        <div ref={frameRef} className="viewer-frame">
          <Suspense fallback={<p role="status">{t("viewer.loading")}</p>}>
            <Document
              file={fileUrl(target.docId)}
              onLoadSuccess={({ numPages }) => setLoaded({ docId: target.docId, pages: numPages })}
              loading={<p role="status">{t("viewer.loading")}</p>}
              error={
                <p className="inline-error" role="alert">
                  {t("viewer.error")}
                </p>
              }
            >
              <div key={`${target.docId}-${page}`} className="page-sheet">
                <Page
                  pageNumber={page}
                  width={width > 0 ? width - 16 : undefined}
                  loading={<p role="status">{t("viewer.loading")}</p>}
                  onGetTextSuccess={({ items }) =>
                    setPageText({ key: textKey, texts: items.map((item) => ("str" in item ? item.str : "")) })
                  }
                  customTextRenderer={renderText}
                  onRenderTextLayerSuccess={scrollToMark}
                />
              </div>
            </Document>
          </Suspense>
        </div>
        {pageCount > 0 && (
          <nav className="pager" aria-label={t("viewer.title")}>
            <button type="button" onClick={() => setPage((p) => p - 1)} disabled={page <= 1}>
              ← <span className="visually-hidden">{t("viewer.previous")}</span>
            </button>
            <span aria-live="polite">{t("viewer.pageOf", { page, total: pageCount })}</span>
            <button type="button" onClick={() => setPage((p) => p + 1)} disabled={page >= pageCount}>
              <span className="visually-hidden">{t("viewer.next")}</span> →
            </button>
          </nav>
        )}
      </div>
    </aside>
  );
}
