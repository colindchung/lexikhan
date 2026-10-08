import { useEffect, useRef, useState } from "react";
import type { ReminderApi, ReminderSettings } from "./api";
import { ApiError } from "./api";
import { Navbar } from "./Navbar";

export function Reminders({
  api,
  signOut,
  signIn,
}: {
  api: ReminderApi;
  signOut: () => void;
  signIn: () => void;
}) {
  const [settings, setSettings] = useState<ReminderSettings>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const [saved, setSaved] = useState(false);
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  async function load() {
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      setSettings(await api.reminders());
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason : new ApiError(0, "Unavailable"),
      );
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
    heading.current?.focus();
  }, [api]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!settings || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      setSettings(
        await api.saveReminders({
          enabled: settings.enabled,
          time: settings.time,
          timezone: settings.timezone,
          version: settings.version,
        }),
      );
      setSaved(true);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason : new ApiError(0, "Unavailable"),
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  const zones = Array.from(
    new Set([
      settings?.timezone || "UTC",
      "UTC",
      ...Intl.supportedValuesOf("timeZone"),
    ]),
  ).sort();
  return (
    <div className="app">
      <Navbar signOut={signOut} disabled={busy} />
      <main className="onboarding" aria-busy={busy}>
        <div className="eyebrow">EMAIL</div>
        <h1 ref={heading} tabIndex={-1}>
          Daily vocabulary
        </h1>
        <p className="lede">
          One new word or phrase by email each day. No repeats.
        </p>
        {error && (
          <div className="notice" role="alert">
            <p>
              {error.status === 401
                ? "Your sign-in expired. Sign in again to continue."
                : error.status === 409
                  ? "Your settings changed in another session. Reload before saving."
                  : "We couldn’t save or load your reminders. Please try again."}
            </p>
            {error.status === 401 ? (
              <button onClick={signIn}>Sign in again</button>
            ) : (
              <button disabled={busy} onClick={() => void load()}>
                Reload settings
              </button>
            )}
          </div>
        )}
        {!settings ? (
          <p role="status">
            {busy ? "Loading reminders…" : "Your settings will appear here."}
          </p>
        ) : (
          <form onSubmit={(e) => void save(e)}>
            {!settings.available && (
              <p className="notice">
                Email setup is pending for your account. Your email address
                needs to be verified and connected before you can enable
                reminders.
              </p>
            )}
            {settings.email && (
              <p>
                Send to <strong>{settings.email}</strong>
              </p>
            )}
            <fieldset disabled={busy}>
              <legend className="sr-only">Email reminder settings</legend>
              <label className="consent">
                <input
                  type="checkbox"
                  checked={settings.enabled}
                  disabled={!settings.available && !settings.enabled}
                  onChange={(e) => {
                    setSaved(false);
                    setSettings({ ...settings, enabled: e.target.checked });
                  }}
                />{" "}
                I agree to receive daily Lexikhan vocabulary emails.
              </label>
              <p className="quiet">
                Up to one email per day. Turn reminders off here at any time.
                Saving with reminders off stops future sends; a message already
                being sent may still arrive.
              </p>
              <label htmlFor="reminder-time">Preferred time</label>
              <input
                id="reminder-time"
                type="time"
                required
                value={settings.time}
                onChange={(e) => {
                  setSaved(false);
                  setSettings({ ...settings, time: e.target.value });
                }}
              />
              <label htmlFor="reminder-zone">Timezone</label>
              <select
                id="reminder-zone"
                value={settings.timezone}
                onChange={(e) => {
                  setSaved(false);
                  setSettings({ ...settings, timezone: e.target.value });
                }}
              >
                {zones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <p className="quiet">
                We check every five minutes around your chosen time. When you’ve
                seen every word, emails pause until we add more.
              </p>
              <button className="primary" type="submit">
                {busy ? "Saving…" : "Save reminders"}
              </button>
            </fieldset>
            {saved && (
              <p role="status">
                {settings.enabled ? "Reminders saved." : "Reminders are off."}
              </p>
            )}
          </form>
        )}
      </main>
    </div>
  );
}
