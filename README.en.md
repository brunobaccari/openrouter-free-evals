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

On PowerShell, use `Copy-Item .env.example .env`. To evaluate another response file with the same contract and five case IDs:

```bash
python evaluate.py --responses responses.json --output results/evaluation.json
```

Exit codes: 0 for approval, 1 for failed responses and 2 for invalid input. Missing or duplicate cases are not accepted as a complete batch.

## Call the hosted API

```bash
python live_openrouter.py
```

Configure the official API URL and free model in `.env`; supply `OPENROUTER_API_KEY` through the environment or the hidden prompt. `.env` is ignored, and process variables take precedence. The API key is configured as a GitHub Actions secret, never as a repository file.

Before generation, the client checks the current catalog: the model must have the `:free` suffix and zero prices. Requests set a zero price ceiling and disable provider fallback. The configured endpoint must be OpenRouter's official HTTPS API. No automatic retries or silent model substitution.

`results/live.json` records timestamp, model, provider, reported usage, responses and case failures. An HTTP error or incomplete batch fails the run. Free-provider limits can still prevent execution.

## Coverage

Five cases cover a 14-day refund policy, missing booking evidence, an instruction injected into a document, conflicting policy sources and a request for internal information. Thirty-four tests verify the evaluator, known mutations, incomplete batches, cost guards, rejection of an invalid API destination and the accuracy and redaction of CI summaries.

`fixtures/cases.json` defines the corpus; `fixtures/responses.json` contains manual reference responses. `evaluate.py` evaluates a batch. `live_openrouter.py` requests real responses. Automatic CI tests the evaluator without model calls; the manual `OpenRouter live` workflow uses the repository secret.

## Failure analysis


## Limits

The free-text answer is checked for presence, length and a literal forbidden token. It is **not semantically compared with the structured facts**. Valid source IDs alone do not prove grounding. Secret detection does not cover encoding or paraphrases. This small corpus is not a general model benchmark or a complete security assessment.

Consult [Actions runs and artifacts](https://github.com/brunobaccari/openrouter-free-evals/actions) for execution reports. Temperature zero does not make runs identical.

References: [free model variants](https://openrouter.ai/docs/guides/routing/model-variants/free) and [provider price limits](https://openrouter.ai/docs/guides/routing/provider-selection).

## GitHub Actions results

In GitHub, open **Actions → workflow → run → Summary**. `Tests` separates unit tests from manual reference responses; download the `results` artifact for `junit.xml` and `evaluation.json`. `OpenRouter live` reports executed cases, failures and incomplete batches; its `live-evaluation` artifact contains `live.json`. Upload and summary also run after failures, with 30-day retention. Missing reports are flagged without claiming a pass.

Expand each case to see its question, synthetic context, required decision/facts/sources, received response and the result of each contract rule. Automatic CI shows manual fixtures; live CI shows actual API responses and provider metadata. There is no required exact answer sentence. Forbidden synthetic tokens are redacted in the summary, while evaluation inputs and artifacts remain unchanged. Rules not evaluated after transport or parsing errors are explicitly marked; they are not counted as passed.

Commit dates in this portfolio were reorganized retroactively; Actions runs retain their actual execution dates.
