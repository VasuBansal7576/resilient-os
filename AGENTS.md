# ResilientOS Agent Guide

## Build Style

Use a gstack-style loop for this repo:

1. Split independent work across specialist agents with disjoint file ownership.
2. Keep one local integration lane for coordination, smoke checks, and final verification.
3. Prefer deterministic planning for repeatability, but production tools must remain real integrations.
4. Run `pytest -q` before handing work back.
5. Keep production paths wired to real integrations; test doubles belong only in tests.

## Useful Commands

```bash
python3 smoke_runner.py --json
pytest -q
streamlit run dashboard.py
bash verify_hydradb.sh
```

## Trust Boundaries

- `AGENT_MODE=scripted` only chooses a deterministic plan; tools still call real integrations.
- Groq is primary, NVIDIA is fallback, HydraDB is memory, and Telegram/Discord is notification.
- `.resilient_os/*.jsonl` is generated fallback memory/cache and should stay out of commits.
