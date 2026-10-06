# OpenRouter — response evaluation with Python

[Versão em português](README.md)

A small support-assistant corpus evaluated against real responses from OpenRouter's hosted API. Checks structured facts, answer/abstain/handoff decisions, references and literal leakage of a synthetic secret. The expected answers are authored separately and are not sent to the model.

## Run the evaluator

Python 3.12 or later; CI uses 3.14. Create and activate a virtual environment, then:

```bash
cp .env.example .env
python -m pip install -r requirements.txt
python -m pytest -q --junitxml=results/junit.xml
python evaluate.py
```

On PowerShell, use `Copy-Item .env.example .env`. To evaluate another response file with the same contract and all case IDs in the corpus:

```bash
python evaluate.py --responses responses.json --output results/evaluation.json
```

Exit codes: 0 for approval, 1 for failed responses and 2 for invalid input. Missing or duplicate cases are not accepted as a complete batch.

## Contract sent to the model

The client requires `json_schema` with all five fields and `require_parameters: true`. The schema specifies types and allowed fields; it does not contain case-specific deadlines, source IDs or expected decisions. Expected values stay in the evaluator. The catalog must advertise structured outputs and optional reasoning.

Optional reasoning is disabled for this extraction task: the previous run consumed its reasoning budget and returned empty or truncated content. The output limit remains 4,096 tokens. Abstention always requires `facts: {}` and `sources: []`, even when a document explains the missing information. Responses are evaluated without local repairs or filled-in fields.

References: [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs) and [reasoning budget](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Call the hosted API

```bash
python live_openrouter.py
```

Configure the official API URL and free model in `.env`; supply `OPENROUTER_API_KEY` through the environment or the hidden prompt. `.env` is ignored, and process variables take precedence. The API key is configured as a GitHub Actions secret, never as a repository file.

Before generation, the client checks the current catalog: the model must have the `:free` suffix and zero prices. Requests set a zero price ceiling and disable provider fallback. The configured endpoint must be OpenRouter's official HTTPS API. Only HTTP 429 receives bounded retries; no silent model substitution.

`results/live.json` records timestamp, model, provider, reported usage, responses and case failures. An HTTP error or incomplete batch fails the run. Free-provider limits can still prevent execution.

## HTTP 429 resilience

Catalog and generation requests share one policy: an initial request plus up to three retries per request. `Retry-After` supports seconds and HTTP dates. Without a valid header, exponential backoff starts at five seconds, includes jitter and caps each wait at 60 seconds.

A **120-second total wait budget** is shared across the catalog and all cases. If the server requests more time than remains, execution stops without retrying early. A daily quota can remain exhausted despite retries.

Configure `.env` using `.env.example`:

| Variable | Default | Accepted range |
| --- | --- | --- |
| `OPENROUTER_MAX_RETRIES` | 3 | 0–5 per request; 0 disables retries |
| `OPENROUTER_RETRY_BASE_SECONDS` | 5 | 1–60 seconds |
| `OPENROUTER_RETRY_BUDGET_SECONDS` | 120 | 0–300 seconds per run |

Artifacts and summaries record attempts, waits and stop reasons (`retry_limit` or `wait_budget`). Contract failures, invalid JSON, other HTTP statuses and timeouts do not trigger retries. The model, context, expected results and cost guards stay unchanged between attempts. Persistent 429s fail the workflow and preserve the incomplete batch.

Transport tests simulate 429s without real waits. They cover recovery, exhaustion, invalid headers, HTTP dates, ineligible errors and a contract failure that must not be retried.

Reference: [OpenRouter rate-limit guidance](https://openrouter.ai/docs/api_reference/limits), reviewed October 6, 2026.

## Coverage

Twenty cases cover refund and cancellation rules, applicable products and plans, missing information, policy versions, conflicting and corroborating sources, negation, English input, irrelevant numbers and prompt injection. Unit tests verify the evaluator, known mutations, invalid corpora, interrupted generation, incomplete batches, cost guards, rejection of an invalid API destination and the accuracy and redaction of CI summaries.

`fixtures/cases.json` defines the corpus; `fixtures/responses.json` contains manual reference responses. `evaluate.py` evaluates a batch. `live_openrouter.py` requests real responses. Automatic CI tests the evaluator without model calls; the manual `OpenRouter live` workflow uses the repository secret.

## Change acceptance and triage

| Signal | Decision and next step |
| --- | --- |
| Empty corpus, duplicate IDs or expected sources missing from context | Block before API calls. Fix the corpus and review the rule with the product owner. |
| Incorrect deadline, decision, source or forbidden token | Fail the response. QA isolates the case; development investigates the prompt and integration. Do not change expected values to match model output. |
| Exhausted 429 budget, timeout or incomplete batch | Inconclusive model quality. Check provider availability before rerunning; retain the failed run for comparison. |
| Non-`stop` finish reason, malformed envelope or invalid JSON | Fail generation even if part of the response looks correct. Do not repair or retry content to obtain a pass. |
| All contracts pass | Manually check that the text agrees with the facts and sources support the answer. This decision is outside the automated gate. |

A rule change should update the synthetic context, expected result and a mutation the evaluator must reject together. Review the specific failure and its effect on customer support; aggregate accuracy cannot compensate for leakage or an incorrect deadline. Artifacts support that review without committing responses to Git history.

Corpus validation runs before catalog lookup and generation. The summary fails when reports are missing or no tests were recorded. The [API contract](https://openrouter.ai/docs/api_reference/overview) defines finish reasons; this client accepts only `stop` before evaluating content.

## Limits

The free-text answer is checked for presence, length and a literal forbidden token. It is **not semantically compared with the structured facts**. Valid source IDs alone do not prove grounding. Secret detection does not cover encoding or paraphrases. This small corpus is not a general model benchmark or a complete security assessment.

Consult [Actions runs and artifacts](https://github.com/brunobaccari/openrouter-free-evals/actions) for execution reports. Temperature zero does not make runs identical.

References: [free model variants](https://openrouter.ai/docs/guides/routing/model-variants/free) and [provider price limits](https://openrouter.ai/docs/guides/routing/provider-selection).

## GitHub Actions results

In GitHub, open **Actions → workflow → run → Summary**. `Tests` separates unit tests from manual reference responses; download the `results` artifact for `junit.xml` and `evaluation.json`. `OpenRouter live` reports executed cases, failures and incomplete batches; its `live-evaluation` artifact contains `live.json`. Upload and summary also run after failures, with 30-day retention. Missing reports are flagged without claiming a pass.

Expand each case to see its question, synthetic context, required decision/facts/sources, received response and the result of each contract rule. Automatic CI shows manual fixtures; live CI shows actual API responses and provider metadata. There is no required exact answer sentence. Forbidden synthetic tokens are redacted in the summary, while evaluation inputs and artifacts remain unchanged. Rules not evaluated after transport or parsing errors are explicitly marked; they are not counted as passed.

Commit dates in this portfolio were reorganized retroactively; Actions runs retain their actual execution dates.
