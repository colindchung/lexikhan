import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Chats } from "./Chats";
import { ApiError } from "./api";
import type { ChatApi, ChatDetail } from "./api";

const initial: ChatDetail = {
  chatId: "one",
  title: "New conversation",
  createdAt: "2026-10-08",
  updatedAt: "2026-10-08",
  turnCount: 0,
  pending: false,
  turns: [],
};
function setup(available = true) {
  const api = {
    chats: vi.fn().mockResolvedValue({ chats: [initial], available }),
    createChat: vi.fn().mockResolvedValue(initial),
    chat: vi.fn().mockResolvedValue(initial),
    sendMessage: vi.fn(),
  } satisfies ChatApi;
  function Harness() {
    const [selected, onSelect] = useState<string>();
    return (
      <Chats
        key={selected}
        selected={selected}
        onSelect={onSelect}
        api={api}
        userId="first"
        close={vi.fn()}
        signIn={vi.fn()}
      />
    );
  }
  render(<Harness />);
  return { api, user: userEvent.setup() };
}
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
it("opens a saved conversation and keeps the draft when returning", async () => {
  const { user } = setup();
  await user.click(
    await screen.findByRole("button", { name: /New conversation/ }),
  );
  await user.type(
    await screen.findByLabelText("Your question"),
    "What is acha?",
  );
  expect(sessionStorage.getItem("lexikhan:chat:first:one")).toBe(
    "What is acha?",
  );
});
it("does not offer paid chat to unapproved accounts", async () => {
  setup(false);
  await screen.findByText(/isn’t enabled/);
  expect(screen.getByRole("button", { name: /New chat/ })).toBeDisabled();
});
it("sends once and renders Markdown without executable HTML or links", async () => {
  const { api, user } = setup();
  api.sendMessage.mockResolvedValue({
    ...initial,
    turnCount: 1,
    turns: [
      {
        messageId: "turn",
        text: "acha",
        answer:
          "**اچھا**: acha: good\n\n- A casual greeting\n\n[Unsafe](javascript:alert(1))<script>no</script>",
        status: "COMPLETE",
        errorMessage: "",
        createdAt: initial.createdAt,
      },
    ],
  });
  api.chat.mockImplementation(async () =>
    api.sendMessage.mock.calls.length
      ? await api.sendMessage.mock.results[0].value
      : initial,
  );
  await user.click(
    await screen.findByRole("button", { name: /New conversation/ }),
  );
  await user.type(await screen.findByLabelText("Your question"), "acha");
  await user.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("A casual greeting");
  expect(screen.getByText("اچھا").tagName).toBe("STRONG");
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText("Unsafe").getAttribute("href")).not.toMatch(
    /^javascript:/,
  );
  expect(api.sendMessage).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Your question")).toHaveValue("");
});
it("retries an ambiguous submission with the same message ID", async () => {
  const { api, user } = setup();
  api.sendMessage.mockRejectedValue(new ApiError(0, "offline"));
  await user.click(
    await screen.findByRole("button", { name: /New conversation/ }),
  );
  await user.type(await screen.findByLabelText("Your question"), "chaabi");
  await user.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByRole("alert");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send question" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Send question" }));
  expect(api.sendMessage.mock.calls[0]).toEqual(api.sendMessage.mock.calls[1]);
});
