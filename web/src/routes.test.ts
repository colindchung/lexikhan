import { expect, it } from "vitest";
import { parseRoute, returnPath } from "./routes";
it("recognizes stable page and conversation routes", () => {
  expect(parseRoute("/reminders").page).toBe("reminders");
  expect(parseRoute("/practice").page).toBe("practice");
  expect(parseRoute("/onboarding").page).toBe("onboarding");
  expect(parseRoute("/chats")).toEqual({ page: "chats" });
  const id = "bc6aeea1-1020-4321-aaaa-000000000001";
  expect(parseRoute(`/chats/${id}`)).toEqual({ page: "chats", chatId: id });
  expect(parseRoute("/chats/invalid").page).toBe("not-found");
});
it("only restores known same-origin routes after sign-in", () => {
  for (const path of [
    "https://evil.test",
    "//evil.test",
    "/\\evil.test",
    "/auth/callback",
    "/missing",
    null,
  ]) {
    expect(returnPath(path)).toBe("/practice");
  }
  expect(returnPath("/reminders")).toBe("/reminders");
});
