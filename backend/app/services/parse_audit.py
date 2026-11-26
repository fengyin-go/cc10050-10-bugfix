"""解析质量审计领域服务：审计解析模板的字段命中率与失败分布，负责命中率劣化告警与沉淀。

数据链路位置：质量审计层，消费页面解析模板上报的命中数据与失败隔离区的
失败原因分布，产出的审计结论供模板版本升级决策。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ParseAuditService(BaseModuleService):
    MODULE = "parse_audit"
    ENTITY = "审计明细"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["审计时间", "解析模板", "样本总数", "字段命中率", "失败原因分布"]
    REQUIRED_FIELDS = ["审计时间", "解析模板", "样本总数"]
    STATUS_ORDER = ["正常", "命中率下降", "解析异常"]
    ACTION_RULES = {"查看详情": "命中率下降", "忽略告警": "解析异常"}
    NEGATIVE_ACTIONS = ["忽略告警"]

    # ---- 业务扩展：命中率审计与劣化告警 ----

    def record_hit_rate(self, template: str, total: int, hit: int) -> dict[str, Any]:
        """记录一次模板命中率审计明细。"""
        if total <= 0:
            return {"ok": False, "message": "样本总数必须为正数"}
        rate = hit / total
        rows = store.rows(self.MODULE)
        aid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        status = "正常" if rate >= 0.9 else ("命中率下降" if rate >= 0.6 else "解析异常")
        rows.append({
            "id": aid,
            "审计时间": datetime.now(timezone.utc).isoformat(),
            "解析模板": template,
            "样本总数": str(total),
            "字段命中率": f"{rate:.2%}",
            "失败原因分布": "" if rate >= 0.9 else "选择器失配/字段缺失",
            "status": status,
            "pending": status != "正常",
            "abnormal": status == "解析异常",
        })
        return {"ok": True, "message": "审计明细已记录", "audit_id": aid, "rate": f"{rate:.2%}"}

    def detect_degradation(self, entry_id: int) -> dict[str, Any]:
        """对比历史审计检测模板命中率劣化趋势。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        template = entry.get("解析模板", "")
        history = [
            r for r in store.rows(self.MODULE)
            if r.get("解析模板") == template and r.get("id") != entry.get("id")
        ]
        current = float(str(entry.get("字段命中率", "0%")).rstrip("%") or 0)
        worst = max(
            (float(str(r.get("字段命中率", "0%")).rstrip("%") or 0) for r in history),
            default=current,
        )
        degraded = worst - current >= 0.15
        if degraded:
            entry["status"] = "命中率下降"
            entry["pending"] = True
        return {
            "ok": True,
            "message": "劣化检测完成",
            "degraded": degraded,
            "current": f"{current:.2%}",
            "best_before": f"{worst:.2%}",
        }

    def summarize_failures(self, entry_id: int) -> dict[str, Any]:
        """汇总失败隔离区中该模板的失败原因分布。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        reasons: dict[str, int] = {}
        for q in store.rows("failure_quarantine"):
            if q.get("失败阶段") == "解析":
                reason = str(q.get("失败原因", "") or "未知")
                reasons[reason] = reasons.get(reason, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:3]
        entry["失败原因分布"] = ";".join(f"{k}:{v}" for k, v in top)
        return {"ok": True, "message": "失败分布已汇总", "distribution": top}

    def recommend_template_upgrade(self, entry_id: int) -> dict[str, Any]:
        """命中率持续下降时建议模板升级版本。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        if entry.get("status") == "正常":
            return {"ok": True, "message": "模板健康，无需升级", "upgrade": False}
        template = next(
            (t for t in store.rows("page_parser")
             if t.get("模板名称") == entry.get("解析模板", "")), None
        )
        if template is None:
            return {"ok": False, "message": "关联模板不存在"}
        return {
            "ok": True,
            "message": "建议升级模板解析版本",
            "upgrade": True,
            "template": template.get("模板名称", ""),
            "current_version": template.get("解析版本", ""),
        }

    def publish_metrics(self, entry_id: int) -> dict[str, Any]:
        """把审计结论发布到采集量指标作为解析量口径。

        归属治理：解析模板名不是站点，只有当它能在站点目录解析为真实站点时才允许
        发布，否则拒绝，避免产生无法追溯到站点的指标行。
        """
        from app.services.collect_metrics import CollectMetricsService

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        site_domain = str(entry.get("解析模板", "") or "")
        metrics_service = CollectMetricsService()
        if metrics_service.resolve_site(site_domain) is None:
            return {
                "ok": False,
                "message": f"解析模板 {site_domain or '（空）'} 无法对应真实站点，拒绝发布站点指标（不产生空站点）",
            }
        metrics = store.rows("collect_metrics")
        row = next((m for m in metrics if m.get("站点名称") == site_domain), None)
        if row is None:
            mid = max((int(m.get("id", 0)) for m in metrics), default=0) + 1
            metrics.append({
                "id": mid,
                "统计时间": datetime.now(timezone.utc).isoformat(),
                "站点名称": site_domain,
                "站点显示名": metrics_service.resolve_site(site_domain).get("站点名称", ""),
                "抓取量": "0",
                "解析量": entry.get("样本总数", "0"),
                "产出量": "0",
                "status": entry.get("status", "正常"),
                "pending": True,
                "abnormal": entry.get("status") == "解析异常",
            })
        else:
            row["解析量"] = entry.get("样本总数", "0")
            row["统计时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "审计结论已发布指标"}

    def audit_health(self, days: int = 1) -> dict[str, Any]:
        """输出审计批次健康度统计。"""
        rows = store.rows(self.MODULE)
        abnormal = sum(1 for r in rows if r.get("status") == "解析异常")
        degraded = sum(1 for r in rows if r.get("status") == "命中率下降")
        return {
            "ok": True,
            "message": "审计健康度统计完成",
            "total": len(rows),
            "abnormal": abnormal,
            "degraded": degraded,
        }
