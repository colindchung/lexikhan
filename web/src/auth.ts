import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { ApiError } from "./api";

export interface Config {
  apiUrl: string;
  authority: string;
  clientId: string;
  authDomain: string;
}
export async function loadConfig(): Promise<Config> {
  const response = await fetch("/config.json", {
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok)
    throw new Error("The app is being prepared. Please refresh in a moment.");
  const config = (await response.json()) as Config;
  if (
    !config.clientId ||
    ![config.apiUrl, config.authority, config.authDomain].every(
      (url) => typeof url === "string" && url.startsWith("https://"),
    )
  ) {
    throw new Error(
      "The app configuration is incomplete. Please try again later.",
    );
  }
  return config;
}
export function createAuth(config: Config) {
  const settings = {
    authority: config.authority,
    client_id: config.clientId,
    redirect_uri: `${location.origin}/auth/callback`,
    response_type: "code",
    scope: "openid email aws.cognito.signin.user.admin",
    automaticSilentRenew: false,
    loadUserInfo: false,
    userStore: new WebStorageStateStore({ store: localStorage }),
    stateStore: new WebStorageStateStore({ store: sessionStorage }),
    // Cognito uses /logout rather than OIDC's end-session endpoint.
    metadata: {
      issuer: config.authority,
      authorization_endpoint: `${config.authDomain}/oauth2/authorize`,
      token_endpoint: `${config.authDomain}/oauth2/token`,
      jwks_uri: `${config.authority}/.well-known/jwks.json`,
    },
  };
  const storageKey = `oidc.user:${config.authority}:${config.clientId}`;
  const epochKey = `${storageKey}:logout`;
  // Keep an existing tab signed in when upgrading from session-only storage.
  const legacy = sessionStorage.getItem(storageKey);
  if (
    legacy &&
    !localStorage.getItem(storageKey) &&
    !localStorage.getItem(epochKey)
  ) {
    localStorage.setItem(storageKey, legacy);
  }
  sessionStorage.removeItem(storageKey);
  const manager = new UserManager(settings);
  let renewing:
    Promise<Awaited<ReturnType<typeof manager.getUser>>> | undefined;
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((listener) => listener());
  const locked = <T>(action: () => Promise<T>): Promise<T> =>
    navigator.locks ? navigator.locks.request(storageKey, action) : action();

  async function restore() {
    if (renewing) return renewing;
    renewing = locked(async () => {
      let user = await manager.getUser();
      if (!user) return null;
      if (!user.expired && (user.expires_in ?? 0) > 60) return user;
      if (!user.refresh_token) {
        await manager.removeUser();
        notify();
        return null;
      }
      const epoch = localStorage.getItem(epochKey);
      try {
        user = await manager.signinSilent();
        if (epoch !== localStorage.getItem(epochKey)) {
          await manager.removeUser();
          return null;
        }
        notify();
        return user;
      } catch (error) {
        const code = (error as { error?: string })?.error;
        if (code === "invalid_grant" || code === "login_required") {
          await manager.removeUser();
          notify();
          return null;
        }
        // Offline, timeout, and provider outages must not destroy the session.
        throw new ApiError(
          0,
          "Reconnecting to your account. Check your connection and try again.",
        );
      }
    }).finally(() => {
      renewing = undefined;
    });
    return renewing;
  }

  return {
    manager,
    signIn: () => manager.signinRedirect(),
    signUp: () =>
      new UserManager({
        ...settings,
        automaticSilentRenew: false,
        metadata: {
          ...settings.metadata,
          authorization_endpoint: `${config.authDomain}/signup`,
        },
      }).signinRedirect(),
    restore,
    subscribe(listener: () => void) {
      listeners.add(listener);
      const storage = (event: StorageEvent) => {
        if (
          event.key === storageKey ||
          event.key === epochKey ||
          event.key === null
        )
          listener();
      };
      const wake = () => {
        if (document.visibilityState === "visible")
          void restore().catch(() => {});
      };
      window.addEventListener("storage", storage);
      window.addEventListener("focus", wake);
      window.addEventListener("online", wake);
      document.addEventListener("visibilitychange", wake);
      const timer = setInterval(wake, 60_000);
      return () => {
        listeners.delete(listener);
        window.removeEventListener("storage", storage);
        window.removeEventListener("focus", wake);
        window.removeEventListener("online", wake);
        document.removeEventListener("visibilitychange", wake);
        clearInterval(timer);
      };
    },
    async token() {
      const user = await restore();
      if (!user || user.expired)
        throw new ApiError(
          401,
          "Your sign-in has expired. Sign in again to continue where you left off.",
        );
      return user.access_token;
    },
    async signOut() {
      // Publish intent immediately so an in-flight refresh cannot restore sign-in.
      localStorage.setItem(epochKey, crypto.randomUUID());
      await locked(async () => {
        const user = await manager.getUser();
        if (user)
          sessionStorage.removeItem(`lexikhan:session:${user.profile.sub}`);
        await manager.removeUser();
      });
      notify();
      const url = new URL(`${config.authDomain}/logout`);
      url.searchParams.set("client_id", config.clientId);
      url.searchParams.set("logout_uri", `${location.origin}/`);
      location.assign(url.toString());
    },
  };
}
