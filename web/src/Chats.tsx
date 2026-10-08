import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useRef, useState } from "react";
import { Brand } from "./App";
import { ApiError } from "./api";
import type { ChatApi, ChatDetail, ChatSummary } from "./api";

export function Chats({
  api,
  userId,
  close,
  signIn,
  selected,
  onSelect,
}: {
  api: ChatApi;
  userId: string;
  close: () => void;
  signIn: () => void;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  const [list, setList] = useState<ChatSummary[]>([]);
  const [available, setAvailable] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [chat, setChat] = useState<ChatDetail>();
  const [draft, setDraft] = useState(() =>
    selected
      ? (sessionStorage.getItem(`lexikhan:chat:${userId}:${selected}`) ?? "")
      : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const [refresh, setRefresh] = useState(0);
  const lock = useRef(false);
  const newId = useRef<string | undefined>(undefined);
  const composer = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const key = (id: string) => `lexikhan:chat:${userId}:${id}`;
  const failure = (e: unknown) =>
    setError(
      e instanceof ApiError
        ? e
        : new ApiError(0, "We couldn’t load your chat. Please try again."),
    );

  useEffect(() => {
    let cancelled = false;
    void api
      .chats()
      .then((result) => {
        if (cancelled) return;
        setList(result.chats);
        setAvailable(result.available);
        setLoaded(true);
      })
      .catch((e) => {
        if (!cancelled) failure(e);
      });
    return () => {
      cancelled = true;
    };
  }, [api, refresh]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const result = await api.chat(selected);
        if (cancelled) return;
        setChat(result);
        const pending = sessionStorage.getItem(key(selected) + ":outbox");
        if (pending) {
          const item = JSON.parse(pending) as { id: string; text: string };
          if (result.turns.some((t) => t.messageId === item.id)) {
            sessionStorage.removeItem(key(selected) + ":outbox");
            if (sessionStorage.getItem(key(selected))?.trim() === item.text) {
              sessionStorage.removeItem(key(selected));
            }
            setDraft((current) =>
              current.trim() === item.text ? "" : current,
            );
          }
        }
        if (result.pending) timer = setTimeout(() => void load(), 650);
      } catch (e) {
        if (!cancelled) failure(e);
      }
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, selected, refresh, userId]);

  useEffect(() => {
    if (follow.current) end.current?.scrollIntoView?.({ block: "end" });
  }, [chat]);

  function select(id: string) {
    follow.current = true;
    onSelect(id);
  }
  async function create() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    try {
      newId.current ??= crypto.randomUUID();
      const result = await api.createChat(newId.current);
      newId.current = undefined;
      select(result.chatId);
      setRefresh((n) => n + 1);
    } catch (e) {
      failure(e);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function send() {
    if (!selected || !draft.trim() || lock.current || chat?.pending) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    follow.current = true;
    const text = draft.trim();
    const saved = sessionStorage.getItem(key(selected) + ":outbox");
    const prior = saved
      ? (JSON.parse(saved) as { id: string; text: string })
      : undefined;
    const outbox =
      prior?.text === text ? prior : { id: crypto.randomUUID(), text };
    sessionStorage.setItem(key(selected) + ":outbox", JSON.stringify(outbox));
    try {
      const result = await api.sendMessage(selected, outbox.id, text);
      setChat(result);
      setDraft("");
      sessionStorage.removeItem(key(selected));
      sessionStorage.removeItem(key(selected) + ":outbox");
    } catch (e) {
      failure(e);
    } finally {
      setRefresh((n) => n + 1);
      lock.current = false;
      setBusy(false);
    }
  }
  function edit(text: string) {
    setDraft(text);
    if (selected) sessionStorage.setItem(key(selected), text);
  }
  return (
    <div className="app chat-app">
      <header>
        <Brand />
        <button className="text-button" onClick={close}>
          Back to practice
        </button>
      </header>
      <div className="chat-layout">
        <aside className="chat-sidebar" aria-label="Saved conversations">
          <div className="eyebrow">YOUR CONVERSATIONS</div>
          <button
            className="primary"
            disabled={!available || busy}
            onClick={() => void create()}
          >
            New chat <span aria-hidden="true">+</span>
          </button>
          {!loaded ? (
            <p role="status">Loading conversations…</p>
          ) : !available ? (
            <p className="quiet">Chat isn’t enabled for your account yet.</p>
          ) : list.length === 0 ? (
            <p className="quiet">Your conversations will be saved here.</p>
          ) : null}
          <nav className="chat-list" aria-label="Chat list">
            {list.map((item) => (
              <button
                key={item.chatId}
                disabled={busy}
                aria-current={selected === item.chatId ? "page" : undefined}
                onClick={() => select(item.chatId)}
              >
                <span>{item.title}</span>
                <small>
                  {new Date(item.updatedAt).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </small>
              </button>
            ))}
          </nav>
        </aside>
        <main className="chat-main">
          <div className="chat-heading-label">Language chat</div>
          <h1>
            {chat?.title && chat.turnCount > 0
              ? chat.title
              : "New conversation"}
          </h1>
          {error && (
            <div role="alert" className="notice">
              <p>
                {error.status === 401
                  ? "Sign in again to continue your conversation."
                  : error.status === 404
                    ? "This conversation doesn’t exist or belongs to another account. Choose a saved chat or start a new one."
                    : error.status === 0
                      ? "Connection interrupted. Your draft is saved; reload to check for a reply."
                      : error.message}
              </p>
              <button
                onClick={
                  error.status === 401
                    ? signIn
                    : () => {
                        setError(undefined);
                        setRefresh((n) => n + 1);
                      }
                }
              >
                {error.status === 401 ? "Sign in again" : "Reload chats"}
              </button>
            </div>
          )}
          {!selected ? (
            <div className="chat-empty">
              <p className="lede">
                Ask about meanings, pronunciation, or usage.
              </p>
              <div className="chat-example">
                “What does acha mean, and when would I say it?”
              </div>
              <p className="quiet">Select a chat or start a new one.</p>
            </div>
          ) : !chat && !error ? (
            <p role="status">Opening conversation…</p>
          ) : chat ? (
            <>
              <div
                className="chat-messages"
                role="log"
                aria-label="Conversation"
                aria-live="off"
                onScroll={(e) => {
                  const el = e.currentTarget;
                  follow.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                }}
              >
                {chat.turns.map((turn) => (
                  <div key={turn.messageId}>
                    <article className="chat-message chat-user">
                      <span className="sr-only">You</span>
                      <p dir="auto">{turn.text}</p>
                    </article>
                    {turn.answer && (
                      <article className="chat-message chat-assistant">
                        <div className="chat-speaker">Lexikhan</div>
                        <div className="chat-markdown">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            skipHtml
                            components={{
                              img: () => null,
                              a: ({ children, href }) => (
                                <a
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {children}
                                </a>
                              ),
                              p: ({ children }) => <p dir="auto">{children}</p>,
                            }}
                          >
                            {turn.answer.replace(/\s*\u2014\s*/g, ", ")}
                          </ReactMarkdown>
                        </div>
                      </article>
                    )}
                    {turn.status === "FAILED" && (
                      <div className="notice">
                        <p>{turn.errorMessage}</p>
                        <button
                          disabled={busy || chat.pending}
                          onClick={() => {
                            sessionStorage.removeItem(
                              key(selected) + ":outbox",
                            );
                            edit(turn.text);
                            composer.current?.focus();
                          }}
                        >
                          Ask again
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                {chat.pending && (
                  <p role="status" className="quiet">
                    {chat.turns.some(
                      (t) => t.status === "GENERATING" && t.answer,
                    )
                      ? "Writing…"
                      : "Thinking…"}
                  </p>
                )}
                {!chat.turns.length && (
                  <p className="quiet">
                    Try a definition, translation, or casual expression.
                  </p>
                )}
                <div ref={end} />
              </div>
              <form
                className="chat-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  void send();
                }}
              >
                <label htmlFor="chat-question">Your question</label>
                <textarea
                  id="chat-question"
                  ref={composer}
                  value={draft}
                  maxLength={2000}
                  placeholder="What does this word mean?"
                  rows={2}
                  disabled={busy || !available}
                  onChange={(e) => edit(e.target.value)}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                />
                <div className="chat-compose-footer">
                  <span className="quiet">
                    Enter to send · Shift+Enter for a new line
                  </span>
                  <button
                    aria-label="Send question"
                    className="primary"
                    disabled={
                      busy || chat.pending || !draft.trim() || !available
                    }
                  >
                    {busy ? "Sending…" : "Send ↑"}
                  </button>
                </div>
              </form>
            </>
          ) : null}
          <p className="quiet chat-privacy">
            Questions are sent to OpenAI. Conversations are saved to your
            account.
          </p>
        </main>
      </div>
    </div>
  );
}
