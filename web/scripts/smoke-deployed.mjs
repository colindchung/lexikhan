// Credentials arrive over stdin from a disposable development-only smoke user.
// No auth state, screenshots, or traces are written to disk.
import { chromium } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const { url, username, password } = JSON.parse(input);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(30000);
  await page.goto(url);
  await page.getByRole("button", { name: /Sign in to your space/ }).click();
  await page.locator('input[name="username"]:visible').fill(username);
  await page.locator('input[name="password"]:visible').fill(password);
  await page.locator('input[name="signInSubmitButton"]:visible').click();
  await page.waitForURL(`${url}/`);
  await page.getByRole("button", { name: /Start session/ }).click();
  await page.getByLabel(/Your answer/).fill("Merci");
  await page.getByRole("button", { name: /Reveal answer/ }).click();
  await page.getByRole("heading", { name: "Merci", exact: true }).waitFor();
  await page.getByRole("button", { name: /Easy Knew it/ }).click();
  await page.getByText("Your progress is saved. Let it settle in.").waitFor();
  await page.reload();
  await page.getByText("Your progress is saved. Let it settle in.").waitFor();
  await page.getByRole("button", { name: /Sign out/ }).click();
  await page.getByRole("button", { name: /Sign in to your space/ }).waitFor();
  console.log(
    "PASS: CloudFront → hosted Cognito PKCE sign-in → Lambda review → completion → reload → sign-out",
  );
} finally {
  await browser.close();
}
