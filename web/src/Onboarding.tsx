import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { OnboardingApi, ProfileResponse } from "./api";
import { ApiError } from "./api";
import { Brand } from "./App";

const languageName = (code: string) =>
  new Intl.DisplayNames(["en"], { type: "language" }).of(code) || code;
const timezones = Array.from(
  new Set([
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    "UTC",
    ...Intl.supportedValuesOf("timeZone"),
  ]),
).sort();

export function Onboarding({
  api,
  userId,
  signIn,
  signOut,
  children,
  onProfileReady,
}: {
  api: OnboardingApi;
  userId: string;
  signIn: () => void;
  signOut: () => void;
  children: ReactNode;
  onProfileReady?: (ready: boolean) => void;
}) {
  const [data, setData] = useState<ProfileResponse>();
  const [error, setError] = useState<ApiError>();
  const [busy, setBusy] = useState(false);
  const [deckId, setDeckId] = useState("");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const [dailyGoal, setDailyGoal] = useState(3);
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const deck = data?.decks.find((d) => d.id === deckId);
  function fail(reason: unknown) {
    setError(
      reason instanceof ApiError
        ? reason
        : new ApiError(0, "We couldn’t save your setup. Please try again."),
    );
  }
  function clearEmptySession() {
    const key = `lexikhan:session:${userId}`;
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || "null");
      if (!saved?.cards?.length) sessionStorage.removeItem(key);
    } catch {
      sessionStorage.removeItem(key);
    }
  }
  async function load() {
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.profile();
      if (response.profile) clearEmptySession();
      setData(response);
      setDeckId(response.decks[0]?.id || "");
    } catch (reason) {
      fail(reason);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [api]);
  useEffect(() => {
    if (data && !data.profile) heading.current?.focus();
    if (data) onProfileReady?.(!!data.profile);
  }, [data, onProfileReady]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!deck || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const { profile } = await api.enroll({
        deckId: deck.id,
        learningLanguage: deck.learningLanguage,
        baseLanguage: deck.baseLanguage,
        timezone,
        dailyGoal,
      });
      // An old empty session must not hide newly enrolled cards.
      clearEmptySession();
      setData({ ...data!, profile });
    } catch (reason) {
      fail(reason);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  if (data?.profile) return children;
  return (
    <div className="app">
      <header>
        <Brand />
        <button className="text-button" onClick={signOut} disabled={busy}>
          Sign out ↗
        </button>
      </header>
      <main className="onboarding" aria-busy={busy}>
        <div className="eyebrow">YOUR FIRST SMALL STEP</div>
        <h1 ref={heading} tabIndex={-1}>
          Make this
          <br />
          <em>your practice.</em>
        </h1>
        <p className="lede">A few useful phrases. A pace that feels right.</p>
        {error && (
          <div className="notice" role="alert">
            <p>
              {error.status === 409
                ? "Your setup was completed in another tab. Load your saved profile to continue."
                : error.status === 401
                  ? "Your sign-in has expired. Sign in again to continue."
                  : "We couldn’t finish your setup. Your choices are still here; please try again."}
            </p>
            {error.status === 401 ? (
              <button onClick={signIn}>Sign in again</button>
            ) : !data || error.status === 409 ? (
              <button disabled={busy} onClick={() => void load()}>
                Load profile
              </button>
            ) : null}
          </div>
        )}
        {!data ? (
          <p role="status">
            {busy ? "Loading your space…" : "Your setup will appear here."}
          </p>
        ) : (
          <form onSubmit={(event) => void submit(event)}>
            <fieldset disabled={busy}>
              <legend className="sr-only">Your learning preferences</legend>
              <label htmlFor="deck">Language & starter deck</label>
              <select
                id="deck"
                value={deckId}
                onChange={(e) => setDeckId(e.target.value)}
                required
              >
                {data.decks.map((d) => (
                  <option key={d.id} value={d.id}>
                    {languageName(d.learningLanguage)} from{" "}
                    {languageName(d.baseLanguage)} — {d.name}
                  </option>
                ))}
              </select>
              {deck && (
                <p className="quiet">
                  {deck.cardCount} everyday phrases, ready for your first
                  session.
                </p>
              )}
              <label htmlFor="goal">Daily practice goal</label>
              <select
                id="goal"
                value={dailyGoal}
                onChange={(e) => setDailyGoal(Number(e.target.value))}
              >
                <option value={3}>3 cards · a gentle start</option>
                <option value={5}>5 cards · a little momentum</option>
                <option value={10}>10 cards · room to grow</option>
              </select>
              <p className="quiet">
                Each session fits your goal. You can always practice more.
              </p>
              <label htmlFor="timezone">Your timezone</label>
              <select
                id="timezone"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                {timezones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <button className="primary" type="submit" disabled={!deck}>
                {busy ? "Preparing your cards…" : "Create my practice"}{" "}
                <span aria-hidden="true">↗</span>
              </button>
            </fieldset>
          </form>
        )}
      </main>
    </div>
  );
}
