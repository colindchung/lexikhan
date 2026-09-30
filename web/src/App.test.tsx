import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App, Welcome } from "./App";
import { ApiError } from "./api";
import type { LearningApi, ReviewResult } from "./api";

const card = {
  cardId: "one",
  prompt: "Hello",
  state: "NEW",
  version: 1,
  dueAt: "2026-01-01T00:00:00Z",
};
const result: ReviewResult = {
  ...card,
  version: 2,
  state: "LEARNING",
  nextDueAt: "2030-01-02T12:00:00Z",
};
function setup(overrides: Partial<LearningApi> = {}) {
  const api = {
    session: vi.fn().mockResolvedValue({ cards: [card] }),
    answer: vi
      .fn()
      .mockResolvedValue({ cardId: "one", answer: "Bonjour", examples: [] }),
    review: vi.fn().mockResolvedValue(result),
    ...overrides,
  };
  const signIn = vi.fn();
  const view = render(
    <App api={api} userId="user-one" signIn={signIn} signOut={vi.fn()} />,
  );
  return { api, signIn, user: userEvent.setup(), ...view };
}
async function reveal(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: /Start session/ }),
  );
  await user.type(screen.getByLabelText(/Your answer/), "Bon jour");
  expect(screen.queryByText("Bonjour")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Reveal answer/ }));
  await screen.findByText("Bonjour");
}
describe("daily practice", () => {
  it("completes prompt → reveal → grade with correct version and saved typed answer", async () => {
    const { user, api } = setup();
    await reveal(user);
    await user.click(screen.getByRole("button", { name: /Good Got it/ }));
    await screen.findByText(/Your progress is saved/);
    expect(api.review).toHaveBeenCalledWith(
      expect.objectContaining({
        cardId: "one",
        version: 1,
        rating: "GOOD",
        typedAnswer: "Bon jour",
      }),
    );
    expect(screen.getByText("newly recalled")).toBeInTheDocument();
  });
  it("retries the exact request after a lost response and reload", async () => {
    const review = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(0, "Connection lost"))
      .mockResolvedValue(result);
    const first = setup({ review });
    await reveal(first.user);
    await first.user.click(screen.getByRole("button", { name: /Good Got it/ }));
    await screen.findByRole("alert");
    const submitted = review.mock.calls[0][0];
    first.unmount();
    const second = setup({ review });
    expect(screen.getByLabelText(/Your answer/)).toHaveValue("Bon jour");
    await second.user.click(
      screen.getByRole("button", { name: "Retry saved review" }),
    );
    await screen.findByText(/Your progress is saved/);
    expect(review.mock.calls[1][0]).toEqual(submitted);
  });
  it("refreshes a stale version without losing the learner answer", async () => {
    const session = vi
      .fn()
      .mockResolvedValueOnce({ cards: [card] })
      .mockResolvedValue({ cards: [] });
    const { user } = setup({
      session,
      review: vi.fn().mockRejectedValue(new ApiError(409, "Card changed")),
    });
    await reveal(user);
    await user.click(screen.getByRole("button", { name: /Hard With effort/ }));
    await user.click(
      await screen.findByRole("button", { name: "Refresh cards" }),
    );
    await screen.findByText("YOUR SAVED ANSWER");
    expect(screen.getByText("Bon jour")).toBeInTheDocument();
  });
  it("offers sign-in on expiration and keeps draft progress", async () => {
    const { user, signIn } = setup({
      answer: vi.fn().mockRejectedValue(new ApiError(401, "Expired")),
    });
    await user.click(
      await screen.findByRole("button", { name: /Start session/ }),
    );
    await user.type(screen.getByLabelText(/Your answer/), "Saved attempt");
    await user.click(screen.getByRole("button", { name: /Reveal answer/ }));
    await user.click(
      await screen.findByRole("button", { name: "Sign in again" }),
    );
    expect(signIn).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem("lexikhan:session:user-one")).toContain(
      "Saved attempt",
    );
  });
  it("does not reveal answers or show grading buttons before explicit reveal", async () => {
    const { user, api } = setup();
    await user.click(
      await screen.findByRole("button", { name: /Start session/ }),
    );
    expect(api.answer).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /Good/ }),
    ).not.toBeInTheDocument();
  });
  it("renders empty and loading states and recovers session fetch failures", async () => {
    const session = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, "Try later"))
      .mockResolvedValue({ cards: [] });
    const { user } = setup({ session });
    await user.click(await screen.findByRole("button", { name: "Try again" }));
    await screen.findByText(/all caught up/);
    expect(
      screen.queryByRole("button", { name: /Start session/ }),
    ).not.toBeInTheDocument();
  });
  it("does not submit twice while a review is in flight", async () => {
    let resolve!: (value: ReviewResult) => void;
    const review = vi.fn().mockReturnValue(
      new Promise<ReviewResult>((done) => {
        resolve = done;
      }),
    );
    const { user } = setup({ review });
    await reveal(user);
    await user.dblClick(screen.getByRole("button", { name: /Good Got it/ }));
    expect(review).toHaveBeenCalledOnce();
    resolve(result);
    await waitFor(() =>
      expect(screen.getByText(/Your progress is saved/)).toBeVisible(),
    );
  });
  it("starts hosted sign-in from the welcome screen", async () => {
    const signIn = vi.fn();
    render(<Welcome signIn={signIn} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Sign in to your space/ }),
    );
    expect(signIn).toHaveBeenCalledOnce();
  });
});
