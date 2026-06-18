"""
models.py
=========
Pydantic schemas used to validate the structured JSON response returned by
Groq (llama-3.3-70b-versatile).

Using strict Pydantic validation ensures that any malformed or hallucinated AI
output is caught before it ever reaches the email-delivery step.
"""

from typing import Literal
from pydantic import BaseModel, Field


class JobEvaluation(BaseModel):
    """
    Represents a single job evaluated and scored by the AI.

    Fields
    ------
    job_title       : Title of the role as extracted from the job posting.
    company         : Hiring company name.
    match_tier      : One of three explicit tiers:
                        - Tier_A_Strong  → strong Backend Engineer match
                        - Tier_B_Fuzzy   → partial / adjacent match
                        - Tier_C_None    → irrelevant, will be discarded
    match_score     : Integer 1–100 representing alignment strength.
    logic_summary   : One-sentence rationale for the tier/score assignment.
    clean_apply_url : Canonical application URL (tracking params stripped).
    """

    job_title: str
    company: str
    match_tier: Literal["Tier_A_Strong", "Tier_B_Fuzzy", "Tier_C_None"]
    match_score: int = Field(..., ge=1, le=100)
    logic_summary: str
    clean_apply_url: str


class JobEvaluationList(BaseModel):
    """
    Wrapper that allows Groq to return multiple evaluations in one JSON
    response, keeping the round-trip count low.
    """

    jobs: list[JobEvaluation]
