import { Navbar } from "./Navbar";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { App, Welcome } from "./App";
import { navigate, returnPath, useRoute } from "./routes";
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
function AuthStatus({
  error,
  retry,
  signOut,
}: {
  error?: string;
  retry?: () => void;
  signOut?: () => void;
}) {
  return (
    <div className="app">
      <Navbar signOut={signOut} />
      <main className="onboarding">
        <h1>{error ? "Unable to load" : "Loading…"}</h1>
        <p role={error ? "alert" : "status"} className="lede">
          {error ?? "Loading your account."}
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
      const signedIn = await auth.manager.signinRedirectCallback();
      const state = signedIn.state as { returnTo?: unknown } | undefined;
      history.replaceState({}, "", returnPath(state?.returnTo));
    } catch {
      history.replaceState({}, "", "/");
      root.render(
        <AuthStatus
          error="Your sign-in link expired or could not be completed. Start again to sign in securely."
          retry={() => location.assign("/")}
        />,
      );
      return;
    }
  }
  await auth.manager.clearStaleState();
  const initialUser = await auth.restore();
  if (initialUser && location.pathname === "/") navigate("/practice", true);
  const api = createApi(config.apiUrl, auth.token);
  function Experience() {
    const [user, setUser] = useState(initialUser);
    const route = useRoute();
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
    const signOut = () => {
      void auth
        .signOut()
        .catch(() => setError("Sign-out couldn’t finish. Please try again."));
    };
    if (route.page === "not-found")
      return (
        <AuthStatus
          signOut={user ? signOut : undefined}
          error="This page doesn’t exist."
          retry={() => navigate("/practice")}
        />
      );
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
          onProfileReady={(ready) => {
            if (!ready && location.pathname !== "/onboarding")
              navigate(
                `/onboarding?next=${encodeURIComponent(location.pathname + location.search)}`,
                true,
              );
            if (ready && location.pathname === "/onboarding") {
              const next = returnPath(
                new URLSearchParams(location.search).get("next"),
              );
              navigate(
                next.startsWith("/onboarding") ? "/practice" : next,
                true,
              );
            }
          }}
          signIn={signIn}
          signOut={signOut}
        >
          {route.page === "chats" ? (
            <Chats
              key={route.chatId ?? "chat-list"}
              selected={route.chatId}
              onSelect={(id) => navigate(`/chats/${id}`)}
              api={api}
              userId={user.profile.sub}
              signIn={signIn}
              signOut={signOut}
            />
          ) : route.page === "reminders" ? (
            <Reminders api={api} signIn={signIn} signOut={signOut} />
          ) : (
            <App
              api={api}
              userId={user.profile.sub}
              signIn={signIn}
              signOut={signOut}
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
      error="Unable to load the app. Check your connection, then try again."
      retry={() => location.reload()}
    />,
  ),
);
