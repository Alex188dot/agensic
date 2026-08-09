from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from agensic.server import deps
from agensic.server.schemas import Context, PredictResponse
from agensic.server.prediction import predict_payload, prediction_lines

router = APIRouter()


@router.post("/predict", response_model=PredictResponse, response_model_exclude_unset=True)
async def predict_completion(ctx: Context, request: Request) -> PredictResponse:
    deps.enter_request_or_503()
    try:
        return await predict_payload(ctx, request)
    finally:
        deps.release_request_slot()


@router.post("/predict-lines", response_class=PlainTextResponse)
async def predict_completion_lines(ctx: Context, request: Request) -> PlainTextResponse:
    deps.enter_request_or_503()
    try:
        payload = await predict_payload(ctx, request)
        return PlainTextResponse(prediction_lines(payload))
    finally:
        deps.release_request_slot()
