# Quanta evals

The 25 scenarios cover files, search, notes, reminders, memory, multi-step tasks, approved confirmations, dangerous-action refusals, and six security cases. Every scenario runs in an isolated temporary workspace with mock tools; no real PC actions or network requests are made.

## Replay

Run `python -m tools.evals --mode replay`. Replay consumes the separate JSON response fixtures in `evals/fixtures/`, all labeled with `model: "scripted"`, and writes `evals/results/replay-scripted.json` by default. Replay is deterministic and checks the agent code and tool-execution pipeline; it does **not** measure model quality.

## Live

Live mode is manually invoked and requires an interactive confirmation:

```powershell
python -m tools.evals --mode live --model qwen3:4b-instruct
```

Before running any scenarios, the runner checks the requested model against the local Ollama `/api/tags` endpoint and prints the available models if the name is missing. Use `--ollama-url` for a non-default local Ollama endpoint. Tools remain mocks in live mode.

Each scenario reports separate safety and tool-use results. Tool-call arguments are represented only by argument names and value lengths; tool values are omitted. Failed scenarios include a failure class. Three identical consecutive Ollama service failures stop the run without a final percentage.

## Security replay matrix

The six prompt-injection and boundary cases have separate scripted response fixtures for direct exposure and full-catalog modes. Run both deterministic checks with:

```powershell
python -m tools.evals --mode replay --category security --tool-exposure-mode on --output evals/results/replay-security-direct.json
python -m tools.evals --mode replay --category security --full-catalog --tool-exposure-mode off --output evals/results/replay-security-full-catalog.json
```

Fixtures deliberately attempt the unsafe action after receiving bait from a page, file, screen/UIA, or MCP response. The runner checks confirmation or SSRF/sensitive-path rejection, unchanged mock state, absence of network attempts, and that each canary string is absent from the temporary JSONL trace.
