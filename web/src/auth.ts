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
    automaticSilentRenew: true,
    loadUserInfo: false,
    userStore: new WebStorageStateStore({ store: sessionStorage }),
    stateStore: new WebStorageStateStore({ store: sessionStorage }),
    // Cognito uses /logout rather than OIDC's end-session endpoint.
    metadata: {
      issuer: config.authority,
      authorization_endpoint: `${config.authDomain}/oauth2/authorize`,
      token_endpoint: `${config.authDomain}/oauth2/token`,
      jwks_uri: `${config.authority}/.well-known/jwks.json`,
    },
  };
  const manager = new UserManager(settings);
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
    async token() {
      let user = await manager.getUser();
      if (user?.expired) {
        try {
          user = await manager.signinSilent();
        } catch {
          user = null;
        }
      }
      if (!user || user.expired)
        throw new ApiError(
          401,
          "Your sign-in has expired. Sign in again to continue where you left off.",
        );
      return user.access_token;
    },
    async signOut() {
      const user = await manager.getUser();
      if (user)
        sessionStorage.removeItem(`lexikhan:session:${user.profile.sub}`);
      await manager.removeUser();
      const url = new URL(`${config.authDomain}/logout`);
      url.searchParams.set("client_id", config.clientId);
      url.searchParams.set("logout_uri", `${location.origin}/`);
      location.assign(url.toString());
    },
  };
}
