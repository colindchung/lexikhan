import { useEffect, useRef, useState } from "react";
import type { Answer, Card, LearningApi, Rating, Review } from "./api";
import { ApiError } from "./api";

const ratings: { value: Rating; label: string; help: string }[] = [
  { value: "AGAIN", label: "Again", help: "Not yet" },
  { value: "HARD", label: "Hard", help: "With effort" },
  { value: "GOOD", label: "Good", help: "Got it" },
  { value: "EASY", label: "Easy", help: "Knew it" },
];
interface Progress {
  screen: "home" | "review" | "complete";
  cards: Card[];
  index: number;
  drafts: Record<string, string>;
  answer?: Answer;
  pending?: Review;
  reviewed: number;
  learned: number;
  forgotten: number;
  nextReviewAt?: string | null;
  retainedAnswer?: string;
}
const empty: Progress = {
  screen: "home",
  cards: [],
  index: 0,
  drafts: {},
  reviewed: 0,
  learned: 0,
  forgotten: 0,
};
function restore(key: string): Progress | null {
  try {
    const value = JSON.parse(
      sessionStorage.getItem(key) || "null",
    ) as Progress | null;
    return value &&
      ["home", "review", "complete"].includes(value.screen) &&
      Array.isArray(value.cards) &&
      value.drafts
      ? value
      : null;
  } catch {
    return null;
  }
}
export function nextDate(value?: string | null) {
  if (!value) return "Check back when you’re ready.";
  if (new Date(value).getTime() <= Date.now())
    return "More practice is ready whenever you are.";
  return `Next review ${new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value))}.`;
}
export function Brand() {
  return (
    <a className="brand" href="/" aria-label="Lexikhan home">
      <span className="brand-mark" aria-hidden="true">
        L<span>•</span>
      </span>
      lexikhan<span className="brand-tag">A LITTLE, EVERY DAY</span>
    </a>
  );
}
export function Welcome({
  signIn,
  error,
}: {
  signIn: () => void;
  error?: string;
}) {
  return (
    <div className="app">
      <header>
        <Brand />
        <span className="header-note">Your personal practice space</span>
      </header>
      <main className="welcome">
        <div className="eyebrow">
          <span className="status-dot" /> SMALL STEPS. LASTING MEMORY.
        </div>
        <h1>
          Make room for
          <br />
          <em>a little language.</em>
        </h1>
        <p className="lede">
          A few words. A moment to remember.
          <br />
          Build a practice that fits into your day.
        </p>
        {error && (
          <p role="alert" className="notice">
            {error}
          </p>
        )}
        <button className="primary" onClick={signIn}>
          Sign in to your space <span aria-hidden="true">↗</span>
        </button>
        <p className="quiet">A calm place to learn, one card at a time.</p>
        <div className="welcome-card" aria-hidden="true">
          <span className="eyebrow">THE ART OF REMEMBERING</span>
          <p>
            Small today.
            <br />
            <em>Second nature tomorrow.</em>
          </p>
          <div className="card-rule" />
          <span>RECALL &nbsp; · &nbsp; REFLECT &nbsp; · &nbsp; REPEAT</span>
        </div>
      </main>
      <footer>
        Consistency, without the pressure.
        <span>LEXIKHAN / PERSONAL LEARNING</span>
      </footer>
    </div>
  );
}
export function App({
  api,
  userId,
  signIn,
  signOut,
  openReminders,
}: {
  api: LearningApi;
  userId: string;
  signIn: () => void;
  signOut: () => void;
  openReminders?: () => void;
}) {
  const key = `lexikhan:session:${userId}`;
  const [progress, setProgress] = useState<Progress>(
    () => restore(key) || empty,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [loaded, setLoaded] = useState(() => !!restore(key));
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const card = progress.cards[progress.index];
  const revealed =
    progress.answer?.cardId === card?.cardId && !!progress.answer;
  function save(next: Progress) {
    sessionStorage.setItem(key, JSON.stringify(next));
    setProgress(next);
  }
  async function perform(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason
          : new ApiError(0, "Something went wrong. Please try again."),
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function refresh() {
    await perform(async () => {
      const response = await api.session();
      save({
        ...empty,
        cards: response.cards,
        nextReviewAt: response.nextReviewAt,
        drafts: progress.drafts,
        retainedAnswer:
          error?.status === 409 && card
            ? progress.drafts[card.cardId]
            : progress.retainedAnswer,
      });
      setLoaded(true);
    });
  }
  useEffect(() => {
    if (!loaded) void refresh();
  }, []); // One initial fetch; retries are explicit.
  useEffect(() => {
    heading.current?.focus();
  }, [progress.screen, progress.index, revealed]);
  async function reveal() {
    if (!card) return;
    await perform(async () => {
      const answer = await api.answer(card.cardId);
      save({ ...progress, answer });
    });
  }
  async function rate(rating: Rating) {
    if (!card || !revealed) return;
    await perform(async () => {
      const pending = progress.pending || {
        reviewId: crypto.randomUUID(),
        cardId: card.cardId,
        version: card.version,
        rating,
        typedAnswer: progress.drafts[card.cardId] || "",
      };
      save({ ...progress, pending }); // Persist the exact request before sending, including across reloads.
      const result = await api.review(pending);
      const nextReviewAt =
        !progress.nextReviewAt ||
        progress.nextReviewAt <= new Date().toISOString()
          ? result.nextDueAt
          : [progress.nextReviewAt, result.nextDueAt].sort()[0];
      const next = progress.index + 1;
      save({
        ...progress,
        pending: undefined,
        answer: undefined,
        index: next,
        screen: next === progress.cards.length ? "complete" : "review",
        reviewed: progress.reviewed + 1,
        learned:
          progress.learned +
          Number(card.state === "NEW" && pending.rating !== "AGAIN"),
        forgotten: progress.forgotten + Number(pending.rating === "AGAIN"),
        nextReviewAt,
      });
    });
  }
  const reviewCount = progress.cards.filter((c) => c.state !== "NEW").length;
  const newCount = progress.cards.length - reviewCount;
  return (
    <div className="app">
      <header>
        <Brand />
        <div className="header-actions">
          {openReminders && (
            <button
              className="text-button"
              onClick={openReminders}
              disabled={busy}
            >
              Reminders
            </button>
          )}
          <button className="text-button" onClick={signOut} disabled={busy}>
            Sign out <span aria-hidden="true">↗</span>
          </button>
        </div>
      </header>
      <main aria-busy={busy}>
        {error && (
          <div className="notice" role="alert">
            <p>{error.message}</p>
            {error.status === 401 ? (
              <button onClick={signIn}>Sign in again</button>
            ) : error.status === 409 ? (
              <button onClick={() => void refresh()} disabled={busy}>
                Refresh cards
              </button>
            ) : (
              !progress.pending && (
                <button
                  onClick={() =>
                    void (progress.screen === "review" ? reveal() : refresh())
                  }
                  disabled={busy}
                >
                  Try again
                </button>
              )
            )}
          </div>
        )}
        {!loaded ? (
          <section className="home">
            <h1 ref={heading} tabIndex={-1}>
              A moment to settle in.
            </h1>
            <p role="status">
              {busy ? "Finding your cards…" : "Your practice will appear here."}
            </p>
          </section>
        ) : progress.screen === "home" ? (
          <section className="home">
            <div className="eyebrow">
              <span className="status-dot" /> YOUR DAILY PRACTICE
            </div>
            <h1 ref={heading} tabIndex={-1}>
              {progress.cards.length ? (
                <>
                  A little practice.
                  <br />
                  <em>A lasting memory.</em>
                </>
              ) : (
                <>
                  Room to breathe.
                  <br />
                  <em>You’re all caught up.</em>
                </>
              )}
            </h1>
            <p className="lede">
              {progress.cards.length
                ? "Pick up where your memory left off."
                : "Nothing is due right now. Your next review will be here when it’s time."}
            </p>
            <div className="session-summary">
              <div>
                <strong>{reviewCount.toString().padStart(2, "0")}</strong>
                <span>to revisit</span>
              </div>
              <div>
                <strong>{newCount.toString().padStart(2, "0")}</strong>
                <span>to discover</span>
              </div>
              <div>
                <strong>
                  {Math.ceil(progress.cards.length * 0.5)}
                  <small> min</small>
                </strong>
                <span>estimated session</span>
              </div>
            </div>
            {progress.cards.length > 0 ? (
              <button
                className="primary"
                disabled={busy}
                onClick={() => save({ ...progress, screen: "review" })}
              >
                Start session <span aria-hidden="true">→</span>
              </button>
            ) : (
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void refresh()}
              >
                Check for cards
              </button>
            )}
            <p className="quiet">
              {progress.cards.length
                ? "No rush. Just a few thoughtful minutes."
                : nextDate(progress.nextReviewAt)}
            </p>
            {progress.retainedAnswer && (
              <aside className="saved-answer">
                <span className="eyebrow">YOUR SAVED ANSWER</span>
                <p>{progress.retainedAnswer}</p>
              </aside>
            )}
            <div className="practice-note">
              <span aria-hidden="true">✳</span>
              <p>
                Remembering takes practice.
                <br />
                <span>We’ll bring each card back when it helps most.</span>
              </p>
            </div>
          </section>
        ) : progress.screen === "complete" ? (
          <section className="complete">
            <div className="completion-mark" aria-hidden="true">
              ✓
            </div>
            <div className="eyebrow">A LITTLE FURTHER THAN BEFORE</div>
            <h1 ref={heading} tabIndex={-1}>
              That’s time
              <br />
              <em>well remembered.</em>
            </h1>
            <p className="lede">Your progress is saved. Let it settle in.</p>
            <div className="session-summary">
              <div>
                <strong>{progress.reviewed}</strong>
                <span>reviewed</span>
              </div>
              <div>
                <strong>{progress.learned}</strong>
                <span>newly recalled</span>
              </div>
              <div>
                <strong>{progress.forgotten}</strong>
                <span>to practice again</span>
              </div>
            </div>
            <p className="next-review">{nextDate(progress.nextReviewAt)}</p>
            <button
              className="primary"
              onClick={() => void refresh()}
              disabled={busy}
            >
              Back to your space <span aria-hidden="true">→</span>
            </button>
          </section>
        ) : (
          card && (
            <section className="review">
              <div className="review-top">
                <span className="eyebrow">
                  {card.state === "NEW"
                    ? "A NEW DISCOVERY"
                    : "A FAMILIAR THOUGHT"}
                </span>
                <span className="counter">
                  {progress.index + 1} <span>/ {progress.cards.length}</span>
                </span>
              </div>
              <progress
                aria-label="Session progress"
                value={progress.index}
                max={progress.cards.length}
              />
              <article className="study-card">
                <span className="eyebrow">BRING THIS TO MIND</span>
                <h1 ref={heading} tabIndex={-1}>
                  {card.prompt}
                </h1>
                <label htmlFor="attempt">
                  Your answer <span>(optional)</span>
                </label>
                <textarea
                  id="attempt"
                  dir="auto"
                  placeholder="Give yourself a moment to recall…"
                  value={progress.drafts[card.cardId] || ""}
                  maxLength={4000}
                  disabled={busy || !!progress.pending}
                  onChange={(e) =>
                    save({
                      ...progress,
                      drafts: {
                        ...progress.drafts,
                        [card.cardId]: e.target.value,
                      },
                    })
                  }
                />
                {revealed && (
                  <div className="answer">
                    <span className="eyebrow">THE ANSWER</span>
                    <h2 dir="auto">{progress.answer?.answer}</h2>
                    {progress.answer?.explanation && (
                      <p>{progress.answer.explanation}</p>
                    )}
                    {progress.answer?.examples.map((example, i) => (
                      <p className="example" key={i}>
                        {example}
                      </p>
                    ))}
                    {progress.answer?.audioUrl?.startsWith("https://") && (
                      <audio
                        controls
                        src={progress.answer.audioUrl}
                        aria-label="Pronunciation"
                      />
                    )}
                  </div>
                )}
              </article>
              {!revealed ? (
                <>
                  <button
                    className="primary reveal"
                    disabled={busy}
                    onClick={() => void reveal()}
                  >
                    {busy ? "Finding the answer…" : "Reveal answer"}{" "}
                    <span aria-hidden="true">↗</span>
                  </button>
                  <p className="quiet centered">
                    Try remembering before you reveal. It’s the effort that
                    counts.
                  </p>
                </>
              ) : (
                <div className="rating-section">
                  <p>How did that feel?</p>
                  {progress.pending ? (
                    <button
                      className="primary"
                      disabled={
                        busy || error?.status === 409 || error?.status === 401
                      }
                      onClick={() => void rate(progress.pending!.rating)}
                    >
                      {busy ? "Saving your review…" : "Retry saved review"}
                    </button>
                  ) : (
                    <div className="ratings">
                      {ratings.map((rating) => (
                        <button
                          key={rating.value}
                          aria-label={`${rating.label} ${rating.help}`}
                          className={`rating rating-${rating.value.toLowerCase()}`}
                          disabled={busy}
                          onClick={() => void rate(rating.value)}
                        >
                          <strong>{rating.label}</strong>
                          <span>{rating.help}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <p className="quiet">
                    An honest answer helps us find the right time to ask again.
                  </p>
                </div>
              )}
            </section>
          )
        )}
      </main>
      <footer>
        A little, every day.
        <span>RECALL &nbsp; / &nbsp; REFLECT &nbsp; / &nbsp; REPEAT</span>
      </footer>
    </div>
  );
}
