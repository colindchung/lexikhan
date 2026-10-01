import { useState } from "react";
import { createRoot } from "react-dom/client";
import { App, Welcome } from "./App";
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
async function boot() {
  const config = await loadConfig();
  const auth = createAuth(config);
  if (location.pathname === "/auth/callback") {
    try {
      await auth.manager.signinRedirectCallback();
    } finally {
      history.replaceState({}, "", "/");
    }
  }
  await auth.manager.clearStaleState();
  const user = await auth.manager.getUser();
  const api = createApi(config.apiUrl, auth.token);
  function Experience() {
    const [showReminders, setShowReminders] = useState(false);
    const [error, setError] = useState<string>();
    const signIn = () => {
      void auth
        .signIn()
        .catch(() => setError("Sign-in couldn’t start. Please try again."));
    };
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
          {showReminders ? (
            <Reminders
              api={api}
              signIn={signIn}
              close={() => setShowReminders(false)}
            />
          ) : (
            <App
              openReminders={() => setShowReminders(true)}
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
      <Welcome signIn={signIn} error={error} />
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
    <Welcome
      signIn={() => location.assign("/")}
      error="We couldn’t open your space. Check your connection, then try again."
    />,
  ),
);
