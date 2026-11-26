"""采集调度计划领域服务：按周期调度价格爬取任务的执行，负责计划触发、状态回写与失败重排。

数据链路位置：调度编排层，把爬取任务按周期组织成执行计划，触发结果回写
上次状态，并把执行量同步到采集量指标。

归属治理约定：
- 计划触发时生成带稳定身份的「执行轮次」，记录原执行任务 ID 与目标站点，
  作为迟到回执归属核对的唯一可信来源；任务同名不作为归属依据；
- 上报执行量时关联任务缺失只能挂起原因，绝不写空站点指标、绝不猜同名任务；
- 指标入账统一走 CollectMetricsService 的可追溯台账，来源重复自动幂等。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlScheduleService(BaseModuleService):
    MODULE = "crawl_schedule"
    ENTITY = "调度计划"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["计划名称", "关联任务", "调度周期", "下次执行", "上次状态"]
    REQUIRED_FIELDS = ["计划名称", "关联任务", "调度周期"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"立即执行": "执行中", "暂停计划": "待执行"}
    NEGATIVE_ACTIONS = []

    # ---- 任务解析：同名任务必须结合站点核对 ----

    def _resolve_task(self, task_name: str, expected_site: str | None = None) -> dict[str, Any] | None:
        candidates = [t for t in store.rows("price_crawler") if t.get("任务名称") == task_name]
        if expected_site:
            matched = next((t for t in candidates if t.get("目标站点") == expected_site), None)
            if matched is not None:
                return matched
        return candidates[0] if len(candidates) == 1 else None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """把调度页动作接到带归属校验的领域方法，通用动作不再绕过同名核对。"""
        if action == "立即执行":
            result = self.execute_now(entry_id)
            return (store.find(self.MODULE, entry_id) if result.get("ok") else None), result["message"]
        if action == "暂停计划":
            result = self.pause_plan(entry_id)
            return (store.find(self.MODULE, entry_id) if result.get("ok") else None), result["message"]
        return super().run_action(entry_id, action)

    # ---- 业务扩展：周期调度与状态回写 ----

    def execute_now(self, entry_id: int) -> dict[str, Any]:
        """立即执行计划：联动关联爬取任务进入运行中，并生成可追溯的执行轮次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        task_name = entry.get("关联任务", "")
        task = self._resolve_task(task_name)
        if task is None:
            entry["status"] = "执行失败"
            entry["上次状态"] = "关联任务不存在或同名任务无法唯一定位"
            entry["abnormal"] = True
            return {"ok": False, "message": f"关联任务 {task_name} 不存在或存在同名冲突，拒绝猜测归属"}
        task["status"] = "运行中"
        task["pending"] = True
        entry["status"] = "执行中"
        entry["pending"] = True
        round_row = self.start_round(entry_id, task_id=int(task["id"]))
        return {
            "ok": True,
            "message": "计划已触发执行",
            "task": task_name,
            "round_id": round_row.get("id"),
        }

    def pause_plan(self, entry_id: int) -> dict[str, Any]:
        """暂停计划并同步暂停关联任务。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "计划不存在"}
        task_name = entry.get("关联任务", "")
        paused = 0
        for task in store.rows("price_crawler"):
            if task.get("任务名称") == task_name and task.get("status") == "运行中":
                task["status"] = "已暂停"
                task["pending"] = True
                paused += 1
        entry["status"] = "待执行"
        entry["pending"] = True
        return {"ok": True, "message": "计划已暂停", "paused_tasks": paused}

    def complete_round(self, entry_id: int, ok: bool) -> dict[str, Any]:
        """回写一轮执行结果并计算下次执行时间。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "计划不存在"}
        period = str(entry.get("调度周期", "60m") or "60m")
        try:
            minutes = int(period.rstrip("m")) if period.endswith("m") else int(period)
        except ValueError:
            minutes = 60
        now = datetime.now(timezone.utc)
        entry["status"] = "已完成" if ok else "执行失败"
        entry["上次状态"] = "成功" if ok else "失败"
        entry["abnormal"] = not ok
        entry["下次执行"] = datetime.fromtimestamp(
            now.timestamp() + minutes * 60, tz=timezone.utc
        ).isoformat()
        return {
            "ok": True,
            "message": "执行结果已回写",
            "next_run": entry.get("下次执行"),
        }

    def reschedule_failed(self, entry_id: int) -> dict[str, Any]:
        """失败计划重新排队并隔离失败上下文。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "执行失败":
            return {"ok": False, "message": "计划不存在或未失败"}
        rows = store.rows("failure_quarantine")
        fid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append({
            "id": fid,
            "样本指纹": f"schedule-{entry_id}",
            "失败阶段": "调度",
            "失败原因": f"计划 {entry.get('计划名称', '')} 执行失败",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "1",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "待执行"
        entry["abnormal"] = False
        entry["pending"] = True
        return {"ok": True, "message": "失败计划已重排", "quarantine_id": fid}

    # ---- 执行轮次：回执归属核对的可信来源 ----

    def start_round(self, entry_id: int, task_id: int, crawled: int = 0) -> dict[str, Any]:
        """为一次计划执行建立执行轮次，固化原执行任务 ID 与目标站点。"""
        entry = store.find(self.MODULE, entry_id)
        task = store.find("price_crawler", int(task_id))
        if entry is None or task is None:
            return {"ok": False, "message": "计划或任务不存在，无法建立执行轮次"}
        rounds = store.rows("execution_round")
        rid = max((int(r.get("id", 0)) for r in rounds), default=0) + 1
        site_domain = str(task.get("目标站点", ""))
        site_row = next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == site_domain), None
        )
        round_row = {
            "id": rid,
            "计划id": entry_id,
            "轮次编号": f"R{entry_id}-{rid}",
            "任务id": task_id,
            "任务名称": task.get("任务名称", ""),
            "来源站点": site_domain,
            "来源站点名称": site_row.get("站点名称", "") if site_row else site_domain,
            "抓取量": crawled,
            "执行时间": _now(),
            "status": "已执行",
            "pending": False,
            "abnormal": False,
        }
        rounds.append(round_row)
        return round_row

    def list_rounds(self, plan_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows("execution_round")
        if plan_id is not None:
            rows = [r for r in rows if r.get("计划id") == plan_id]
        return rows

    def get_round(self, round_id: int) -> dict[str, Any] | None:
        return store.find("execution_round", round_id)

    # ---- 执行量上报：只走可追溯台账，绝不产生空站点 ----

    def report_execution(self, entry_id: int, *, round_id: int | None = None) -> dict[str, Any]:
        """把计划执行量上报采集量指标。

        修复点：
        - 关联任务缺失/同名冲突时返回失败并说明原因，不再返回成功；
        - 站点无法确定时绝不创建空站点指标行；
        - 入账走 metrics_ledger，同一执行轮次重复上报只加一次。
        """
        from app.services.collect_metrics import CollectMetricsService

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "计划不存在"}

        round_row = self.get_round(int(round_id)) if round_id is not None else None
        if round_row is not None:
            task = store.find("price_crawler", int(round_row.get("任务id")))
            site = round_row.get("来源站点", "")
            source_id = int(round_row["id"])
        else:
            task_name = entry.get("关联任务", "")
            task = self._resolve_task(task_name)
            if task is None:
                return {
                    "ok": False,
                    "message": f"关联任务 {task_name} 不存在或同名冲突，无法确定站点，拒绝上报空站点指标",
                }
            site = str(task.get("目标站点", ""))
            source_id = entry_id

        if not site:
            return {"ok": False, "message": "执行来源缺少目标站点，拒绝上报空站点指标"}

        result = CollectMetricsService().apply_crawl_metric(
            site=site,
            crawled=1,
            source_type="execution_round" if round_row is not None else "crawl_schedule",
            source_id=source_id,
            round_id=round_row.get("id") if round_row else None,
            task_id=task.get("id") if task else None,
        )
        result["site"] = site
        return result

    def due_plans(self, now: str | None = None) -> dict[str, Any]:
        """列出到达下次执行时间的待执行计划。"""
        current = now or datetime.now(timezone.utc).isoformat()
        due = [
            p for p in store.rows(self.MODULE)
            if p.get("status") == "待执行"
            and str(p.get("下次执行", "") or "9999") <= current
        ]
        return {
            "ok": True,
            "message": "到期计划已筛选",
            "due_count": len(due),
            "plans": [p.get("计划名称", "") for p in due],
        }
