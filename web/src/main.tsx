import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App, Brand, Welcome } from "./App";
import { Chats } from "./Chats";
import { Reminders } from "./Reminders";
import { Onboarding } from "./Onboarding";
import { createApi } from "./api";
import { createAuth, loadConfig } from "./auth";
import "@fontsource/dm-sans/latin-400.css";
import "@fontsource/dm-sans/latin-500.css";
import "@fontsource/dm-sans/latin-600.css";
import "@fontsource/libre-caslon-display/latin-400.css";
import "./style.css";

const root = createRoot(document.getElementById("root")!);
function AuthStatus({ error, retry }: { error?: string; retry?: () => void }) {
  return (
    <div className="app">
      <header>
        <Brand />
      </header>
      <main className="onboarding">
        <div className="eyebrow">A LITTLE, EVERY DAY</div>
        <h1>{error ? "Let’s try that again." : "Opening your space…"}</h1>
        <p role={error ? "alert" : "status"} className="lede">
          {error ?? "Just a moment while we get you settled in."}
        </p>
        {retry && (
          <button className="primary" onClick={retry}>
            Try again
          </button>
        )}
        {error && (
          <p>
            <a href="/">Back to Lexikhan</a>
          </p>
        )}
      </main>
    </div>
  );
}
root.render(<AuthStatus />);
async function boot() {
  const config = await loadConfig();
  const auth = createAuth(config);
  if (location.pathname === "/auth/callback") {
    try {
      await auth.manager.signinRedirectCallback();
    } catch {
      history.replaceState({}, "", "/");
      root.render(
        <AuthStatus
          error="Your sign-in link expired or could not be completed. Start again to sign in securely."
          retry={() => location.assign("/")}
        />,
      );
      return;
    } finally {
      history.replaceState({}, "", "/");
    }
  }
  await auth.manager.clearStaleState();
  const initialUser = await auth.restore();
  const api = createApi(config.apiUrl, auth.token);
  function Experience() {
    const [user, setUser] = useState(initialUser);
    const viewKey = `lexikhan:view:${user?.profile.sub ?? "guest"}`;
    const [view, setView] = useState<"practice" | "reminders" | "chats">(() => {
      const saved = localStorage.getItem(viewKey);
      return saved === "chats" || saved === "reminders" ? saved : "practice";
    });
    useEffect(() => {
      localStorage.setItem(viewKey, view);
    }, [viewKey, view]);
    useEffect(
      () =>
        auth.subscribe(() => {
          void auth.manager.getUser().then((next) => {
            if (next && next.profile.sub !== user?.profile.sub)
              location.reload();
            else setUser(next);
          });
        }),
      [user?.profile.sub],
    );
    const [error, setError] = useState<string>();
    const [pending, setPending] = useState<"signin" | "signup">();
    const starting = useRef(false);
    useEffect(() => {
      const reset = () => {
        starting.current = false;
        setPending(undefined);
      };
      window.addEventListener("pageshow", reset);
      return () => window.removeEventListener("pageshow", reset);
    }, []);
    const startAuth = (mode: "signin" | "signup") => {
      if (starting.current) return;
      starting.current = true;
      setPending(mode);
      setError(undefined);
      void (mode === "signup" ? auth.signUp() : auth.signIn()).catch(() => {
        starting.current = false;
        setPending(undefined);
        setError(
          "We couldn’t open sign-in. Check your connection and try again.",
        );
      });
    };
    const signIn = () => startAuth("signin");
    return user ? (
      <>
        {error && (
          <div role="alert" className="notice">
            {error}
          </div>
        )}
        <Onboarding
          api={api}
          userId={user.profile.sub}
          signIn={signIn}
          signOut={() => {
            void auth
              .signOut()
              .catch(() =>
                setError("Sign-out couldn’t finish. Please try again."),
              );
          }}
        >
          {view === "chats" ? (
            <Chats
              api={api}
              userId={user.profile.sub}
              signIn={signIn}
              close={() => setView("practice")}
            />
          ) : view === "reminders" ? (
            <Reminders
              api={api}
              signIn={signIn}
              close={() => setView("practice")}
            />
          ) : (
            <App
              openReminders={() => setView("reminders")}
              openChats={() => setView("chats")}
              api={api}
              userId={user.profile.sub}
              signIn={signIn}
              signOut={() => {
                void auth
                  .signOut()
                  .catch(() =>
                    setError("Sign-out couldn’t finish. Please try again."),
                  );
              }}
            />
          )}
        </Onboarding>
      </>
    ) : (
      <Welcome
        signIn={signIn}
        signUp={() => startAuth("signup")}
        pending={pending}
        error={error}
      />
    );
  }
  root.render(<Experience />);
  // Offline shell only. Reviews still require a connection and aren't cached.
  if ("serviceWorker" in navigator && import.meta.env.PROD) {
    void navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}
void boot().catch(() =>
  root.render(
    <AuthStatus
      error="We couldn’t open your space. Check your connection, then try again."
      retry={() => location.reload()}
    />,
  ),
);
