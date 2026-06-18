"""
ai_filter.py
============
Gemini 2.0 Flash scoring and tier-based filtering.

Pipeline
--------
1. Assemble a single JSON prompt from all raw job dicts (batched to stay
   within token limits).
2. Send to ``gemini-2.0-flash`` with ``response_mime_type="application/json"``
   so the model returns a structured payload directly.
3. Parse and validate the response through ``JobEvaluationList`` (Pydantic).
4. Apply tier filter:
     - Keep ALL  ``Tier_A_Strong`` jobs.
     - Keep      ``Tier_B_Fuzzy``  jobs only if ``match_score >= 70``.
     - Discard   ``Tier_C_None``   jobs unconditionally.

Error handling
--------------
If the AI response is malformed or the Pydantic validation fails, a
``ValueError`` is raised with a descriptive message.  ``main.py`` catches
this and aborts the run with a log entry rather than sending a broken email.
"""

import json
import logging
from textwrap import dedent

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_SYSTEM_INSTRUCTION,
    MIN_TIER_B_SCORE,
    TIER_A,
    TIER_B,
)
from models import JobEvaluation, JobEvaluationList

logger = logging.getLogger(__name__)

# Maximum number of jobs per Gemini request (prevents token overflow)
_BATCH_SIZE: int = 20


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_prompt(batch: list[dict]) -> str:
    """
    Serialise a batch of raw job dicts into the prompt sent to Gemini.

    Each job is rendered as a numbered block containing its title and body
    text so the model has enough context to assign an accurate tier.
    """
    lines: list[str] = [
        "Below are job postings extracted from email alerts and web scrapes.",
        "Evaluate each one and return a JSON object with a top-level 'jobs' array.",
        "Each element must have exactly these fields:",
        "  job_title, company, match_tier, match_score, logic_summary, clean_apply_url",
        "",
        "match_tier must be one of: Tier_A_Strong | Tier_B_Fuzzy | Tier_C_None",
        "match_score must be an integer from 1 to 100.",
        "clean_apply_url must be the application URL with tracking params stripped.",
        "",
    ]

    for i, job in enumerate(batch, start=1):
        lines.append(f"--- Job #{i} ---")
        lines.append(f"Subject / Title : {job.get('title', 'Unknown')}")
        lines.append(f"Apply URL       : {job.get('clean_url') or job.get('apply_url', '')}")
        lines.append(f"Body text       :\n{job.get('text', '')[:2000]}")
        lines.append("")

    return "\n".join(lines)


def _call_gemini(client: genai.Client, prompt: str) -> list[JobEvaluation]:
    """
    Send *prompt* to the Gemini model and parse the structured JSON response
    into a validated list of ``JobEvaluation`` objects.

    Raises
    ------
    ValueError
        If the response cannot be parsed or fails Pydantic validation.
    """
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=GEMINI_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.2,   # low temperature → more deterministic tier assignments
        ),
    )

    raw_text: str = response.text or ""
    logger.debug("Raw Gemini response (truncated): %s", raw_text[:500])

    try:
        parsed_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Gemini returned non-JSON response: {exc}\n"
            f"Raw output (first 300 chars): {raw_text[:300]}"
        ) from exc

    try:
        evaluation_list = JobEvaluationList.model_validate(parsed_data)
    except Exception as exc:
        raise ValueError(
            f"Pydantic validation failed for Gemini response: {exc}"
        ) from exc

    return evaluation_list.jobs


def _apply_tier_filter(evaluations: list[JobEvaluation]) -> list[JobEvaluation]:
    """
    Return only jobs that pass the tier threshold:
    - All  Tier_A_Strong
    - Tier_B_Fuzzy with match_score >= MIN_TIER_B_SCORE
    - Tier_C_None is always discarded
    """
    kept: list[JobEvaluation] = []

    for job in evaluations:
        if job.match_tier == TIER_A:
            kept.append(job)
            logger.info(
                "  ✅ KEEP [%s | score=%d] %s @ %s",
                job.match_tier, job.match_score, job.job_title, job.company,
            )
        elif job.match_tier == TIER_B and job.match_score >= MIN_TIER_B_SCORE:
            kept.append(job)
            logger.info(
                "  ✅ KEEP [%s | score=%d] %s @ %s",
                job.match_tier, job.match_score, job.job_title, job.company,
            )
        else:
            logger.info(
                "  ❌ DROP [%s | score=%d] %s @ %s",
                job.match_tier, job.match_score, job.job_title, job.company,
            )

    return kept


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_jobs(raw_jobs: list[dict]) -> list[JobEvaluation]:
    """
    Score and filter *raw_jobs* using Gemini 2.0 Flash.

    Parameters
    ----------
    raw_jobs : list[dict]
        Jobs that have already been deduplicated (output of ``dedup.filter_new``).

    Returns
    -------
    list[JobEvaluation]
        Jobs that passed the tier filter, ready for email delivery.

    Raises
    ------
    ValueError
        If the AI response cannot be parsed or validated.
    """
    if not raw_jobs:
        logger.info("No jobs to evaluate.")
        return []

    client = genai.Client(api_key=GEMINI_API_KEY)
    all_kept: list[JobEvaluation] = []

    # Process in batches to avoid token-limit errors
    for batch_start in range(0, len(raw_jobs), _BATCH_SIZE):
        batch = raw_jobs[batch_start : batch_start + _BATCH_SIZE]
        batch_num = (batch_start // _BATCH_SIZE) + 1
        total_batches = -(-len(raw_jobs) // _BATCH_SIZE)  # ceiling div

        logger.info(
            "Sending batch %d/%d (%d jobs) to Gemini …",
            batch_num, total_batches, len(batch),
        )

        prompt = _build_prompt(batch)
        evaluations = _call_gemini(client, prompt)

        logger.info(
            "Batch %d/%d: received %d evaluation(s). Applying tier filter …",
            batch_num, total_batches, len(evaluations),
        )
        kept = _apply_tier_filter(evaluations)
        all_kept.extend(kept)

    logger.info(
        "AI filtering complete: %d/%d job(s) passed the tier threshold.",
        len(all_kept), len(raw_jobs),
    )
    return all_kept
