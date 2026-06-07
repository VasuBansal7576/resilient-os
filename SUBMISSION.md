# ResilientOS Submission

## What It Is

ResilientOS is a runtime kernel for AI agents that keeps them alive under pressure. It intercepts every planned tool call, records tool-call history, diagnoses failures, checks memory for past recoveries, applies a recovery strategy, and resumes execution.

The hackathon story is simple:

1. A normal agent tool fails.
2. ResilientOS classifies the failure.
3. It asks HydraDB/local memory whether this failure has been survived before.
4. If a learned antibody exists, it executes that strategy first.
5. If not, it falls back to a hard-coded recovery strategy.
6. It stores the outcome so the next run is more resilient.

## Tracks Covered

- **Memory:** HydraDB memory with durable `.resilient_os/local_graph.jsonl` fallback.
- **Tools:** ScrapeGraphAI, Groq, NVIDIA-compatible fallback path, Telegram, Discord.
- **Recovery:** rate limits, auth expiry, timeouts, cascade failures, retry loops, cached fallback, checkpoint rollback.
- **Adaptation:** successful recovery memories are reused as learned antibodies on later failures.
- **Ethical red-team:** authorized local abuse checks block unsafe scrape targets and detect prompt/tool-abuse attempts.

## Verified Proof

Verified locally on 2026-05-25:

```bash
pytest -q
# 37 passed

python3 smoke_runner.py --json
# clean run scraped https://www.pythonanywhere.com/pricing/
# extracted real pricing: Developer $10/month, Custom $10 to $500/month, Beginner $0/month
# rate_limit_first_infection recovered via exponential_backoff
# rate_limit_learned_antibody recovered via hydradb:exponential_backoff
# auth_expiry recovered via refresh_token
# cascade recovered via circuit_breaker/cache and hydradb:circuit_breaker
# final smoke state: 20 memory events, 5 recoveries, 5 immunity events

python3 ethical_hack_runner.py --json
# blocks cloud metadata SSRF, localhost SSRF, file-scheme reads, prompt override,
# destructive tool abuse, and unsafe scrape execution before provider access

bash verify_hydradb.sh
# HydraDB tenant status returned healthy infrastructure
# memories/add_memory queued successfully
# recall_preferences returned the inserted rate-limit memory with graph context
```

The real smoke run used ScrapeGraphAI through Groq to scrape the PythonAnywhere pricing page and sent a real Telegram notification. Chat IDs and secrets are intentionally not committed.

## Reproducible Judge Path

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your own keys to `.env`:

```bash
HYDRADB_API_KEY=...
HYDRADB_TENANT_ID=...
HYDRADB_SUB_TENANT_ID=resilient-os
GROQ_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Then run:

```bash
pytest -q
python3 ethical_hack_runner.py --json
python3 smoke_runner.py --json
bash verify_hydradb.sh
streamlit run dashboard.py
```

## Demo Script

1. Open the dashboard with `streamlit run dashboard.py`.
2. Run the default PythonAnywhere pricing task with no chaos and show `Last run: Clean`.
3. Arm rate-limit chaos on ScrapeGraphAI and run again.
4. Show the `ERROR` event followed by the `Recovered event`.
5. Run the same rate-limit scenario again and show `hydradb:exponential_backoff`.
6. Show the immunity counter increasing.
7. Run `python3 ethical_hack_runner.py --json` to show the local authorized red-team checks.
8. Run `bash verify_hydradb.sh` to show live HydraDB add/recall with graph context.
9. Explain that `AGENT_MODE=scripted` only makes the plan deterministic; tools still call real integrations.

## Honest Caveats

- NVIDIA fallback is implemented and tested, but it only runs live when `NVIDIA_API_KEY` is configured.
- The HydraDB tenant can be any tenant the user owns. `HYDRADB_SUB_TENANT_ID=resilient-os` isolates this project inside a tenant.
- Local JSONL memory is generated fallback memory/cache and should stay out of commits.
- If no notification channel is configured, notification tool calls correctly fail and enter recovery instead of pretending success.
- Ethical hacking coverage is intentionally limited to authorized local abuse checks and public-web URL boundaries; it does not scan or attack third-party systems.
