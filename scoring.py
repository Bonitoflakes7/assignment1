"""
Deterministic scoring engine for DOL Prevailing Wage Level determination.

This intentionally contains ZERO machine learning / LLM calls. It is a pure,
auditable rules engine based on the DOL's Prevailing Wage Determination
Policy Guidance (Nonagricultural Immigration Programs, rev. Nov 2009), which
is still the operative framework referenced in current (2025-2026) DOL
guidance and legal commentary.

Real DOL methodology (simplified faithfully, not reinvented):
  - Every job starts at Level I.
  - Education: employer's required education compared to the occupation's
    normal/baseline requirement. +1 point per level above baseline, capped
    at +2 (DOL's own worked example: Bachelor's baseline + PhD required = 2).
  - Experience: employer's required experience compared to the occupation's
    normal range. DOL's real worksheet compares against an O*NET SVP/Job
    Zone RANGE (not a single number), which requires O*NET lookup data we
    don't have here. We approximate with a documented, monotonic bucket
    scale (0/1/2/3 points) so the tool stays deterministic and auditable
    rather than guessing at unavailable O*NET data.
  - Special skills: +1 point if required AND not customary for the role.
  - Supervisory duties: +1 point if required AND not customary for the role.
  - Total points added to the Level I baseline, capped at Level IV.

IMPORTANT: This is a decision-support approximation, not an official DOL
determination. Real filings must use the DOL's actual worksheet/O*NET data
or a National Prevailing Wage Center determination. That disclaimer is
returned in every API response, not just documented here.
"""

from models import WageLevelRequest, FactorBreakdown, WageLevelResponse

LEVEL_NAMES = {1: "Level I", 2: "Level II", 3: "Level III", 4: "Level IV"}

MAX_EDU_POINTS = 2
MAX_EXP_POINTS = 3
MAX_SKILL_POINTS = 1
MAX_SUPERVISORY_POINTS = 1


def score_education(req: WageLevelRequest) -> FactorBreakdown:
    diff = req.required_education.rank - req.baseline_education.rank

    if diff <= 0:
        points = 0
        reasoning = (
            f"Required education ({req.required_education.value}) meets or is below "
            f"the occupation's baseline ({req.baseline_education.value}). No points added."
        )
    elif diff == 1:
        points = 1
        reasoning = (
            f"Required education ({req.required_education.value}) is one level above "
            f"baseline ({req.baseline_education.value}). +1 point."
        )
    else:
        points = MAX_EDU_POINTS
        reasoning = (
            f"Required education ({req.required_education.value}) is {diff} levels above "
            f"baseline ({req.baseline_education.value}). Capped at +{MAX_EDU_POINTS} points "
            f"per DOL guidance (e.g. Bachelor's baseline -> PhD required = 2 points)."
        )

    return FactorBreakdown(
        factor="Education",
        points=points,
        max_points=MAX_EDU_POINTS,
        reasoning=reasoning,
    )


def score_experience(req: WageLevelRequest) -> FactorBreakdown:
    excess = req.required_experience_years - req.baseline_experience_years

    if excess <= 0:
        points = 0
        band = "at or below the occupation's normal experience level"
    elif excess <= 2:
        points = 1
        band = "modestly above normal (0-2 years over baseline)"
    elif excess <= 4:
        points = 2
        band = "notably above normal (2-4 years over baseline)"
    else:
        points = MAX_EXP_POINTS
        band = "substantially above normal (4+ years over baseline)"

    reasoning = (
        f"Required experience ({req.required_experience_years}y) vs. baseline "
        f"({req.baseline_experience_years}y): {band}. +{points} point(s). "
        f"[Approximation of DOL's O*NET Job Zone/SVP range comparison - "
        f"an official filing should use the actual O*NET range for the SOC code.]"
    )

    return FactorBreakdown(
        factor="Experience",
        points=points,
        max_points=MAX_EXP_POINTS,
        reasoning=reasoning,
    )


def score_special_skills(req: WageLevelRequest) -> FactorBreakdown:
    if req.special_skills_required and not req.special_skills_customary:
        points = 1
        reasoning = (
            "Special skills are required and are NOT customary for this occupation. +1 point."
        )
    elif req.special_skills_required and req.special_skills_customary:
        points = 0
        reasoning = (
            "Special skills are required but ARE customary/normal for this occupation. "
            "No points added (per DOL guidance, customary requirements don't raise the level)."
        )
    else:
        points = 0
        reasoning = "No special skills required. No points added."

    return FactorBreakdown(
        factor="Special Skills",
        points=points,
        max_points=MAX_SKILL_POINTS,
        reasoning=reasoning,
    )


def score_supervisory(req: WageLevelRequest) -> FactorBreakdown:
    if req.supervisory_duties_required and not req.supervisory_customary:
        points = 1
        reasoning = (
            "Supervisory duties are required and are NOT customary for this occupation. +1 point."
        )
    elif req.supervisory_duties_required and req.supervisory_customary:
        points = 0
        reasoning = (
            "Supervisory duties are required but ARE customary for this occupation "
            "(e.g. a Manager title). No points added - DOL guidance explicitly warns "
            "against auto-adding a point here, since supervisory occupations' wages "
            "already account for supervision."
        )
    else:
        points = 0
        reasoning = "No supervisory duties required. No points added."

    return FactorBreakdown(
        factor="Supervisory Duties",
        points=points,
        max_points=MAX_SUPERVISORY_POINTS,
        reasoning=reasoning,
    )


def determine_wage_level(req: WageLevelRequest) -> WageLevelResponse:
    breakdown = [
        score_education(req),
        score_experience(req),
        score_special_skills(req),
        score_supervisory(req),
    ]

    total_points = sum(f.points for f in breakdown)
    level_number = min(4, 1 + total_points)

    return WageLevelResponse(
        occupation_title=req.occupation_title,
        soc_code=req.soc_code,
        wage_level=LEVEL_NAMES[level_number],
        level_number=level_number,
        total_points=total_points,
        breakdown=breakdown,
        method=(
            "Deterministic rule-based scoring per DOL Prevailing Wage Determination "
            "Policy Guidance (rev. Nov 2009): start at Level I, add points for "
            "education/experience/skills/supervision above the occupation's normal "
            "baseline, cap at Level IV. No AI/LLM inference is used in this calculation."
        ),
        disclaimer=(
            "This is an automated approximation for planning purposes only. It is NOT "
            "an official DOL prevailing wage determination and must not be used as a "
            "substitute for filing Form ETA-9141 with the National Prevailing Wage "
            "Center (for PERM) or for the wage-level judgment required on an LCA. DOL's "
            "own guidance states this process 'should not be implemented in an "
            "automated fashion' without human review. Consult an immigration attorney "
            "or the official O*NET/OES data at flag.dol.gov before filing."
        ),
    )
