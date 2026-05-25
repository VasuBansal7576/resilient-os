# ResilientOS

ResilientOS is a runtime kernel for AI agents built for the Agents Under Pressure x HydraDB hackathon. It wraps real tool calls, records each success or failure, diagnoses common failure modes, and recovers with learned strategies from HydraDB plus a local resilience cache.

It is not another chat wrapper. It is an immune-system layer for agents: observe tool calls, survive failures, remember recoveries, and reuse successful antibodies on the next run.

## Hackathon Proof

Verified locally on 2026-05-25:

- `pytest -q` -> `37 passed`
- `python3 smoke_runner.py --json` ran clean, rate-limit, learned-antibody, auth-expiry, and cascade scenarios
- Real smoke run scraped `https://www.pythonanywhere.com/pricing/` with ScrapeGraphAI through Groq and sent a Telegram notification
- The scrape returned concrete pricing evidence, including `Developer` at `$10/month`, `Custom` at `$10 to $500/month`, and `Beginner` at `$0/month`
- Smoke sequence ended with `20` memory events, `5` recoveries, and `5` successful immunity memories
- `bash verify_hydradb.sh` added a memory to HydraDB and recalled it with graph context
- `AgentPlanner(mode="auto")` selected Groq and produced real `scrape_url` + `send_notification` tool calls

Covered tracks:

- **Memory:** HydraDB memory plus durable local JSONL resilience fallback
- **Tools:** ScrapeGraphAI, Groq, NVIDIA-compatible fallback path, Telegram, Discord
- **Recovery:** rate-limit, auth, timeout, cascade, retry-loop, cache, and checkpoint strategies
- **Adaptation:** successful recoveries become learned antibodies for later failures

## Judge Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` with HydraDB, at least one LLM provider, and one notification channel. Then run:

```bash
pytest -q
python3 smoke_runner.py --json
bash verify_hydradb.sh
streamlit run dashboard.py
```

The fastest demo path is: scrape the live PythonAnywhere pricing page -> inject rate limit -> recover -> run the same rate limit again -> observe `hydradb:exponential_backoff` learned immunity -> show HydraDB recall and dashboard counters.

## What Ships

- `kernel.py` intercepts real tool calls and retries after recovery.
- `detector.py` classifies rate limits, auth failures, timeouts, cascades, and retry loops.
- `recovery.py` executes learned antibodies before hard-coded fallback strategies.
- `hydradb_client.py` uses HydraDB when configured and durable JSONL fallback only for resilience.
- `integrations.py` runs open-source ScrapeGraphAI, Groq primary, NVIDIA fallback, and Telegram/Discord notifications.
- `agent.py` can use Groq/NVIDIA in `auto` or `llm` mode, with deterministic planning available through `scripted`.
- `dashboard.py` is a Streamlit UI for chaos injection, memory metrics, and execution logs.
- `smoke_runner.py` provides a terminal proof path for clean, chaos, recovery, learned immunity, auth, and cascade scenarios.
- `verify_hydradb.sh` verifies the live HydraDB add-memory and recall path.

There is no ScrapeGraph cloud dependency and no `SGAI_API_KEY`. Web extraction uses the open-source `scrapegraphai` Python package.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run dashboard.py
```

For a real run, configure HydraDB, at least one LLM provider, and at least one notification channel in `.env`.

```bash
HYDRADB_API_KEY=...
HYDRADB_TENANT_ID=...
HYDRADB_SUB_TENANT_ID=resilient-os
GROQ_API_KEY=...
# optional fallback:
NVIDIA_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
# or DISCORD_WEBHOOK_URL=...
```

Only these secrets are needed for the production path:

- `HYDRADB_API_KEY` and `HYDRADB_TENANT_ID`
- optional `HYDRADB_SUB_TENANT_ID` to isolate this app inside an existing tenant
- at least one LLM provider: `GROQ_API_KEY` or `NVIDIA_API_KEY`
- either `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID`, or `DISCORD_WEBHOOK_URL`

## Optional Live HydraDB

```bash
export HYDRADB_API_KEY="your_key"
export HYDRADB_TENANT_ID="resilient-os"
export HYDRADB_SUB_TENANT_ID="resilient-os"
bash verify_hydradb.sh
```

If HydraDB is unreachable, the app still logs memories to `.resilient_os/local_graph.jsonl` so the runtime can keep operating.

The tenant can be any HydraDB tenant you own. `HYDRADB_SUB_TENANT_ID=resilient-os` is used to isolate this project when sharing an existing tenant.

## LLM Providers

```bash
LLM_PROVIDER_ORDER=groq,nvidia
GROQ_API_KEY="your_groq_key"
NVIDIA_API_KEY="your_nvidia_key"
streamlit run dashboard.py
```

Groq is used first when configured. NVIDIA is used as the fallback by the ScrapeGraphAI tool runner and planner when `NVIDIA_API_KEY` is present.

## Test

```bash
pytest -q
python3 smoke_runner.py --json
```

## Dashboard Smoke Flow

The Streamlit dashboard keeps the recovery proof visible as plain text and metrics:

1. Run the default task with no active chaos. `Last run` should show `Clean`.
2. Arm `rate limit on ScrapeGraphAI`, then run again. The log should show an `ERROR` followed by a `Recovered event`.
3. Watch `Immunity counter` reflect stored successful recoveries and any recovered event from the current run.
4. Run the same chaos again to show the HydraDB/local antibody path before the fallback strategy.
5. Check `Memory backend` to confirm whether the run is using HydraDB or the local JSONL fallback.

## Submission Notes

- `.env`, `.resilient_os/*.jsonl`, virtualenvs, caches, and Streamlit secrets are intentionally ignored.
- `AGENT_MODE=scripted` only makes planning deterministic; the tool calls still go through the real integration layer.
- Local JSONL memory is a resilience fallback, not a fake production path. HydraDB remains the primary memory backend when configured.
