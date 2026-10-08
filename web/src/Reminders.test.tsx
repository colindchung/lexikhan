import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Reminders } from "./Reminders";
import { ApiError } from "./api";

function setup(available = true) {
  const initial = {
    enabled: false,
    available,
    email: available ? "person@example.test" : null,
    time: "18:00",
    timezone: "America/Toronto",
    version: 0,
    nextReminderAt: null,
  };
  const api = {
    reminders: vi.fn().mockResolvedValue(initial),
    saveReminders: vi.fn().mockImplementation(async (body) => ({
      ...initial,
      ...body,
      version: body.version + 1,
    })),
  };
  render(<Reminders api={api} signIn={vi.fn()} signOut={vi.fn()} />);
  return { api, user: userEvent.setup() };
}
it("requires an approved number before opt-in", async () => {
  const { api } = setup(false);
  expect(await screen.findByRole("checkbox")).toBeDisabled();
  expect(screen.getByText(/Email setup is pending/)).toBeVisible();
  expect(api.saveReminders).not.toHaveBeenCalled();
});
it("saves explicit consent and can turn reminders off", async () => {
  const { api, user } = setup();
  await user.click(await screen.findByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save reminders" }));
  await screen.findByText("Reminders saved.");
  expect(api.saveReminders).toHaveBeenLastCalledWith({
    enabled: true,
    time: "18:00",
    timezone: "America/Toronto",
    version: 0,
  });
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save reminders" }));
  await screen.findByText("Reminders are off.");
  expect(api.saveReminders).toHaveBeenLastCalledWith(
    expect.objectContaining({ enabled: false, version: 1 }),
  );
});
it("keeps selections after a failed save and retries the same version", async () => {
  const { api, user } = setup();
  api.saveReminders.mockRejectedValueOnce(new ApiError(503, "Unavailable"));
  await user.click(await screen.findByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save reminders" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("checkbox")).toBeChecked();
  await user.click(screen.getByRole("button", { name: "Save reminders" }));
  await screen.findByText("Reminders saved.");
  expect(api.saveReminders.mock.calls[0]).toEqual(
    api.saveReminders.mock.calls[1],
  );
});
