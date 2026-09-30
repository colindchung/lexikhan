import { afterEach, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("sends an access token and preserves the review payload", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const payload = {
    cardId: "one",
    version: 3,
    reviewId: "retry-id",
    rating: "GOOD" as const,
  };
  await createApi("https://api.example", async () => "access-token").review(
    payload,
  );
  expect(fetch).toHaveBeenCalledWith(
    "https://api.example/reviews",
    expect.objectContaining({
      method: "POST",
      headers: {
        Authorization: "Bearer access-token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    }),
  );
});
it("distinguishes conflicts from connection errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("{}", { status: 409 })),
  );
  await expect(
    createApi("https://api.example", async () => "token").session(),
  ).rejects.toMatchObject({ status: 409 });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  await expect(
    createApi("https://api.example", async () => "token").session(),
  ).rejects.toBeInstanceOf(ApiError);
});
