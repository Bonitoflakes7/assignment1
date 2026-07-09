"""
FastAPI service for DOL Prevailing Wage Level determination.

Design goals (why the code is structured this way):
  1. NO hallucination risk: the actual level determination is pure arithmetic
     in scoring.py. There is no LLM in this request path at all.
  2. Bad input is rejected loudly, not smoothed over: Pydantic validation in
     models.py + a global exception handler here mean malformed or
     contradictory input returns a clear 4xx error with details, never a
     silently-wrong 200.
  3. Every response is auditable: the API returns a per-factor point
     breakdown with reasoning, not just a bare "Level III" answer, so a
     human can check the math.
  4. Unexpected server errors return a generic 500 (no stack traces leaked
     to the client) while the real error is logged server-side.
"""

import logging
import time
import uuid
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from models import WageLevelRequest, WageLevelResponse, ErrorResponse
from scoring import determine_wage_level

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("prevailing_wage_service")

app = FastAPI(
    title="DOL Prevailing Wage Level Estimator",
    description=(
        "Deterministic, rule-based estimator for the DOL's four-tier "
        "prevailing wage level (I-IV), based on the DOL Prevailing Wage "
        "Determination Policy Guidance. Not an official determination - "
        "see the `disclaimer` field on every response."
    ),
    version="1.0.0",
)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Attach a request ID + timing to every request for traceability."""
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        # Safety net: if something below forgets to catch an error,
        # we still log it with context instead of crashing silently.
        logger.exception(f"[{request_id}] Unhandled exception during request")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="internal_server_error",
                detail="An unexpected error occurred. This has been logged.",
            ).model_dump(),
        )
    duration_ms = round((time.time() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"-> {response.status_code} ({duration_ms}ms)"
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Turns Pydantic/FastAPI's default (verbose, nested) validation errors into
    a clean, field-by-field error map. This is the primary defense against
    bad/incomplete data reaching the scoring engine.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    field_errors = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p != "body")
        field_errors[loc or "request"] = err["msg"]

    logger.warning(f"[{request_id}] Validation failed: {field_errors}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="validation_error",
            detail="One or more fields failed validation. See 'fields' for details.",
            fields=field_errors,
        ).model_dump(),
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    """Catches model_validator-raised errors (cross-field logic checks)."""
    request_id = getattr(request.state, "request_id", "unknown")
    field_errors = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "request"
        field_errors[loc] = err["msg"]

    logger.warning(f"[{request_id}] Model validation failed: {field_errors}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="validation_error",
            detail="Input failed logical consistency checks.",
            fields=field_errors,
        ).model_dump(),
    )


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": "prevailing-wage-level-estimator"}


@app.post(
    "/v1/wage-level",
    response_model=WageLevelResponse,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["wage-level"],
    summary="Estimate DOL prevailing wage level (I-IV) from job requirements",
)
async def wage_level(req: WageLevelRequest, request: Request):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        f"[{request_id}] Scoring request for occupation='{req.occupation_title}' "
        f"soc_code={req.soc_code}"
    )
    result = determine_wage_level(req)
    logger.info(
        f"[{request_id}] Result: {result.wage_level} "
        f"({result.total_points} total points)"
    )
    return result


@app.get("/", tags=["meta"])
async def root():
    return {
        "service": "DOL Prevailing Wage Level Estimator",
        "docs": "/docs",
        "health": "/health",
        "estimate_endpoint": "POST /v1/wage-level",
    }
