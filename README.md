# DOL Prevailing Wage Level Estimator (FastAPI)

A small, deterministic backend service that estimates a DOL prevailing wage
level (Level I-IV) from job requirements, based on the DOL's actual
**Prevailing Wage Determination Policy Guidance** point-worksheet
methodology.

## Why it's built this way (important)

This is **not** an LLM making a judgment call — there is no model in the
request path at all. `scoring.py` is pure arithmetic: every point awarded is
traceable to a specific rule from DOL's published guidance. That's the
guard against "hallucinated" results the way you'd get from asking a chat
model to just eyeball a level.

The second guard is **strict input validation** (`models.py`). Nothing
reaches the scoring engine unless it passes:
- type/enum checks (education must be one of 6 known levels, SOC code must
  match the real `XX-XXXX(.XX)` format, experience years must be non-negative
  and within a sane range)
- cross-field logic checks (e.g. you can't mark a skill as "not customary"
  if you also said the skill isn't required — that's a contradiction, not
  data)

Bad input gets a clean `422` with a field-by-field explanation, not a
silently-computed wrong answer.

## The methodology (real DOL rules, simplified where noted)

Per DOL's 2009 Prevailing Wage Determination Policy Guidance (still the
operative framework):

1. Every job **starts at Level I**.
2. **Education**: employer's required education vs. the occupation's normal
   (O*NET) baseline. +1 point per level above, capped at +2.
3. **Experience**: employer's required years vs. the occupation's normal
   range. DOL's real worksheet compares against an O*NET SVP/Job Zone
   *range*, which needs O*NET lookup data this service doesn't have baked
   in — so this factor uses a documented, monotonic bucket scale (0/1/2/3
   points) as an explicit **approximation**. This is flagged in every
   response's factor reasoning.
4. **Special skills**: +1 point only if skills are required *and* not
   customary for the occupation.
5. **Supervisory duties**: +1 point only if supervision is required *and*
   not customary for the occupation (DOL guidance explicitly says
   supervisory occupations' wages already price in supervision, so this
   should NOT auto-add a point).
6. Points sum, add to the Level I baseline, cap at Level IV.

Every API response includes:
- a full point-by-point `breakdown` with reasoning (auditable, not a black box)
- a `disclaimer` stating this is a planning aid, not an official determination

**This tool does not replace filing Form ETA-9141 or an attorney's review.**
DOL's own guidance says the process "should not be implemented in an
automated fashion" without human judgment — treat this as a fast first-pass
estimator, not a filing-ready determination.

## Running it

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs

## Example request

```bash
curl -X POST http://localhost:8000/v1/wage-level \
  -H "Content-Type: application/json" \
  -d '{
    "occupation_title": "Software Developer",
    "soc_code": "15-1252.00",
    "baseline_education": "bachelors",
    "required_education": "masters",
    "baseline_experience_years": 2,
    "required_experience_years": 7,
    "special_skills_required": true,
    "special_skills_customary": false,
    "supervisory_duties_required": true,
    "supervisory_customary": false
  }'
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `occupation_title` | string | Required, for reference/audit trail |
| `soc_code` | string | Optional, must match `NN-NNNN` or `NN-NNNN.NN` |
| `baseline_education` | enum | `none`, `high_school`, `associate`, `bachelors`, `masters`, `doctorate` — the occupation's *normal* requirement |
| `required_education` | enum | same options — what the employer's job offer requires |
| `baseline_experience_years` | float 0-20 | occupation's typical experience |
| `required_experience_years` | float 0-40 | what the job offer requires |
| `special_skills_required` | bool | default false |
| `special_skills_customary` | bool | default true; only meaningful if `special_skills_required=true` |
| `supervisory_duties_required` | bool | default false |
| `supervisory_customary` | bool | default true; only meaningful if `supervisory_duties_required=true` |
| `notes` | string | optional, max 1000 chars |

## Error handling

- **422** — validation failure (bad enum, out-of-range number, contradictory
  logic, malformed SOC code, missing required field). Response includes a
  `fields` map naming exactly what's wrong.
- **500** — unexpected server error. Logged server-side with a request ID;
  client gets a generic message (no stack trace leakage).
- Every request gets an `X-Request-ID` header and a log line for traceability.

## Endpoints

- `GET /health` — liveness check
- `POST /v1/wage-level` — the estimator
- `GET /docs` — Swagger UI
