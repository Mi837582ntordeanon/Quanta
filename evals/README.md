# Quanta evals

The first ten scenarios exercise files, search, notes, reminders, and refusals of dangerous actions. Every scenario runs in an isolated temporary workspace with mock tools; no real PC actions or network requests are made.

## Replay

Run `python -m tools.evals --mode replay`. Replay consumes the separate JSON response fixtures in `evals/fixtures/`, all labeled with `model: "scripted"`, and writes `evals/results/replay-scripted.json` by default. Replay is deterministic and checks the agent code and tool-execution pipeline; it does **not** measure model quality.

## Live

Live mode is manually invoked and requires an interactive confirmation:

```powershell
python -m tools.evals --mode live --model qwen3:4b-instruct
```

Before running any scenarios, the runner checks the requested model against the local Ollama `/api/tags` endpoint and prints the available models if the name is missing. Use `--ollama-url` for a non-default local Ollama endpoint. Tools remain mocks in live mode.

Each scenario reports separate safety and tool-use results. Tool-call arguments are represented only by argument names and value lengths; tool values are omitted. Failed scenarios include a failure class. Three identical consecutive Ollama service failures stop the run without a final percentage.
