import { expect, test } from "@playwright/test";
const config = {
  apiUrl: "https://api.example.test",
  authority: "https://cognito.example.test/pool",
  clientId: "test-client",
  authDomain: "https://auth.example.test",
};
test.beforeEach(async ({ page }) => {
  await page.route("**/config.json", (route) =>
    route.fulfill({ json: config }),
  );
});
test("welcome begins authorization-code sign-in with PKCE", async ({
  page,
}) => {
  await page.route("https://auth.example.test/**", (route) =>
    route.fulfill({
      body: "<h1>Hosted sign-in</h1>",
      contentType: "text/html",
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: /Sign in to your space/ }).click();
  await expect(page).toHaveURL(/oauth2\/authorize/);
  const url = new URL(page.url());
  expect(url.searchParams.get("response_type")).toBe("code");
  expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url.searchParams.get("code_challenge")).toBeTruthy();
  expect(url.searchParams.get("state")).toBeTruthy();
});
test("signup opens directly with a fresh PKCE request", async ({ page }) => {
  await page.route("https://auth.example.test/**", (route) =>
    route.fulfill({
      body: "<h1>Create your account</h1>",
      contentType: "text/html",
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Create your account" }).click();
  await expect(page).toHaveURL(/auth.example.test\/signup\?/);
  const url = new URL(page.url());
  expect(url.searchParams.get("response_type")).toBe("code");
  expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url.searchParams.get("code_challenge")).toBeTruthy();
  expect(url.searchParams.get("state")).toBeTruthy();
});
test("invalid callback gives a recovery path and clears sensitive URL parameters", async ({
  page,
}) => {
  await page.goto("/auth/callback?code=invalid&state=missing");
  await expect(page.getByRole("alert")).toContainText("Start again");
  await expect(page).toHaveURL("/");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("button", { name: "Create your account" }),
  ).toBeVisible();
});
test("configuration failure provides a working retry", async ({ page }) => {
  await page.route("**/config.json", (route) =>
    route.fulfill({ status: 503, body: "Unavailable" }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Check your connection");
  await page.route("**/config.json", (route) =>
    route.fulfill({ json: config }),
  );
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("button", { name: "Create your account" }),
  ).toBeVisible();
});
test("review flow is keyboard accessible, retries safely, and fits the screen", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(({ authority, clientId }) => {
    sessionStorage.setItem(
      `oidc.user:${authority}:${clientId}`,
      JSON.stringify({
        access_token: "test-access",
        token_type: "Bearer",
        scope: "openid email aws.cognito.signin.user.admin",
        profile: { sub: "test-user" },
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      }),
    );
  }, config);
  const card = {
    cardId: "one",
    prompt: "I forgot my umbrella.",
    state: "NEW",
    version: 1,
    dueAt: "2026-01-01T00:00:00Z",
  };
  let attempts = 0;
  let reviewId: string;
  await page.route("https://api.example.test/**", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer test-access");
    if (route.request().url().endsWith("/profile"))
      return route.fulfill({
        json: { profile: { deckId: "ur-en-v1" }, decks: [] },
      });
    if (route.request().url().endsWith("/session"))
      return route.fulfill({ json: { cards: [card] } });
    if (route.request().url().endsWith("/answer"))
      return route.fulfill({
        json: {
          cardId: "one",
          answer: "J’ai oublié mon parapluie.",
          explanation: "A useful phrase for a rainy day.",
          examples: [],
        },
      });
    const body = route.request().postDataJSON();
    attempts++;
    if (attempts === 1) {
      reviewId = body.reviewId;
      return route.fulfill({ status: 503, json: {} });
    }
    expect(body.reviewId).toBe(reviewId);
    return route.fulfill({
      json: { ...card, version: 2, nextDueAt: "2030-01-02T12:00:00Z" },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: /Start session/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: card.prompt })).toBeFocused();
  await page.getByLabel(/Your answer/).fill("Mon parapluie");
  await expect(page.getByText("J’ai oublié mon parapluie.")).toHaveCount(0);
  await page.getByRole("button", { name: /Reveal answer/ }).click();
  await expect(page.getByText("J’ai oublié mon parapluie.")).toBeVisible();
  await page.screenshot({
    path: `test-results/review-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: /Good Got it/ }).click();
  await page.getByRole("button", { name: "Retry saved review" }).click();
  await expect(
    page.getByText("Your progress is saved. Let it settle in."),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("new learner enrolls in Urdu and sees right-to-left answers", async ({
  page,
}) => {
  await page.addInitScript(({ authority, clientId }) => {
    sessionStorage.setItem(
      `oidc.user:${authority}:${clientId}`,
      JSON.stringify({
        access_token: "test-access",
        token_type: "Bearer",
        scope: "openid email aws.cognito.signin.user.admin",
        profile: { sub: "new-user" },
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      }),
    );
  }, config);
  const settings = {
    deckId: "ur-en-v1",
    learningLanguage: "ur",
    baseLanguage: "en",
    timezone: "America/Toronto",
    dailyGoal: 3,
  };
  const deck = {
    id: "ur-en-v1",
    name: "Urdu essentials",
    learningLanguage: "ur",
    baseLanguage: "en",
    cardCount: 12,
  };
  let profile: typeof settings | null = null;
  const card = {
    cardId: "ur-en-v1-01",
    prompt: "Hello (a respectful greeting)",
    state: "NEW",
    version: 1,
    dueAt: "2026-01-01T00:00:00Z",
  };
  await page.route("https://api.example.test/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/reminders")
      return route.fulfill({
        json: {
          enabled: false,
          available: false,
          email: null,
          time: "18:00",
          timezone: "America/Toronto",
          version: 0,
          nextReminderAt: null,
        },
      });
    if (path === "/profile")
      return route.fulfill({ json: { profile, decks: [deck] } });
    if (path === "/onboarding") {
      expect(route.request().postDataJSON()).toEqual(settings);
      profile = settings;
      return route.fulfill({ json: { profile } });
    }
    if (path === "/session") return route.fulfill({ json: { cards: [card] } });
    return route.fulfill({
      json: {
        cardId: card.cardId,
        answer: "السلام علیکم",
        explanation: "Assalaam alaikum — a common respectful greeting.",
        examples: [],
      },
    });
  });
  await page.goto("/");
  await expect(page.getByLabel("Language & starter deck")).toHaveValue(
    "ur-en-v1",
  );
  await page.getByLabel("Your timezone").selectOption("America/Toronto");
  await page.screenshot({
    path: `test-results/onboarding-${test.info().project.name}.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: /Create my practice/ }).click();
  await page.getByRole("button", { name: /Start session/ }).click();
  await page.getByRole("button", { name: /Reveal answer/ }).click();
  await expect(page.getByRole("heading", { name: "السلام علیکم" })).toHaveCSS(
    "direction",
    "rtl",
  );
  await expect(page.getByText(/Assalaam alaikum/)).toBeVisible();
  await page.screenshot({
    path: `test-results/urdu-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "السلام علیکم" }),
  ).toBeVisible();
  await expect(page.getByLabel("Language & starter deck")).toHaveCount(0);
  await page.getByRole("button", { name: "Reminders", exact: true }).click();
  await expect(page.getByRole("checkbox")).toBeDisabled();
  await expect(page.getByText(/Email setup is pending/)).toBeVisible();
  await page.screenshot({
    path: `test-results/reminders-${test.info().project.name}.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Back to practice" }).click();
  await expect(
    page.getByRole("heading", { name: "السلام علیکم" }),
  ).toBeVisible();
});
