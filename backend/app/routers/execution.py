"""执行回执归属治理接口：上报/重投、补正关联、继续入账、执行轮次与场景复位。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.crawl_schedule import CrawlScheduleService
from app.services.execution_receipt import ExecutionReceiptService
from app.store import store

router = APIRouter(prefix="/api/execution", tags=["执行回执归属治理"])


class SubmitReceiptPayload(BaseModel):
    receipt_code: str
    round_id: int | None = None
    plan_id: int | None = None
    task_id: int | None = None
    task_name: str = ""
    crawled: int = 1
    operator: str = "调度服务"


class CorrectPayload(BaseModel):
    round_id: int
    operator: str = "值班员"
    evidence: str = ""


class ContinuePayload(BaseModel):
    operator: str = "值班员"


class StartRoundPayload(BaseModel):
    plan_id: int
    task_id: int
    crawled: int = 0


def _service() -> ExecutionReceiptService:
    return ExecutionReceiptService()


@router.post("/receipts/submit")
def submit_receipt(payload: SubmitReceiptPayload) -> dict[str, Any]:
    """上报执行回执（迟到/重投同一回执编号自动幂等）。"""
    return _service().submit_receipt(
        receipt_code=payload.receipt_code,
        round_id=payload.round_id,
        plan_id=payload.plan_id,
        task_id=payload.task_id,
        task_name=payload.task_name,
        crawled=payload.crawled,
        operator=payload.operator,
    )


@router.get("/receipts")
def list_receipts(status: str | None = None) -> dict[str, Any]:
    rows = _service().list_receipts(status)
    return {"ok": True, "message": "执行回执已列出", "items": rows, "total": len(rows)}


@router.get("/receipts/unresolved")
def list_unresolved() -> dict[str, Any]:
    rows = _service().list_unresolved()
    return {"ok": True, "message": "无法确定站点的回执已列出", "items": rows, "total": len(rows)}


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: int) -> dict[str, Any]:
    row = _service().get_receipt(receipt_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"执行回执 {receipt_id} 不存在")
    return row


@router.post("/receipts/{receipt_id}/correct")
def correct_attribution(receipt_id: int, payload: CorrectPayload) -> dict[str, Any]:
    """补正关联：按原执行轮次恢复归属并生成站点补记单（先不入账）。"""
    return _service().correct_attribution(
        receipt_id,
        round_id=payload.round_id,
        operator=payload.operator,
        evidence=payload.evidence,
    )


@router.post("/receipts/{receipt_id}/continue")
def continue_receipt(receipt_id: int, payload: ContinuePayload) -> dict[str, Any]:
    """用原回执继续一次重投：归属确认后入账，重复继续不重复加量。"""
    return _service().continue_receipt(receipt_id, operator=payload.operator)


@router.get("/receipts/{receipt_code}/backfill-basis")
def backfill_basis(receipt_code: str) -> dict[str, Any]:
    """查这次补记的依据、责任人与入账情况。"""
    return _service().backfill_basis(receipt_code)


@router.get("/backfills")
def list_backfills(receipt_code: str | None = None) -> dict[str, Any]:
    rows = _service().list_backfills(receipt_code)
    return {"ok": True, "message": "站点补记单已列出", "items": rows, "total": len(rows)}


# ---- 执行轮次 ----

@router.get("/rounds")
def list_rounds(plan_id: int | None = None) -> dict[str, Any]:
    rows = CrawlScheduleService().list_rounds(plan_id)
    return {"ok": True, "message": "执行轮次已列出", "items": rows, "total": len(rows)}


@router.get("/rounds/{round_id}")
def get_round(round_id: int) -> dict[str, Any]:
    row = CrawlScheduleService().get_round(round_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"执行轮次 {round_id} 不存在")
    return row


@router.post("/rounds")
def start_round(payload: StartRoundPayload) -> dict[str, Any]:
    result = CrawlScheduleService().start_round(
        payload.plan_id, payload.task_id, payload.crawled
    )
    if isinstance(result, dict) and result.get("ok") is False:
        raise HTTPException(status_code=400, detail=result.get("message", "建立执行轮次失败"))
    return {"ok": True, "message": "执行轮次已建立", "round": result}


# ---- 场景复位（复现/回归） ----

@router.post("/scenario/reset")
def reset_scenario() -> dict[str, Any]:
    restored = store.reset_scenario()
    return {
        "ok": True,
        "message": "回执归属治理场景已复位（计划39/北站5/同名南站/迟到回执 RC-39-01）",
        "tables": {name: len(rows) for name, rows in restored.items()},
    }
