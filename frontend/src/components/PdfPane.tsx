import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { fileUrl } from "../api";
import { locatePassages, renderItem, type PageItem, type PassageMatches } from "../highlight";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export interface ViewerTarget {
  docId: number;
  filename: string;
  page: number;
  /** Evidence passages to mark on the cited page, as they appear in the page text. */
  highlights: string[];
}

/** Space left above a passage scrolled into view, in pixels. */
const SCROLL_MARGIN = 48;

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

/** The text items of one rendered page, kept so new passages can be marked without reloading. */
interface PageText {
  key: string;
  items: PageItem[];
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** Scroll the first mark of `passage` into view inside `frame` (or the window when the
 *  frame does not scroll, as in the narrow layout), with some room above it. */
function scrollToPassage(frame: HTMLElement | null, passage: number) {
  const mark = frame?.querySelector<HTMLElement>(`mark.evidence-mark[data-passage="${passage}"]`);
  if (!frame || !mark) return;
  const behavior: ScrollBehavior = reducedMotion() ? "auto" : "smooth";
  const offset = mark.getBoundingClientRect().top - frame.getBoundingClientRect().top;
  if (frame.scrollHeight > frame.clientHeight + 1) {
    frame.scrollTo({ top: frame.scrollTop + offset - SCROLL_MARGIN, behavior });
  } else {
    window.scrollBy({ top: mark.getBoundingClientRect().top - SCROLL_MARGIN, behavior });
  }
}

export function PdfPane({ target, onClose }: PdfPaneProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(target.page);
  const [shownTarget, setShownTarget] = useState(target);
  // The page count belongs to one document; a stale count from the previous one is ignored.
  const [loaded, setLoaded] = useState<{ docId: number; pages: number } | null>(null);
  const [pageText, setPageText] = useState<PageText | null>(null);
  // Which of the found passages is current (index into matches.found).
  const [current, setCurrent] = useState(0);
  const [frameRef, width] = useWidth<HTMLDivElement>();

  // Jump to the cited page when a new source is opened. This is done while rendering rather
  // than in an effect: react-pdf suspends while a page loads, and React re-runs effects when a
  // suspended tree is shown again, which would reset the page after every "next".
  if (target !== shownTarget) {
    setShownTarget(target);
    setPage(target.page);
    setCurrent(0);
  }

  const pageCount = loaded?.docId === target.docId ? loaded.pages : 0;
  // Passages are marked only on the page they were cited from.
  const passages = page === target.page ? target.highlights : null;
  // The page's text is loaded once per page; the marks are recomputed whenever the passages
  // change. (Computing them only when the text loads missed a second chip on the same page.)
  const textKey = `${target.docId}:${page}`;
  const items = pageText?.key === textKey ? pageText.items : null;
  const matches: PassageMatches | null = useMemo(
    () => (items && passages && passages.length > 0 ? locatePassages(items, passages) : null),
    [items, passages],
  );
  const found = matches?.found ?? [];
  const currentPassage = found.length > 0 ? found[Math.min(current, found.length - 1)] : -1;

  const renderText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) =>
      renderItem(str, matches?.ranges.get(itemIndex), currentPassage),
    [matches, currentPassage],
  );

  // Runs after every text-layer render: a new page, new passages, a resize, another passage.
  const showCurrent = () => scrollToPassage(frameRef.current, currentPassage);
  const step = (delta: number) => setCurrent((index) => (index + delta + found.length) % found.length);

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
                  onGetTextSuccess={({ items: content }) =>
                    setPageText({
                      key: textKey,
                      items: content.map((item) =>
                        "str" in item ? { str: item.str, y: item.transform[5], height: item.height } : { str: "" },
                      ),
                    })
                  }
                  customTextRenderer={renderText}
                  onRenderTextLayerSuccess={showCurrent}
                />
              </div>
            </Document>
          </Suspense>
        </div>
        {found.length > 1 && (
          <nav className="pager passage-pager" aria-label={t("viewer.passages")}>
            <button type="button" onClick={() => step(-1)}>
              ↑ <span className="visually-hidden">{t("viewer.previousPassage")}</span>
            </button>
            <span aria-live="polite">
              {t("viewer.passageOf", { current: Math.min(current, found.length - 1) + 1, total: found.length })}
            </span>
            <button type="button" onClick={() => step(1)}>
              <span className="visually-hidden">{t("viewer.nextPassage")}</span> ↓
            </button>
          </nav>
        )}
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
