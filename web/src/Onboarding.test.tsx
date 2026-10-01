import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Onboarding } from "./Onboarding";
import { ApiError } from "./api";

const decks = [
  {
    id: "ur-en-v1",
    name: "Urdu essentials",
    learningLanguage: "ur",
    baseLanguage: "en",
    cardCount: 12,
  },
];
function setup(saved = false) {
  const api = {
    profile: vi.fn().mockResolvedValue({
      profile: saved ? { deckId: "ur-en-v1" } : null,
      decks,
    }),
    enroll: vi.fn().mockImplementation(async (settings) => ({
      profile: { ...settings, createdAt: "2026-01-01T00:00:00Z" },
    })),
  };
  const signIn = vi.fn();
  const view = render(
    <Onboarding api={api} userId="test" signIn={signIn} signOut={vi.fn()}>
      <p>Review ready</p>
    </Onboarding>,
  );
  return { api, signIn, user: userEvent.setup(), ...view };
}
it("defaults to Urdu, persists choices, retries and clears an old empty session", async () => {
  sessionStorage.setItem("lexikhan:session:test", "stale");
  const { api, user } = setup();
  api.enroll.mockRejectedValueOnce(new ApiError(503, "Unavailable"));
  expect(await screen.findByLabelText("Language & starter deck")).toHaveValue(
    "ur-en-v1",
  );
  await user.selectOptions(screen.getByLabelText("Daily practice goal"), "5");
  await user.selectOptions(
    screen.getByLabelText("Your timezone"),
    "America/Toronto",
  );
  await user.click(screen.getByRole("button", { name: /Create my practice/ }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Daily practice goal")).toHaveValue("5");
  await user.click(screen.getByRole("button", { name: /Create my practice/ }));
  await screen.findByText("Review ready");
  expect(api.enroll).toHaveBeenLastCalledWith({
    deckId: "ur-en-v1",
    learningLanguage: "ur",
    baseLanguage: "en",
    dailyGoal: 5,
    timezone: "America/Toronto",
  });
  expect(sessionStorage.getItem("lexikhan:session:test")).toBeNull();
});
it("returning learners bypass setup without enrolling again", async () => {
  const { api } = setup(true);
  await screen.findByText("Review ready");
  expect(api.enroll).not.toHaveBeenCalled();
});
it("recovers a concurrent enrollment by loading the saved profile", async () => {
  const { api, user } = setup();
  await screen.findByLabelText("Daily practice goal");
  api.enroll.mockRejectedValueOnce(new ApiError(409, "Conflict"));
  await user.click(screen.getByRole("button", { name: /Create my practice/ }));
  api.profile.mockResolvedValue({ profile: { deckId: "ur-en-v1" }, decks });
  await user.click(await screen.findByRole("button", { name: "Load profile" }));
  await screen.findByText("Review ready");
});
