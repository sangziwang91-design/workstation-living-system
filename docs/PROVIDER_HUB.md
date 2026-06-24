# WLS Provider Hub

Provider Hub is the loopback-only supplier switchboard for WLS. It provides supplier cards, official registration links, current-account model discovery, connectivity checks, custom OpenAI-compatible endpoints, and preferred-candidate selection.

It is deliberately separated from the canonical planner. Selecting a provider does not attach it to `LivingSystem`, grant write authority, enable paid fallback, or promote provider output into canonical facts.

## Install from the sole project root

```powershell
python -m pip install -e ".[dev,providers]"
```

The `providers` extra installs `keyring`, which connects WLS to the operating-system credential vault. On Windows, the intended backend is Windows Credential Manager. When no usable vault backend exists, WLS refuses persistent credential storage.

## Open the page

```powershell
wls-provider ui
```

The page opens at:

```text
http://127.0.0.1:8765/providers
```

The page uses an ephemeral in-memory UI session. It contains no credential form and stores no credential in browser storage, URLs, Git, Notion, SQLite, or WLS configuration files.

## Add one supplier

1. Click `注册` on the supplier card.
2. Create the credential on the official supplier site.
3. Run the hidden local entry command shown on the card, for example:

```powershell
wls-provider set-key groq
```

4. Paste the credential into the hidden terminal prompt.
5. Return to the page and click `检测与发现模型`.
6. Choose one of the discovered models and save the configuration.
7. Click `设为首选` only after the connectivity probe reports `PASS`.

Selection means `preferred candidate provider`. It does not mean runtime admission.

## Initial registry

| Provider ID | Supplier | Registration |
|---|---|---|
| `github_models` | GitHub Models | <https://github.com/marketplace/models> |
| `gemini` | Google Gemini API | <https://aistudio.google.com/app/apikey> |
| `groq` | GroqCloud | <https://console.groq.com/keys> |
| `mistral` | Mistral API | <https://console.mistral.ai/api-keys> |
| `openrouter` | OpenRouter | <https://openrouter.ai/settings/keys> |
| `deepseek` | DeepSeek API | <https://platform.deepseek.com/api_keys> |
| `custom_openai` | Owner-approved compatible endpoint | owner supplied |

Free tiers, visible models, privacy rules, regional access, and account limits are controlled by each supplier and can change. Provider Hub discovers the current account's visible model list instead of treating a hard-coded model name as permanent truth.

## Commands

```powershell
wls-provider list
wls-provider set-key PROVIDER_ID
wls-provider delete-key PROVIDER_ID
wls-provider configure PROVIDER_ID --model MODEL
wls-provider probe PROVIDER_ID
wls-provider select PROVIDER_ID
wls-provider ui
```

An approved local endpoint can be registered through `custom_openai`. Non-loopback custom endpoints require HTTPS, and URLs containing embedded credentials are rejected.

## Safety boundary

- loopback-only HTTP service;
- ephemeral UI session;
- same-origin checks;
- browser requests containing `credential`, `api_key`, or `token` are rejected;
- no browser credential field;
- strict browser security headers;
- redirects blocked during model-list probes;
- timeout capped at 30 seconds;
- response size capped at 2 MiB;
- provider state separated from credential storage;
- automatic paid fallback permanently disabled in this target;
- provider output has no canonical truth authority;
- provider selection does not modify `RuntimeConfig.provider`;
- no provider receives private WLS context in this target;
- no account rotation, quota evasion, or terms bypass.

## Privacy ceilings

- `P0_PUBLIC_OR_SYNTHETIC`: public, synthetic, or disposable inputs only.
- `P1_REDACTED_ONLY`: redacted snippets without secrets or sensitive private context.
- `OWNER_DEFINED`: explicit owner policy required before use.

## Owner-host verification

Before any provider admission target, the owner must verify on the intended Windows host:

```powershell
python -m pip install -e ".[dev,providers]"
wls-provider set-key PROVIDER_ID
wls-provider probe PROVIDER_ID
wls-provider ui
```

Required evidence:

- vault backend name;
- credential configured without plaintext files;
- one successful model-list probe;
- discovered model list;
- deletion and re-entry behavior;
- no automatic request beyond the explicit probe;
- no paid fallback.

## Next stage

The next stage is one bounded `READ_ONLY_TRIAL` for one selected provider. It must use a frozen public or synthetic benchmark, zero-spend policy, schema-valid output checks, quota and latency evidence, deterministic verification, and owner approval before any runtime attachment.
