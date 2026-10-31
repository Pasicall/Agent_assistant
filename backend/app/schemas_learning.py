from typing import Literal

from pydantic import Field

from app.schemas import StrictRequest


class LearningCreate(StrictRequest):
    knowledge_base_id: str
    document_ids: list[str] = Field(min_length=1, max_length=10)
    goal_text: str = Field(min_length=10, max_length=1000)
    request_key: str = Field(min_length=8, max_length=64)


class CapabilityEdit(StrictRequest):
    id: str
    title: str = Field(min_length=1, max_length=200)
    observable_outcome: str = Field(min_length=1, max_length=2000)
    prerequisite_ids: list[str] = Field(max_length=3)
    source_ids: list[str] = Field(max_length=12)
    coverage_status: Literal["covered", "partial", "missing"]


class PlanEdit(StrictRequest):
    state_version: int = Field(ge=0)
    capabilities: list[CapabilityEdit] = Field(min_length=3, max_length=3)


class VersionRequest(StrictRequest):
    state_version: int = Field(ge=0)


class HintRequest(StrictRequest):
    request_key: str = Field(min_length=8, max_length=64)
    level: int = Field(ge=1, le=4)


class AttemptRequest(StrictRequest):
    request_key: str = Field(min_length=8, max_length=64)
    answer_text: str = Field(min_length=1, max_length=10000)


class NextRequest(VersionRequest):
    request_key: str = Field(min_length=8, max_length=64)
    skip: bool = False


class CorrectionRequest(VersionRequest):
    request_key: str = Field(min_length=8, max_length=64)
    attempt_id: str
    reason: str = Field(min_length=1, max_length=1000)


class CriterionResult(StrictRequest):
    criterion_id: str
    verdict: Literal["met", "partial", "not_met", "insufficient_evidence"]
    student_quotes: list[str] = Field(max_length=4)
    source_ids: list[str] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=1500)


class ObstacleCandidate(StrictRequest):
    code: Literal["interleaving", "stock_value_vs_order_count", "prevention", "expression"]
    criterion_ids: list[str] = Field(min_length=1, max_length=4)
    alternative_explanation: str = Field(min_length=1, max_length=1000)


class AssessmentResult(StrictRequest):
    criteria: list[CriterionResult] = Field(min_length=1, max_length=5)
    obstacle_candidates: list[ObstacleCandidate] = Field(max_length=4)
    feedback: str = Field(min_length=1, max_length=2000)
    reveals_solution: bool
