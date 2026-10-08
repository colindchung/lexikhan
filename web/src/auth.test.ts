import { beforeEach, expect, it, vi } from "vitest";
import { createAuth } from "./auth";
const fake = vi.hoisted(() => ({
  user: null as any,
  settings: {} as any,
  renew: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("oidc-client-ts", () => ({
  WebStorageStateStore: class {
    constructor(public settings: any) {}
  },
  UserManager: class {
    constructor(settings: any) {
      fake.settings = settings;
    }
    getUser = async () => fake.user;
    signinSilent = fake.renew;
    removeUser = fake.remove;
  },
}));
const config = {
  apiUrl: "https://api.test",
  authority: "https://auth.test/pool",
  clientId: "client",
  authDomain: "https://auth.test",
};
const key = `oidc.user:${config.authority}:${config.clientId}`;
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  fake.user = {
    expired: true,
    expires_in: -1,
    refresh_token: "refresh",
    access_token: "old",
    profile: { sub: "me" },
  };
  fake.remove.mockImplementation(async () => {
    fake.user = null;
  });
});
it("persists the user but keeps PKCE state scoped to the tab", () => {
  sessionStorage.setItem(key, "legacy");
  createAuth(config);
  expect(localStorage.getItem(key)).toBe("legacy");
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(fake.settings.userStore.settings.store).toBe(localStorage);
  expect(fake.settings.stateStore.settings.store).toBe(sessionStorage);
});
it("does not revive a legacy session after another tab signed out", () => {
  sessionStorage.setItem(key, "legacy");
  localStorage.setItem(key + ":logout", "epoch");
  createAuth(config);
  expect(localStorage.getItem(key)).toBeNull();
});
it("coalesces parallel refreshes and returns the renewed token", async () => {
  fake.renew.mockImplementation(async () => ({
    ...fake.user,
    expired: false,
    expires_in: 3600,
    access_token: "new",
  }));
  const auth = createAuth(config);
  expect(await Promise.all([auth.token(), auth.token(), auth.token()])).toEqual(
    ["new", "new", "new"],
  );
  expect(fake.renew).toHaveBeenCalledTimes(1);
});
it("retains the session on a temporary network failure", async () => {
  fake.renew.mockRejectedValue(new TypeError("offline"));
  const auth = createAuth(config);
  await expect(auth.token()).rejects.toMatchObject({ status: 0 });
  expect(fake.remove).not.toHaveBeenCalled();
  expect(fake.user.refresh_token).toBe("refresh");
});
it("requires sign-in after a definitively rejected refresh token", async () => {
  fake.renew.mockRejectedValue({ error: "invalid_grant" });
  await expect(createAuth(config).token()).rejects.toMatchObject({
    status: 401,
  });
  expect(fake.remove).toHaveBeenCalledOnce();
});
it("discards a refresh that overlaps a sign-out", async () => {
  fake.renew.mockImplementation(async () => {
    localStorage.setItem(key + ":logout", "new epoch");
    return { ...fake.user, expired: false };
  });
  expect(await createAuth(config).restore()).toBeNull();
  expect(fake.remove).toHaveBeenCalledOnce();
});
it("shares storage changes and checks the session when a tab wakes", async () => {
  fake.user.expired = false;
  fake.user.expires_in = 3600;
  const listener = vi.fn();
  const auth = createAuth(config);
  const stop = auth.subscribe(listener);
  window.dispatchEvent(new StorageEvent("storage", { key }));
  expect(listener).toHaveBeenCalledOnce();
  fake.user.expired = true;
  fake.renew.mockResolvedValue({ ...fake.user, expired: false });
  window.dispatchEvent(new Event("focus"));
  await vi.waitFor(() => expect(fake.renew).toHaveBeenCalledOnce());
  stop();
});
