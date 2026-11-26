"""采集量指标治理接口：来源台账、来源追溯与站点趋势（拒绝空站点/默认站点）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.collect_metrics import CollectMetricsService

router = APIRouter(prefix="/api/metrics-governance", tags=["采集量指标治理"])


def _service() -> CollectMetricsService:
    return CollectMetricsService()


@router.get("/ledger")
def list_ledger(site: str | None = None) -> dict[str, Any]:
    """站点抓取指标台账：每条抓取量都可追溯到执行来源。"""
    rows = _service().list_ledger(site)
    return {"ok": True, "message": "来源台账已列出", "items": rows, "total": len(rows)}


@router.get("/ledger/{ledger_id}/trace")
def trace_source(ledger_id: int) -> dict[str, Any]:
    """按台账追溯回执、执行轮次与任务。"""
    return _service().trace_source(ledger_id)


@router.get("/trend")
def trend_report(site: str) -> dict[str, Any]:
    """站点趋势；无法确定站点时明确拒绝，不展示空站点/默认站点抓取。"""
    return _service().trend_report(site)


@router.get("/sites")
def list_sites() -> dict[str, Any]:
    """指标列表可打开的真实站点清单（站点目录 + 指标站点交集）。"""
    service = _service()
    items = []
    for row in service.list_entries()[0]:
        site_row = service.resolve_site(str(row.get("站点名称", "")))
        if site_row is None:
            continue
        items.append({
            "metric_id": row.get("id"),
            "site_domain": row.get("站点名称"),
            "site_name": site_row.get("站点名称"),
            "region": site_row.get("采集区域"),
            "status": row.get("status"),
            "crawled": row.get("抓取量"),
        })
    return {"ok": True, "message": "指标可追溯站点已列出", "items": items, "total": len(items)}
