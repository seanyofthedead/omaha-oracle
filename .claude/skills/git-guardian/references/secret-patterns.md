# Secret & Sensitive File Patterns

Use these patterns when scanning files before any push to GitHub. Run against all staged, modified, and untracked files.

## API Key Patterns

### AWS Access Keys
- **Pattern:** `(?:^|[^A-Za-z0-9/+=])AKIA[0-9A-Z]{16}(?:[^A-Za-z0-9/+=]|$)`
- **What it is:** An AWS access key ID — a credential that grants access to Amazon cloud services (servers, databases, storage).
- **Why it's dangerous:** Anyone with this key can spin up servers, access your data, or rack up massive bills on your AWS account.
- **Fix:** Move to `.env` as `AWS_ACCESS_KEY_ID=...`, load via `os.environ` / `process.env`, add `.env` to `.gitignore`.

### AWS Secret Keys
- **Pattern:** `(?:aws_secret_access_key|aws_secret_key|AWS_SECRET)[\s]*[=:]\s*['"]?[A-Za-z0-9/+=]{40}['"]?`
- **What it is:** The secret half of an AWS credential pair.
- **Why it's dangerous:** Combined with an access key, this gives full access to your AWS account.
- **Fix:** Same as AWS access key — move to `.env`.

### GitHub Tokens
- **Pattern:** `(?:ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})`
- **What it is:** A GitHub personal access token — lets someone act as you on GitHub.
- **Why it's dangerous:** Someone could push code to your repos, read private repos, or delete your projects.
- **Fix:** Move to `.env` as `GITHUB_TOKEN=...`, use `os.environ` to access it.

### OpenAI API Keys
- **Pattern:** `sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}`
- **What it is:** An OpenAI API key for ChatGPT / GPT-4 access.
- **Why it's dangerous:** Someone could use your key to make API calls and run up your bill.
- **Fix:** Move to `.env` as `OPENAI_API_KEY=...`.

**Broader OpenAI pattern (catches newer key formats):**
- **Pattern:** `(?:^|[^A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}(?:[^A-Za-z0-9_-]|$)`
- **Note:** May produce false positives in files with JWT tokens or similar. Verify matches manually.

### Anthropic API Keys
- **Pattern:** `sk-ant-[A-Za-z0-9_-]{20,}`
- **What it is:** An Anthropic API key for Claude access.
- **Why it's dangerous:** Someone could use your key for Claude API calls on your bill.
- **Fix:** Move to `.env` as `ANTHROPIC_API_KEY=...`.

### Stripe Keys
- **Pattern:** `(?:sk_live_|pk_live_|rk_live_)[A-Za-z0-9]{20,}`
- **What it is:** A Stripe payment processing key (live mode — real money).
- **Why it's dangerous:** A live secret key (`sk_live_`) could let someone issue refunds, view customer payment data, or transfer funds.
- **Fix:** Move to `.env`, ensure you're using test keys (`sk_test_`) in development. Test keys are safe to have in code but still better in `.env`.

### Google API / Service Account Keys
- **Pattern:** `AIza[0-9A-Za-z_-]{35}`
- **What it is:** A Google Cloud API key.
- **Why it's dangerous:** Depending on permissions, could access Google Cloud services, Maps, etc. on your account.
- **Fix:** Move to `.env` as `GOOGLE_API_KEY=...`.

### Slack Tokens
- **Pattern:** `xox[bpors]-[A-Za-z0-9-]{10,}`
- **What it is:** A Slack bot or user token.
- **Why it's dangerous:** Could let someone read messages, post as you, or access your workspace.
- **Fix:** Move to `.env` as `SLACK_TOKEN=...`.

### Discord Tokens
- **Pattern:** `(?:^|[^A-Za-z0-9.])[A-Za-z0-9]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}`
- **What it is:** A Discord bot or user token.
- **Why it's dangerous:** Could let someone control your Discord bot or impersonate you.
- **Fix:** Move to `.env` as `DISCORD_TOKEN=...`.

## Generic Secret Patterns

### Key-Value Secrets
- **Pattern:** `(?i)(?:password|passwd|pwd|secret|token|api_key|apikey|api_secret|access_token|auth_token|credentials|private_key)\s*[=:]\s*['"][^\s'"]{8,}['"]`
- **What it is:** A hardcoded password, secret, or token assigned to a variable.
- **Why it's dangerous:** Hardcoded secrets in source code are the #1 cause of credential leaks. Once on GitHub, bots scrape them within minutes.
- **Fix:** Move the value to `.env`, reference it via environment variable in code.

### Connection Strings with Credentials
- **Pattern:** `(?i)(?:mysql|postgres|postgresql|mongodb|redis|amqp|mssql):\/\/[^\s:]+:[^\s@]+@[^\s]+`
- **What it is:** A database or service connection string with an embedded username and password.
- **Why it's dangerous:** Gives direct access to your database — someone could read, modify, or delete all your data.
- **Fix:** Move the full connection string to `.env`, or better yet, store user/password separately and build the string in code.

### Bearer Tokens
- **Pattern:** `(?i)(?:bearer|authorization)\s*[=:]\s*['"]?[A-Za-z0-9_\-.]{20,}['"]?`
- **What it is:** An authentication bearer token, often for APIs.
- **Why it's dangerous:** Grants API access as if the token holder is you.
- **Fix:** Move to `.env` or use a secrets manager.

## Private Keys & Certificates

### Private Key Files (PEM format)
- **Pattern:** `-----BEGIN\s(?:RSA\s|DSA\s|EC\s|OPENSSH\s|PGP\s)?PRIVATE KEY-----`
- **What it is:** A private encryption key — the digital equivalent of a physical key to your house.
- **Why it's dangerous:** With your private key, someone can impersonate your servers, decrypt your traffic, or sign code as you.
- **Fix:** NEVER put private keys in code. Store in a secrets manager (AWS Secrets Manager, Vault) or as a CI/CD secret. If this key has been pushed to GitHub even once, consider it compromised and rotate it immediately.

### PGP Private Keys
- **Pattern:** `-----BEGIN PGP PRIVATE KEY BLOCK-----`
- **What it is:** A PGP/GPG private key used for encryption or signing.
- **Why it's dangerous:** Could be used to decrypt messages intended for you or forge your digital signature.
- **Fix:** Same as above — never in code, rotate if exposed.

## High-Risk Filenames

These files should NEVER be committed. If detected in staged or untracked files, flag immediately.

| Filename Pattern | What It Is | Why It's Dangerous |
|---|---|---|
| `.env` | Environment variables file | Usually contains all your project's secrets in one place |
| `.env.local` | Local environment overrides | Same as .env — secrets |
| `.env.production` | Production secrets | Contains live/production credentials |
| `.env.*.local` | Environment-specific local overrides | May contain secrets |
| `id_rsa` / `id_ed25519` / `id_ecdsa` | SSH private keys | Grants SSH access to your servers |
| `*.pem` | Certificate/key files | May contain private keys |
| `*.key` / `*.pfx` / `*.p12` | Key/certificate files | Private keys or certificate bundles |
| `credentials.json` | Google Cloud credentials | Full service account access |
| `service-account*.json` | GCP service account key | Full access to Google Cloud project |
| `*.keystore` / `*.jks` | Java keystores | Contains certificates and private keys |
| `token.json` | OAuth tokens | Grants API access |
| `.htpasswd` | Apache password file | Contains hashed passwords |
| `wp-config.php` | WordPress config | Database credentials, auth keys |
| `*.sqlite` / `*.db` | Database files | May contain user data, passwords |
| `docker-compose*.yml` (with passwords) | Docker config | Often contains hardcoded database passwords |

## Scanning Instructions

When running the secret scan, follow this procedure:

1. Get the list of files to check:
   ```bash
   # Staged files
   git diff --cached --name-only 2>/dev/null
   # Modified but unstaged files
   git diff --name-only 2>/dev/null
   # New untracked files (that would be added)
   git ls-files --others --exclude-standard 2>/dev/null
   ```

2. Check filenames against the high-risk filename list FIRST (fast check).

3. For each file that isn't in the high-risk filename list, run the regex patterns against its contents. Skip binary files and files over 1MB.

4. For each match found:
   - Show the file, line number, and a preview of the match (with the actual secret partially redacted: show first 4 and last 2 characters only)
   - Explain what it is and why it's dangerous (use the descriptions above)
   - Offer to fix it: move to `.env`, update code to use env var, add to `.gitignore`

5. This scan is a 🔴 BLOCKER — the push CANNOT proceed until all secrets are resolved or explicitly acknowledged by the developer as false positives.

6. This check can NEVER be skipped, not even in Quick Mode.

## False Positive Handling

Some patterns may match non-secret strings. If the developer says "that's not a real secret" or "that's a test value":
- Ask: "Just to be safe — is this a test/dummy value, or a real credential? If it's real, I really need to move it out of the code."
- If confirmed as test/dummy: allow it but suggest adding a comment like `# TEST KEY — not a real credential` to prevent future flags
- If confirmed as real: block the push and fix it
