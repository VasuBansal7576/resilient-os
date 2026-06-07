# HydraDB Ethical Bounty Report

## Summary

I ran a low-impact, authorized assessment of HydraDB public API behavior using the `hydradb_bounty_assessment.py` harness in this repo.

Result: no critical or high-severity security break was found in the tested scope. Auth enforcement, invalid-token rejection, tenant isolation, sub-tenant isolation, malformed JSON error hygiene, and CORS posture all behaved safely in the tested cases.

One low-severity improvement item was observed around immediate recall freshness/exactness after adding a memory to my own tenant.

## Scope

In scope:

- `https://api.hydradb.com`
- My own configured HydraDB API key and tenant/sub-tenant
- Low-impact unauthenticated and invalid-token probes
- Unknown random tenant/sub-tenant recall probes
- Malformed JSON error hygiene
- CORS posture on an authenticated API path
- One benign own-tenant add/recall probe when `--live-write` is used

Out of scope:

- Brute force, load testing, credential attacks, destructive requests, or persistence abuse
- Attempts to read or modify other users' data
- Scanning non-HydraDB assets
- Bypassing Discord, Google Forms, or any non-HydraDB system

## Commands Run

```bash
pytest -q
# 57 passed

python3 hydradb_bounty_assessment.py --json
# 9 pass, 0 findings

python3 hydradb_bounty_assessment.py --json --live-write
# 9 pass, 0 findings, 1 low-severity manual-review item
```

## Passing Security Controls

| Check | Result | Evidence |
|---|---:|---|
| Unauthenticated tenant list | Pass | `GET /tenants/tenant_ids` returned `401 UNAUTHORIZED` |
| Unauthenticated tenant creation | Pass | `POST /tenants/create` returned `401 UNAUTHORIZED` |
| Unauthenticated memory write | Pass | `POST /memories/add_memory` returned `401 UNAUTHORIZED` |
| Unauthenticated processing-status lookup | Pass | `POST /ingestion/verify_processing` returned `401 UNAUTHORIZED` |
| Invalid bearer token | Pass | `GET /tenants/tenant_ids` returned `403 FORBIDDEN` with `Malformed API Key` |
| Malformed JSON | Pass | `POST /recall/recall_preferences` returned `422 VALIDATION_ERROR` without stack/internal leakage |
| Unknown random tenant recall | Pass | returned `404 NOT_FOUND`, no recall data |
| Unknown random sub-tenant recall | Pass | returned `200` with empty `chunks` and `sources` |
| CORS posture | Pass | `Access-Control-Allow-Origin` was `https://app.hydradb.com`, not wildcard-with-credentials |

## Low-Severity Improvement Item

### Exact own-tenant recall immediately after add returns a previous probe

Severity: Low / product-security reliability

Status: Manual review

What happened:

1. The harness added one benign memory to my configured tenant/sub-tenant with a unique probe ID:
   `resilientos-bounty-probe-87afe197e5f54046887aef96e239c6c2`
2. It immediately queried recall for that exact probe ID.
3. The recall response returned a previously indexed probe with the same prefix:
   `resilientos-bounty-probe-effea56b405543ed8646afc6e49f317b`
4. It did not immediately return the exact newly added probe ID.

Why this matters:

- This does not look like an auth or tenant-isolation failure.
- It does suggest that an exact unique query immediately after `add_memory` can return stale or semantically similar older memory while the new memory is still queued/indexing.
- For agent memory systems, this can cause confusing behavior when a user expects a newly added exact marker to be available right away.

Recommended improvements:

- Make `add_memory` response/SDK docs explicit that recall is asynchronous until processing completes.
- Provide a first-class direct lookup by `source_id` or `probe_id`/metadata filter for exact verification workflows.
- Add optional exact-match boosting or metadata filtering in `recall_preferences`.
- Consider returning a clearer ingestion state in recall responses when a matching recently queued memory is not yet searchable.

## Responsible Disclosure Notes

This assessment did not attempt to access third-party/customer data, did not brute force credentials, did not perform load testing, and did not attack non-HydraDB systems. The report is intended as constructive evidence for the HydraDB bounty request.
