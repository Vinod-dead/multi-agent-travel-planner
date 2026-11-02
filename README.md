# Multi-Agent Travel Planner

Tell it where you want to go, how long, and how much you can spend. A team of AI agents researches the destination, then looks at flights, places to stay, local transport and activities **in parallel**, and a final budget check adds everything up. You get a day-by-day plan in a web page you can print or download.

- Works with **Ollama** (default, runs on your computer), **OpenAI**, LM Studio, Groq, or any OpenAI-compatible server.
- Works with **no model at all**: if the model is off or answers badly, you get a clearly labelled budget-based estimate instead of an error.
- No build step for the web UI. Python only.

## Quick start (VS Code)

1. Unzip, then **File > Open Folder** in VS Code.
2. Open a terminal (**Terminal > New Terminal**) and run:

   **Windows**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   python -m travel_planner serve --open
   ```
   **macOS / Linux**
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   python -m travel_planner serve --open
   ```
   Shortcut: double-click `start.bat` (Windows) or run `./start.sh` (macOS/Linux). They do all of the above.

3. Your browser opens at <http://localhost:8000>. Fill in the form and press **Plan my trip**.

Press **F5** in VS Code to run it with the debugger (`.vscode/launch.json` is included). Python 3.10 or newer is required.

### Using a local model with Ollama

```
ollama pull qwen2.5:3b
ollama serve          # skip if Ollama is already running in the background
python -m travel_planner check
```

`check` tells you whether the model is reachable and answering. The coloured chip at the top of the web page shows the same thing; click it to re-check.

Bigger models give much better plans. If your computer can run it, try `qwen2.5:7b` or `llama3.1:8b` and set `LLM_MODEL` in `.env`. A 3B model on a CPU can take a few minutes per plan, which is normal; the progress board shows what is happening.

### Using NVIDIA NIM

Get a key at <https://build.nvidia.com> (it starts with `nvapi-`), then edit `.env`:

```
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.3-70b-instruct
LLM_API_KEY=nvapi-your-key-here
LLM_CONCURRENCY=2
```

Models get retired on NVIDIA's side (`HTTP 410 Gone`; `meta/llama-3.3-70b-instruct` ended on 2026-08-26). Run
`python -m travel_planner models` to list what your key can use, and pick a current one, for example
`nvidia/nemotron-3.5-lightning-30b-a3b`. Nemotron 3 models "think" before answering, so also add this line,
otherwise they can return an empty reply:

```
LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
```

Restart the app. Never share the key or commit `.env` (it is already in `.gitignore`).

### Using OpenAI or another hosted model

Edit `.env`:

```
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
```

## Configuration

All settings live in `.env` (see `.env.example` for comments). The important ones:

| Setting | Default | What it does |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen2.5:3b` | Model name (you can also pick one in the web form) |
| `LLM_API_KEY` | `ollama` | Key for hosted services (Ollama ignores it) |
| `LLM_ENABLED` | `true` | `false` = never call a model, always estimate |
| `LLM_TIMEOUT` | `180` | Seconds to wait for one answer |
| `LLM_CONCURRENCY` | `2` | Model calls at the same time |
| `TAVILY_API_KEY` | empty | Optional: adds real web sources to the overview |
| `DEFAULT_CURRENCY` | `INR` | Pre-selected currency |
| `PORT` | `8000` | Web app port |

## How it works

```
                       +--> Flight search ------+
Destination research --+--> Accommodation ------+--> Budget check --> Plan
                       +--> Local transport ----+
                       +--> Activity planning --+
```

Orchestrated with **LangGraph** (`travel_planner/planner.py`). Each agent:

1. asks the model for JSON in a small fixed shape,
2. repairs and validates the answer (small models often add Markdown, trailing commas, `"₹5,000"` instead of `5000`, or get cut off mid-answer),
3. sanity-checks it (impossible prices are rejected),
4. falls back to an offline estimate for that section if the model cannot help. If the model server is down, the first failure switches every agent to estimates straight away instead of waiting on timeouts.

The **budget agent does not use the model**: adding up costs is arithmetic, and models get arithmetic wrong. If the plan is over budget it switches to the cheapest stay and flights, and tells you if it still does not fit.

Every section in the result is tagged **Model suggestion** or **Offline estimate**, so you always know what you are looking at.

> **Prices are estimates, not live fares.** The app has no access to airline or hotel booking systems. Treat amounts as planning guidance and check real prices before you book.

## Project layout

```
travel_planner/
  api.py          FastAPI app (JSON API + serves the web UI)
  planner.py      LangGraph workflow
  agents/         destination, flights, lodging, transport, activities, budget
  llm.py          OpenAI-compatible client + JSON repair
  fallback.py     offline estimator
  models.py       request / plan data models
  store.py        SQLite history      jobs.py   background runner
  export.py       Markdown export     research.py  optional Tavily search
  web/            index.html, style.css, app.js (no build step)
tests/            71 tests, including a fake model server that misbehaves on purpose
```

## Command line

```
python -m travel_planner serve                 # web app
python -m travel_planner check                 # test the model connection
python -m travel_planner plan --origin Pune --destination Goa --days 4 --budget 60000 --interests beaches food
python -m travel_planner plan --budget 80000 --days 5 --json     # let it choose the destination
```

## API

| Method | Path | |
|---|---|---|
| `GET` | `/api/health` | model status and available models |
| `POST` | `/api/plans` | start a plan, returns `{"id": ...}` (HTTP 202) |
| `GET` | `/api/plans/{id}` | status, per-agent progress, and the result when done |
| `GET` | `/api/plans` | history |
| `POST` | `/api/plans/{id}/cancel` | stop a running plan |
| `DELETE` | `/api/plans/{id}` | delete |
| `GET` | `/api/plans/{id}/export?format=md\|json` | download |

Interactive docs: <http://localhost:8000/docs>.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests run against a fake OpenAI-compatible server (`tests/fake_llm.py`) in modes `clean`, `messy`, `garbage`, `cut`, `empty`, `missing` and `down`, so the failure handling is genuinely exercised. You can also run it by hand for a demo without any model:

```
python -m tests.fake_llm --port 11500 --mode messy
# in another terminal, with LLM_BASE_URL=http://localhost:11500/v1 and LLM_MODEL=fake in .env
```

## Troubleshooting

| You see | Cause and fix |
|---|---|
| Chip says **No model: offline estimates** | Ollama is not running. Start it (`ollama serve`) and click the chip. |
| **Model "x" not installed** | `ollama pull x`, or change `LLM_MODEL` to a model you have (`ollama list`). |
| Plan takes minutes | Normal for small CPU models. Lower `LLM_CONCURRENCY` to 1, use a shorter trip, or a smaller/faster model. If you see timeouts, raise `LLM_TIMEOUT`. |
| Plan says **Partly estimated** | The model returned unusable output for some sections. Each warning says which. A larger model fixes most of these. |
| `Address already in use` | Another app uses port 8000. Run with `--port 8001` or set `PORT` in `.env`. |
| `ModuleNotFoundError` | The virtual environment is not active or packages are missing. Run `pip install -r requirements.txt` inside `.venv`. |
| Garbled characters in a Windows terminal | Use the web page, or a terminal that supports UTF-8 such as Windows Terminal. |

## What changed from the original project

The original code imported a package that did not match its folder name, shipped two copies of the code, called Stagehand classes that do not exist, and its tests mocked LangGraph completely so they could not catch any of this. Flight search always returned the same fake NYC to LAX flights, and model answers were discarded. This version is a clean rebuild:

- one package, one entry point, 71 tests that run the real code paths
- model answers are actually used, validated and repaired; no hard-coded fake data presented as real
- removed heavy or broken dependencies (`stagehand-py`, `agents-sdk-models`, `supabase`, `firecrawl`, `openai-agents`); history is stored in a local SQLite file instead
- new: web UI, REST API, progress board, history, Markdown/JSON export, offline mode

Browser automation for live price scraping, and cloud storage, were dropped because they could not run as written. They can be added back as separate agents on top of this structure.

## License

MIT. Originally based on the work of Bjorn Melin (see `LICENSE`).
