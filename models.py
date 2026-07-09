"""
Pydantic models for the DOL Prevailing Wage Level determination service.

All validation happens here at the boundary. Nothing enters the scoring
engine (scoring.py) unless it has already passed strict type/range/logic
checks. This is what keeps the service from producing "confident nonsense" -
bad input is rejected loudly (HTTP 422) rather than silently scored.
"""

from enum import Enum
from typing import Optional, Dict, List
from pydantic import BaseModel, Field, field_validator, model_validator


class EducationLevel(str, Enum):
    """
    Ordered education levels. Order matters - it is used to compute how many
    "steps" above baseline the employer's requirement sits, per DOL Appendix D
    methodology (e.g. Bachelor's baseline + Master's required = 1 step).
    """
    NONE = "none"
    HIGH_SCHOOL = "high_school"
    ASSOCIATE = "associate"
    BACHELORS = "bachelors"
    MASTERS = "masters"
    DOCTORATE = "doctorate"

    @property
    def rank(self) -> int:
        order = [
            EducationLevel.NONE,
            EducationLevel.HIGH_SCHOOL,
            EducationLevel.ASSOCIATE,
            EducationLevel.BACHELORS,
            EducationLevel.MASTERS,
            EducationLevel.DOCTORATE,
        ]
        return order.index(self)


class WageLevelRequest(BaseModel):
    # Reference fields only - not used in scoring, but required so the
    # response/audit trail is tied to a specific job, not a black box.
    occupation_title: str = Field(..., min_length=2, max_length=200)
    soc_code: Optional[str] = Field(
        default=None,
        pattern=r"^\d{2}-\d{4}(\.\d{2})?$",
        description="O*NET-SOC code, e.g. 15-1252.00. Optional but recommended.",
    )

    # --- Education factor ---
    baseline_education: EducationLevel = Field(
        ..., description="The occupation's NORMAL/typical education requirement per O*NET."
    )
    required_education: EducationLevel = Field(
        ..., description="What the employer's job offer actually requires."
    )

    # --- Experience factor ---
    baseline_experience_years: float = Field(
        ..., ge=0, le=20,
        description="Occupation's typical/normal years of experience (O*NET Job Zone).",
    )
    required_experience_years: float = Field(
        ..., ge=0, le=40,
        description="Years of experience the employer's job offer requires.",
    )

    # --- Special skills factor ---
    special_skills_required: bool = Field(
        default=False,
        description="Does the job require special skills (e.g. a specific language, niche tool, license)?",
    )
    special_skills_customary: bool = Field(
        default=True,
        description="If special skills are required, are they customary/normal for this occupation? "
                     "(True = normal for the role, so no extra point; False = unusual, adds a point.)",
    )

    # --- Supervisory factor ---
    supervisory_duties_required: bool = Field(
        default=False, description="Does the job require supervising other workers?"
    )
    supervisory_customary: bool = Field(
        default=True,
        description="Is supervision a customary/inherent part of this occupation "
                     "(e.g. 'Manager' titles)? True = customary, no extra point.",
    )

    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("occupation_title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("occupation_title cannot be blank or whitespace only")
        return v

    @model_validator(mode="after")
    def sanity_checks(self) -> "WageLevelRequest":
        # Logical consistency checks - these catch impossible/contradictory
        # inputs that would otherwise silently produce a "plausible-looking"
        # but meaningless score.
        if self.required_experience_years < 0 or self.baseline_experience_years < 0:
            raise ValueError("experience years cannot be negative")

        if self.special_skills_required is False and self.special_skills_customary is False:
            raise ValueError(
                "special_skills_customary=False has no meaning when "
                "special_skills_required=False. Set special_skills_customary=True "
                "(default) or omit it."
            )

        if self.supervisory_duties_required is False and self.supervisory_customary is False:
            raise ValueError(
                "supervisory_customary=False has no meaning when "
                "supervisory_duties_required=False. Set supervisory_customary=True "
                "(default) or omit it."
            )

        return self


class FactorBreakdown(BaseModel):
    factor: str
    points: int
    max_points: int
    reasoning: str


class WageLevelResponse(BaseModel):
    occupation_title: str
    soc_code: Optional[str]
    wage_level: str            # "Level I" ... "Level IV"
    level_number: int          # 1-4
    total_points: int
    breakdown: List[FactorBreakdown]
    method: str
    disclaimer: str


class ErrorResponse(BaseModel):
    error: str
    detail: str
    fields: Optional[Dict[str, str]] = None
