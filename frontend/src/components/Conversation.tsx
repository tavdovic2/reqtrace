import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, type AnswerLine, type AskDebug, type AskResponse, type Drawer, type Source } from "../api";
import { useErrorMessage } from "../hooks";

type Scope = "drawer" | "cabinet";

interface Exchange {
  id: number;
  question: string;
  scopeName: string | null;
  response?: AskResponse;
  error?: string;
}

interface ConversationProps {
  openDrawer: Drawer | null;
  onOpenSource: (source: Source) => void;
}

export function Conversation({ openDrawer, onOpenSource }: ConversationProps) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const [scope, setScope] = useState<Scope>("cabinet");
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [pending, setPending] = useState(false);
  const nextId = useRef(1);
  const logEnd = useRef<HTMLDivElement>(null);

  const effectiveScope: Scope = scope === "drawer" && openDrawer ? "drawer" : "cabinet";

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: "end" });
  }, [history, pending]);

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || pending) return;
    const drawer = effectiveScope === "drawer" ? openDrawer : null;
    const id = nextId.current++;
    setHistory((h) => [...h, { id, question: text, scopeName: drawer?.name ?? null }]);
    setQuestion("");
    setPending(true);
    try {
      const response = await api.ask(text, drawer?.id ?? null);
      setHistory((h) => h.map((x) => (x.id === id ? { ...x, response } : x)));
    } catch (e) {
      const message = t("chat.error", { message: errorMessage(e) });
      setHistory((h) => h.map((x) => (x.id === id ? { ...x, error: message } : x)));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="conversation">
      <fieldset className="scope-switch">
        <legend>{t("chat.scope")}</legend>
        <input
          type="radio"
          id="scope-drawer"
          name="scope"
          value="drawer"
          checked={effectiveScope === "drawer"}
          disabled={!openDrawer}
          onChange={() => setScope("drawer")}
        />
        <label htmlFor="scope-drawer">{t("chat.thisDrawer")}</label>
        <input
          type="radio"
          id="scope-cabinet"
          name="scope"
          value="cabinet"
          checked={effectiveScope === "cabinet"}
          onChange={() => setScope("cabinet")}
        />
        <label htmlFor="scope-cabinet">{t("chat.wholeCabinet")}</label>
      </fieldset>
      <p className="scope-hint muted">
        {openDrawer
          ? effectiveScope === "drawer"
            ? t("chat.searchingIn", { name: openDrawer.name })
            : null
          : t("chat.openDrawerHint")}
      </p>

      <div className="chat-log" aria-live="polite">
        {history.length === 0 && <p className="muted chat-empty">{t("chat.empty")}</p>}
        {history.map((exchange) => (
          <ExchangeView key={exchange.id} exchange={exchange} onOpenSource={onOpenSource} />
        ))}
        {pending && (
          <p className="thinking" role="status">
            {t("chat.thinking")}
          </p>
        )}
        <div ref={logEnd} />
      </div>

      <form className="ask-form" onSubmit={ask}>
        <label htmlFor="question-input" className="visually-hidden">
          {t("chat.label")}
        </label>
        <input
          id="question-input"
          type="text"
          autoComplete="off"
          maxLength={2000}
          placeholder={t("chat.placeholder")}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={pending || !question.trim()}>
          {t("chat.send")}
        </button>
      </form>
    </div>
  );
}

function ExchangeView({ exchange, onOpenSource }: { exchange: Exchange; onOpenSource: (s: Source) => void }) {
  const { t } = useTranslation();
  const { response } = exchange;

  return (
    <article className="exchange">
      <p className="question">
        <span className="qa-mark" aria-hidden="true">
          {t("chat.question")}
        </span>
        {exchange.question}
      </p>
      {exchange.error && (
        <p className="inline-error" role="alert">
          {exchange.error}
        </p>
      )}
      {response && (
        <div className="answer">
          <span className="qa-mark" aria-hidden="true">
            {t("chat.answer")}
          </span>
          {response.answered ? (
            <ul className="answer-lines">
              {answerLines(response).map((line, index) => (
                <li key={index}>
                  <p>{line.text}</p>
                  <ul className="source-tags" aria-label={t("chat.sources")}>
                    {line.sources.map((sourceIndex) => {
                      const source = response.sources[sourceIndex];
                      return (
                        <li key={`${source.doc_id}-${source.page}`}>
                          <button
                            type="button"
                            className="highlight-tag"
                            title={source.snippet}
                            aria-label={t("chat.openSource", { filename: source.filename, page: source.page })}
                            onClick={() => onOpenSource(source)}
                          >
                            {t("chat.sourceTag", { filename: source.filename, page: source.page })}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>
          ) : (
            <>
              <p className="not-found">{t("chat.notFound")}</p>
              {response.debug && <WhyNotFound debug={response.debug} />}
            </>
          )}
        </div>
      )}
    </article>
  );
}

/** The searched phrasings, the retrieved passages and the claims verification dropped. */
function WhyNotFound({ debug }: { debug: AskDebug }) {
  const { t } = useTranslation();
  return (
    <details className="why">
      <summary>{t("why.toggle")}</summary>
      <p className="muted">{debug.model_answered ? t("why.modelAnswered") : t("why.modelRefused")}</p>
      {debug.discarded.length > 0 && (
        <>
          <h4>{t("why.discarded")}</h4>
          <ul>
            {debug.discarded.map((claim, index) => (
              <li key={index}>
                <q>{claim.statement}</q>{" "}
                <span className="muted">
                  {claim.code === "number_missing"
                    ? t("why.numberMissing", { numbers: claim.numbers.join(", ") })
                    : t("why.noCitation")}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <h4>{t("why.queries")}</h4>
      <ul>
        {debug.queries.map((query, index) => (
          <li key={index}>{query}</li>
        ))}
      </ul>
      <h4>{t("why.retrieved", { count: debug.retrieved.length })}</h4>
      <ol className="why-chunks">
        {debug.retrieved.map((chunk) => (
          <li key={chunk.rank}>
            <span className="muted">
              {t("why.chunkMeta", {
                filename: chunk.filename,
                page: chunk.page,
                score: chunk.score.toFixed(3),
              })}
            </span>
            <p>{chunk.snippet}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}

/** One line per claim; older responses without lines show the whole answer with all sources. */
function answerLines(response: AskResponse): AnswerLine[] {
  if (response.lines.length > 0) return response.lines;
  return [{ text: response.answer, sources: response.sources.map((_, index) => index) }];
}
