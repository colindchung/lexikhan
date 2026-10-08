// Credentials arrive over stdin from a disposable development-only smoke user.
// No auth state, screenshots, or traces are written to disk.
import { chromium } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const { url, username, password } = JSON.parse(input);
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  let page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(`${url}/reminders`);
  await page.getByRole("button", { name: /Sign in/ }).click();
  await page.locator('input[name="username"]:visible').fill(username);
  await page.locator('input[name="password"]:visible').fill(password);
  await page.locator('input[name="signInSubmitButton"]:visible').click();
  await page.waitForURL(
    (target) =>
      target.origin === new URL(url).origin &&
      ["/reminders", "/onboarding"].includes(target.pathname),
  );
  await page.getByLabel("Language & starter deck").waitFor();
  if (
    (await page.getByLabel("Language & starter deck").inputValue()) !==
    "ur-en-v1"
  )
    throw new Error("Urdu must be default");
  await page.getByLabel("Your timezone").selectOption("America/Toronto");
  await page.getByRole("button", { name: /Create my practice/ }).click();
  await page.getByText(/Email setup is pending/).waitFor();
  if (new URL(page.url()).pathname !== "/reminders")
    throw new Error("Sign-in return route was lost");
  await page.reload();
  await page.getByText(/Email setup is pending/).waitFor();
  await page.getByRole("button", { name: "Back to practice" }).click();
  await page.getByRole("button", { name: /Start session/ }).click();
  for (const answer of ["السلام علیکم", "شکریہ", "براہ کرم"]) {
    await page.getByLabel(/Your answer/).fill("practice");
    await page.getByRole("button", { name: /Reveal answer/ }).click();
    await page.getByRole("heading", { name: answer, exact: true }).waitFor();
    await page.getByRole("button", { name: /Easy Knew it/ }).click();
  }
  await page.getByText("Progress saved.").waitFor();
  await page.reload();
  await page.getByText("Progress saved.").waitFor();
  await page.getByRole("button", { name: "Reminders", exact: true }).click();
  await page.getByText(/Email setup is pending/).waitFor();
  if (!(await page.getByRole("checkbox").isDisabled()))
    throw new Error("Unapproved email must be unavailable");
  await page.getByRole("button", { name: "Save reminders" }).click();
  await page.getByText("Reminders are off.").waitFor();
  await page.getByRole("button", { name: "Back to practice" }).click();
  // Force a refresh on reopening without logging or exporting any token.
  await page.evaluate(() => {
    const key = Object.keys(localStorage).find(
      (key) => key.startsWith("oidc.user:") && !key.endsWith(":logout"),
    );
    if (!key) throw new Error("Persistent session missing");
    const user = JSON.parse(localStorage.getItem(key));
    if (!user.refresh_token) throw new Error("Refresh token missing");
    user.expires_at = 0;
    localStorage.setItem(key, JSON.stringify(user));
  });
  await page.close();
  page = await context.newPage();
  await page.goto(url);
  await page.getByRole("button", { name: "Reminders", exact: true }).waitFor();
  await page.getByRole("button", { name: /Sign out/ }).click();
  await page.getByRole("button", { name: /Sign in/ }).waitFor();
  console.log(
    "PASS: CloudFront → hosted Cognito PKCE sign-in → Urdu onboarding → Lambda review → completion → reload → close/reopen → token renewal → sign-out",
  );
} finally {
  await browser.close();
}
