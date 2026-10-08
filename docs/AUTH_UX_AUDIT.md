# Authentication UX audit

Reviewed the live welcome, sign-in, signup, and password-recovery entry points,
plus callback/configuration failure handling. Branding is applied through
Cognito's supported classic UI customization API; credentials remain on Cognito.

| Before | After | Why |
| --- | --- | --- |
| Blank banner, gray background, blue buttons | Lexikhan wordmark, cream background, green buttons, serif headings | Continuity with the app and daily emails |
| Signup hidden behind sign-in | Direct Create your account action alongside sign-in | Makes public registration discoverable |
| Small fields and buttons | 48px controls and 16px input text | Easier touch interaction and readable mobile inputs |
| No initial loading feedback | Branded Opening your space status | Explains the wait for configuration and authentication |
| Repeat clicks could create multiple redirects | Busy labels and a synchronous click guard | Prevents overlapping authentication requests |
| Generic error after an invalid callback | Clear recovery screen; callback parameters removed | Gives the user a way back without retaining expired URL parameters |
| Configuration errors mixed with sign-in | Retry reloads configuration; 15-second request timeout | A connection failure no longer leaves an indefinite blank screen |
| Generic verification-code email | Lexikhan subject, name, colors, and clear code instructions | Connects email verification to the account being created |
| Browser Back could retain disabled actions | Reset pending state on pageshow | Allows another attempt after returning from authentication |

## Verification

- Auth-code flow, S256 PKCE, and unique state retained for both entry points.
- Desktop/mobile regression tests cover direct signup, invalid callbacks,
  configuration retry, onboarding, and review flows.
- Live development auth branding verified visually at desktop and 375px width.
- Existing deployment smoke signs in with a disposable development user and
  verifies onboarding and review persistence.
- Production sign-in, signup, and recovery styling verified after deployment.
- No live signup/verification email or password reset was submitted during audit.

## Remaining platform constraints

Cognito classic controls form wording, the page title, link colors, logo alt text,
password guidance, and input autocomplete attributes. CSS cannot add a
show-password control or custom navigation. Those require a different managed
login experience or a custom authentication UI; this change retains the existing
authentication architecture and password policy.

The hosted domain remains the Cognito domain. A first-party authentication
hostname would also require a certificate and a new DNS record.

Verification email content is branded, but Cognito's default delivery service
still sends it. The reminder SES account is sandboxed; switching public signup
emails to that sender would prevent delivery to unverified recipients.

## Deployment

Versioned assets are in `infra/auth/`. The deployment workflow calls
`scripts/deploy_auth.py` for each stage, applies the logo and CSS together,
and reads the customization back to verify it. The verification email template
is managed by CDK. This does not migrate users, rotate credentials, change
consent, or alter the password policy.
