import { lazy, Suspense, useState } from "react";

import type { DocumentInfo, Source } from "./api";
import { Cabinet } from "./components/Cabinet";
import { Header } from "./components/Header";
import { Notepad } from "./components/Notepad";
import type { ViewerTarget } from "./components/PdfPane";
import { useCabinet, useHealth } from "./hooks";

// pdf.js is large; load the viewer only when a page is first opened.
const PdfPane = lazy(() => import("./components/PdfPane").then((m) => ({ default: m.PdfPane })));

export function App() {
  const cabinet = useCabinet();
  const { health, reachable } = useHealth();
  const [openDrawerId, setOpenDrawerId] = useState<number | null>(null);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);

  const drawers = cabinet.drawers ?? [];
  const openDrawer = drawers.find((d) => d.id === openDrawerId) ?? null;

  const openPage = (doc: DocumentInfo, page: number, highlight: string | null = null) =>
    setViewer({ docId: doc.id, filename: doc.filename, page, highlight });
  const openSource = (source: Source) =>
    setViewer({ docId: source.doc_id, filename: source.filename, page: source.page, highlight: source.highlight });

  return (
    <div className="desk">
      <Header health={health} apiReachable={reachable} />
      <div className={`workspace${viewer ? " has-viewer" : ""}`}>
        <Cabinet
          cabinet={cabinet}
          openDrawerId={openDrawerId}
          onOpenDrawer={setOpenDrawerId}
          onOpenDocument={(doc) => openPage(doc, 1)}
        />
        <Notepad
          drawers={drawers}
          documents={cabinet.documents}
          openDrawer={openDrawer}
          onOpenSource={openSource}
          onOpenPage={openPage}
        />
        {viewer && (
          <Suspense fallback={<aside className="viewer" />}>
            <PdfPane target={viewer} onClose={() => setViewer(null)} />
          </Suspense>
        )}
      </div>
    </div>
  );
}
