# WLS Provider Hub

Provider Hub is a loopback-only supplier switchboard for WLS. It provides provider cards, official registration links, model discovery, connectivity checks, custom OpenAI-compatible endpoints, and preferred-candidate selection.

It is deliberately separated from the canonical planner. Selecting a provider does not attach it to `LivingSystem`, grant write authority, or enable paid fallback.

## Install the secure local vault adapter

```powershell
cd source
python -m pip install -e ".[dev]"
python -m pip install -r requirements-provider-hub.txt
```

The optional `keyring` package connects WLS to the operating-system credential vault. On Windows, the intended backend is Windows Credential Manager. When no approved vault backend is available, WLS refuses persistent credential storage.

## Open the provider page

```powershell
cd source
python -m wls.provider_cli ui
```

The page opens on the local loopback address:

```text
http://127.0.0.1:8765/providers
```

The page uses an ephemeral in-memory UI session. It does not contain a credential form and never stores credentials in the browser.

## Add a provider

1. Click `注册` on a provider card and create a credential on the official supplier site.
2. Run the secure terminal command shown on that card. Example:

```powershell
python -m wls.provider_cli set-key groq
```

3. Paste the credential into the hidden terminal prompt.
4. Return to the page and click `检测与发现模型`.
5. After a successful check, click `设为首选` to mark a preferred candidate.

Provider Hub stores only non-secret metadata in:

```text
<WLS_HOME>/state/provider_hub.json
```

Credentials do not enter Git, Notion, SQLite, configuration files, logs, URLs, or browser storage.

## Initial provider registry

| Provider ID | Supplier | Official registration |
|---|---|---|
| `github_models` | GitHub Models | <https://github.com/marketplace/models> |
| `gemini` | Google Gemini API | <https://aistudio.google.com/app/apikey> |
| `groq` | GroqCloud | <https://console.groq.com/keys> |
| `mistral` | Mistral API | <https://console.mistral.ai/api-keys> |
| `openrouter` | OpenRouter | <https://openrouter.ai/settings/keys> |
| `deepseek` | DeepSeek API | <https://platform.deepseek.com/api_keys> |
| `custom_openai` | Owner-approved compatible endpoint | owner supplied |

Free tiers, available models, data-use rules, and regional availability are supplier-controlled and may change. Provider Hub discovers the current account's visible model list instead of treating presets as permanent truth.

## Commands

```powershell
python -m wls.provider_cli list
python -m wls.provider_cli set-key PROVIDER_ID
python -m wls.provider_cli delete-key PROVIDER_ID
python -m wls.provider_cli configure PROVIDER_ID --model MODEL
python -m wls.provider_cli probe PROVIDER_ID
python -m wls.provider_cli select PROVIDER_ID
python -m wls.provider_cli ui
```

An approved local endpoint can be registered through `custom_openai`. Non-loopback custom endpoints require HTTPS, and URLs containing embedded credentials are rejected.

## Current safety boundary

- loopback-only UI;
- ephemeral UI session;
- same-origin checks;
- strict browser security headers;
- no browser credential input;
- redirects blocked during model-list probes;
- bounded timeout and response size;
- provider state separated from credential storage;
- automatic paid fallback disabled;
- provider output has no canonical truth authority;
- provider selection does not change `RuntimeConfig.provider`;
- no provider receives private WLS context in this version.

## Privacy ceilings

- `P0_PUBLIC_OR_SYNTHETIC`: public, synthetic, or disposable inputs only.
- `P1_REDACTED_ONLY`: redacted snippets without secrets or sensitive private context.
- `OWNER_DEFINED`: explicit owner policy required before use.

## Next stage

The next evolution target is one bounded `READ_ONLY_TRIAL` for one selected provider. It must use a frozen public or synthetic benchmark, zero-spend policy, structured-output tests, latency and quota evidence, comparison against local baselines, and owner approval before any runtime attachment.
