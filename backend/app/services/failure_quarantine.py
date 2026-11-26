"""失败隔离区领域服务：隔离解析与匹配失败的样本供延迟重试，负责重试次数与丢弃治理。

数据链路位置：失败治理层，承接解析模板、比价计算、调度计划与代理线路
写入的失败上下文，恢复结果回写采集量指标与相关模块。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FailureQuarantineService(BaseModuleService):
    MODULE = "failure_quarantine"
    ENTITY = "隔离记录"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["样本指纹", "失败阶段", "失败原因", "隔离时间", "重试次数"]
    REQUIRED_FIELDS = ["样本指纹", "失败阶段", "失败原因"]
    STATUS_ORDER = ["待处理", "重试中", "已丢弃", "已恢复"]
    ACTION_RULES = {"立即重试": "待处理", "丢弃样本": "已丢弃"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：失败重试与恢复治理 ----

    def retry_now(self, entry_id: int) -> dict[str, Any]:
        """立即重试隔离样本：按失败阶段回放到对应模块。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        stage = entry.get("失败阶段", "")
        if stage not in ("采集", "解析", "计算", "匹配", "调度", "出口"):
            return {"ok": False, "message": f"未知失败阶段: {stage}"}
        retries = int(str(entry.get("重试次数", "0") or 0)) + 1
        if retries > 5:
            entry["status"] = "已丢弃"
            entry["pending"] = False
            return {"ok": False, "message": "重试次数超限，样本已丢弃"}
        entry["重试次数"] = str(retries)
        entry["status"] = "重试中"
        entry["pending"] = True
        return {
            "ok": True,
            "message": f"样本已按 {stage} 阶段重试",
            "retries": retries,
        }

    def mark_restored(self, entry_id: int) -> dict[str, Any]:
        """标记样本已恢复并把恢复量回写采集量指标。

        归属治理：只有隔离记录携带可解析的真实站点时才回写指标，绝不在站点不明时
        随便挑一条指标行（默认站点）加量。
        """
        from app.services.collect_metrics import CollectMetricsService

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "隔离记录不存在"}
        entry["status"] = "已恢复"
        entry["pending"] = False
        entry["abnormal"] = False
        site = str(entry.get("站点名称", "") or "")
        metrics_service = CollectMetricsService()
        site_row = metrics_service.resolve_site(site)
        if site_row is None:
            return {
                "ok": True,
                "message": "样本已恢复；隔离记录未携带可确定站点，未回写任何站点指标（不使用默认站点）",
                "fingerprint": entry.get("样本指纹", ""),
                "metrics_updated": False,
            }
        row = next(
            (m for m in store.rows("collect_metrics") if m.get("站点名称") == site),
            None,
        )
        if row is not None:
            row["产出量"] = str(int(row.get("产出量", "0") or 0) + 1)
        return {
            "ok": True,
            "message": "样本已恢复",
            "fingerprint": entry.get("样本指纹", ""),
            "metrics_updated": row is not None,
            "site": site,
        }

    def discard(self, entry_id: int, reason: str) -> dict[str, Any]:
        """丢弃无法恢复的样本并记录最终原因。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "隔离记录不存在"}
        if entry.get("status") == "已恢复":
            return {"ok": False, "message": "已恢复样本不能丢弃"}
        entry["status"] = "已丢弃"
        entry["失败原因"] = f"{entry.get('失败原因', '')};丢弃:{reason}"
        entry["pending"] = False
        return {"ok": True, "message": f"样本已丢弃: {reason}"}

    def retryable_by_stage(self, stage: str) -> dict[str, Any]:
        """筛选某失败阶段可重试的样本清单。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("失败阶段") == stage and r.get("status") == "待处理"
            and int(str(r.get("重试次数", "0") or 0)) <= 3
        ]
        return {
            "ok": True,
            "message": "可重试样本已筛选",
            "stage": stage,
            "retryable": len(rows),
            "fingerprints": [r.get("样本指纹", "") for r in rows][:10],
        }

    def escalate_to_review(self, entry_id: int) -> dict[str, Any]:
        """多次重试仍失败的样本升级人工复核。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "隔离记录不存在"}
        retries = int(str(entry.get("重试次数", "0") or 0))
        if retries < 3:
            return {"ok": False, "message": "重试次数不足，暂不升级人工复核"}
        rows = store.rows("match_review")
        rid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": rid,
            "记录编号": f"FQ-{entry_id}-{rid}",
            "匹配记录": entry.get("样本指纹", ""),
            "复核结论": f"重试 {retries} 次仍未恢复",
            "复核角色": "数据治理专员",
            "复核时间": datetime.now(timezone.utc).isoformat(),
            "status": "待复核",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "已升级人工复核", "review_id": rid}

    def quarantine_stats(self) -> dict[str, Any]:
        """输出隔离区统计快照供采集量指标消费。"""
        rows = store.rows(self.MODULE)
        by_stage: dict[str, int] = {}
        for r in rows:
            stage = str(r.get("失败阶段", "未知"))
            by_stage[stage] = by_stage.get(stage, 0) + 1
        return {
            "ok": True,
            "message": "隔离区统计完成",
            "total": len(rows),
            "by_stage": by_stage,
            "restored": sum(1 for r in rows if r.get("status") == "已恢复"),
        }
