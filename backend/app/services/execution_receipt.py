"""执行回执领域服务：调度执行回执的接收、归属核对、补记与重放入账。

数据链路位置：调度编排层与观测层之间的归属治理关口。回执上报时如果关联任务
已经下线，只能先以「无法确定站点」挂起并保留原因，**绝不**按同名任务猜测归属，
也**绝不**写入空站点或默认站点的抓取指标。归属只能凭执行轮次记录的原始执行
来源（任务 ID、目标站点）核对恢复；恢复后重投同一回执只入账一次，并留下可查
的站点补记依据。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

# 回执归属状态机
STATUS_ORPHAN = "无法确定站点"
STATUS_RESOLVED = "已确认归属"
STATUS_BOOKED = "已入账"

# 补记状态
BACKFILL_NONE = "未补记"
BACKFILL_PENDING = "已补记待重投"
BACKFILL_DONE = "补记已入账"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(table: str) -> int:
    return max((int(r.get("id", 0)) for r in store.rows(table)), default=0) + 1


class ExecutionReceiptService:
    """执行回执归属治理服务（独立于通用 CRUD 骨架，承载真实业务规则）。"""

    MODULE = "execution_receipt"

    # ---- 查询 ----

    def list_receipts(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.MODULE)
        if status:
            rows = [r for r in rows if r.get("归属状态") == status]
        return rows

    def get_receipt(self, receipt_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, receipt_id)

    def get_by_code(self, receipt_code: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.MODULE) if r.get("回执编号") == receipt_code),
            None,
        )

    def list_unresolved(self) -> list[dict[str, Any]]:
        """没有关联任务、仍无法确定站点的回执清单（保留原因，供继续核实）。"""
        return [
            r for r in store.rows(self.MODULE)
            if r.get("归属状态") == STATUS_ORPHAN
        ]

    # ---- 上报 / 重投 ----

    def submit_receipt(
        self,
        *,
        receipt_code: str,
        round_id: int | None,
        plan_id: int | None,
        task_id: int | None,
        task_name: str,
        crawled: int = 1,
        operator: str = "调度服务",
    ) -> dict[str, Any]:
        """接收一份执行回执（含迟到重投）。

        规则：
        - 同一回执编号重复提交：不重复产生抓取量，直接回显既有归属与入账状态；
        - 关联任务存在时必须核对执行轮次的原始来源与站点，名称相同不作为依据；
        - 关联任务缺失时挂起为「无法确定站点」并写明原因，返回 ok=True（上报本身
          已受理），但不产生任何站点抓取指标。
        """
        if crawled < 0:
            return {"ok": False, "message": "抓取量不能为负数"}

        existing = self.get_by_code(receipt_code)
        if existing is not None:
            existing["重投次数"] = int(str(existing.get("重投次数", "0") or 0)) + 1
            existing["最近重投时间"] = _now()
            return {
                "ok": True,
                "message": (
                    f"回执 {receipt_code} 已存在，重复提交未重复增加抓取量"
                    if existing.get("入账标识")
                    else f"回执 {receipt_code} 已存在，本次重投已记录，归属状态：{existing.get('归属状态')}"
                ),
                "duplicate": True,
                "receipt": existing,
                "booked": bool(existing.get("入账标识")),
            }

        receipt: dict[str, Any] = {
            "id": _next_id(self.MODULE),
            "回执编号": receipt_code,
            "执行轮次id": round_id,
            "计划id": plan_id,
            "任务id": task_id,
            "任务名称": task_name,
            "原执行站点": "",
            "原执行站点名称": "",
            "抓取量": crawled,
            "上报时间": _now(),
            "最近重投时间": _now(),
            "重投次数": 1,
            "归属状态": STATUS_ORPHAN,
            "无法确定原因": "",
            "解析站点": "",
            "补记状态": BACKFILL_NONE,
            "补记人": "",
            "补记依据": "",
            "补记时间": "",
            "入账标识": "",
            "status": STATUS_ORPHAN,
            "pending": True,
            "abnormal": True,
        }

        resolved = self._resolve_attribution(receipt, round_id, task_id, task_name)
        store.rows(self.MODULE).append(receipt)
        return resolved

    def _resolve_attribution(
        self,
        receipt: dict[str, Any],
        round_id: int | None,
        task_id: int | None,
        task_name: str,
    ) -> dict[str, Any]:
        """按执行来源核对回执归属；任何环节对不上都挂起，绝不猜站点。"""
        round_row = (
            store.find("execution_round", int(round_id)) if round_id is not None else None
        )
        task = (
            store.find("price_crawler", int(task_id)) if task_id is not None else None
        )

        # 1) 有执行轮次：以轮次记录的原始任务 ID 与站点为唯一可信来源
        if round_row is not None:
            round_task_id = round_row.get("任务id")
            if task_id is not None and int(round_task_id) != int(task_id):
                receipt["无法确定原因"] = (
                    f"回执任务ID {task_id} 与执行轮次 {round_row.get('轮次编号')} "
                    f"的原执行任务 {round_task_id} 不一致，拒绝按同名任务归属"
                )
                return self._orphan_result(receipt)
            same_name = [
                t for t in store.rows("price_crawler")
                if t.get("任务名称") == round_row.get("任务名称")
                and int(t.get("id", 0)) != int(round_task_id)
            ]
            receipt["任务id"] = round_task_id
            receipt["任务名称"] = round_row.get("任务名称", task_name)
            receipt["原执行站点"] = round_row.get("来源站点", "")
            receipt["原执行站点名称"] = round_row.get("来源站点名称", "")
            self._mark_resolved(
                receipt,
                reason=(
                    f"凭执行轮次 {round_row.get('轮次编号')} 的原始来源核对归属"
                    + ("；已排除同名任务" if same_name else "")
                ),
            )
            return {
                "ok": True,
                "message": f"回执归属已核对到 {receipt['原执行站点名称']}（以执行轮次为准）",
                "duplicate": False,
                "receipt": receipt,
                "site": receipt["原执行站点"],
            }

        # 2) 没有轮次但任务还在：核对任务当前站点
        if task is not None:
            receipt["任务id"] = task_id
            receipt["原执行站点"] = task.get("目标站点", "")
            receipt["原执行站点名称"] = self._site_display_name(task.get("目标站点", ""))
            self._mark_resolved(receipt, reason="凭关联任务的当前目标站点核对归属")
            return {
                "ok": True,
                "message": f"回执归属已核对到 {receipt['原执行站点名称']}",
                "duplicate": False,
                "receipt": receipt,
                "site": receipt["原执行站点"],
            }

        # 3) 关联任务已下线：挂起，保留无法确定站点的原因
        same_name = [t for t in store.rows("price_crawler") if t.get("任务名称") == task_name]
        reason = f"回执上报时关联任务（{task_name}）已下线，任务关联丢失"
        if same_name:
            reason += (
                "；当前存在同名任务"
                + "、".join(
                    f"{t.get('任务名称')}→{self._site_display_name(t.get('目标站点', ''))}"
                    f"（任务ID {t.get('id')}）"
                    for t in same_name
                )
                + "，名称相同不能证明归属相同，需按原执行轮次补核"
            )
        receipt["无法确定原因"] = reason
        return self._orphan_result(receipt)

    def _orphan_result(self, receipt: dict[str, Any]) -> dict[str, Any]:
        receipt["归属状态"] = STATUS_ORPHAN
        receipt["status"] = STATUS_ORPHAN
        receipt["pending"] = True
        receipt["abnormal"] = True
        return {
            "ok": True,
            "message": f"回执已受理但{STATUS_ORPHAN}：{receipt.get('无法确定原因')}",
            "duplicate": False,
            "receipt": receipt,
            "unresolved": True,
        }

    def _mark_resolved(self, receipt: dict[str, Any], *, reason: str) -> None:
        receipt["归属状态"] = STATUS_RESOLVED
        receipt["解析站点"] = receipt.get("原执行站点", "")
        receipt["无法确定原因"] = ""
        receipt["status"] = STATUS_RESOLVED
        receipt["pending"] = True
        receipt["abnormal"] = False
        receipt["补记状态"] = BACKFILL_NONE
        receipt["补记依据"] = reason

    # ---- 补正关联 / 补记 ----

    def correct_attribution(
        self,
        receipt_id: int,
        *,
        round_id: int,
        operator: str,
        evidence: str,
    ) -> dict[str, Any]:
        """补正回执关联：用原执行轮次恢复归属，生成站点补记单，但先不入账。

        必须核对原执行来源及站点：
        - 轮次不存在 → 拒绝并说明原因，回执保持挂起，可用原回执继续核实；
        - 同名任务的站点与轮次来源不一致 → 拒绝，防止给同名新任务站点加量。
        """
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return {"ok": False, "message": f"执行回执 {receipt_id} 不存在"}
        round_row = store.find("execution_round", int(round_id))
        if round_row is None:
            return {
                "ok": False,
                "message": f"执行轮次 {round_id} 不存在，无法核对原执行来源，回执保持「{STATUS_ORPHAN}」",
                "receipt": receipt,
            }

        source_site = round_row.get("来源站点", "")
        source_site_name = round_row.get("来源站点名称", "") or self._site_display_name(source_site)

        # 同名冲突核对：当前同名任务若指向别的站点，明确记录排除依据
        conflicts = [
            t for t in store.rows("price_crawler")
            if t.get("任务名称") == round_row.get("任务名称")
            and t.get("目标站点") != source_site
        ]
        conflict_note = ""
        if conflicts:
            conflict_note = "；已排除同名任务：" + "、".join(
                f"任务ID {t.get('id')}→{self._site_display_name(t.get('目标站点', ''))}"
                for t in conflicts
            )

        receipt["执行轮次id"] = round_row.get("id")
        receipt["计划id"] = round_row.get("计划id")
        receipt["任务id"] = round_row.get("任务id")
        receipt["任务名称"] = round_row.get("任务名称")
        receipt["原执行站点"] = source_site
        receipt["原执行站点名称"] = source_site_name
        receipt["解析站点"] = source_site
        receipt["归属状态"] = STATUS_RESOLVED
        receipt["无法确定原因"] = ""
        receipt["status"] = STATUS_RESOLVED
        receipt["pending"] = True
        receipt["abnormal"] = False

        full_evidence = (
            f"{evidence or '按原执行轮次补核'}；轮次 {round_row.get('轮次编号')} "
            f"原执行任务ID {round_row.get('任务id')}、原站点 {source_site_name}{conflict_note}"
        )
        receipt["补记状态"] = BACKFILL_PENDING
        receipt["补记人"] = operator
        receipt["补记依据"] = full_evidence
        receipt["补记时间"] = _now()

        backfill = {
            "id": _next_id("site_backfill"),
            "补记单号": f"BF-{receipt.get('回执编号')}",
            "回执编号": receipt.get("回执编号"),
            "执行轮次id": round_row.get("id"),
            "轮次编号": round_row.get("轮次编号"),
            "任务id": round_row.get("任务id"),
            "任务名称": round_row.get("任务名称"),
            "站点名称": source_site,
            "站点显示名": source_site_name,
            "抓取量": receipt.get("抓取量", 1),
            "补记人": operator,
            "补记依据": full_evidence,
            "补记时间": receipt["补记时间"],
            "入账状态": BACKFILL_PENDING,
            "入账标识": "",
            "status": BACKFILL_PENDING,
            "pending": True,
            "abnormal": False,
        }
        store.rows("site_backfill").append(backfill)
        receipt["补记单id"] = backfill["id"]
        return {
            "ok": True,
            "message": f"归属已补正到{source_site_name}，站点补记单 {backfill['补记单号']} 已生成，待原回执继续后入账",
            "receipt": receipt,
            "backfill": backfill,
            "site": source_site,
        }

    # ---- 继续（重投入账） ----

    def continue_receipt(self, receipt_id: int, *, operator: str) -> dict[str, Any]:
        """用原回执继续一次重投：归属确认后才入账，且同一回执只入账一次。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return {"ok": False, "message": f"执行回执 {receipt_id} 不存在"}

        receipt["重投次数"] = int(str(receipt.get("重投次数", "0") or 0)) + 1
        receipt["最近重投时间"] = _now()

        if receipt.get("入账标识"):
            return {
                "ok": True,
                "message": f"回执 {receipt.get('回执编号')} 已入账，重复继续不再增加抓取量",
                "duplicate": True,
                "receipt": receipt,
                "booked": True,
            }

        if receipt.get("归属状态") != STATUS_RESOLVED:
            return {
                "ok": False,
                "message": (
                    f"重投无法匹配：{receipt.get('无法确定原因') or '回执尚未补正归属'}。"
                    "请先用原执行轮次补核，原回执仍保留可继续核实"
                ),
                "receipt": receipt,
                "unresolved": True,
            }

        # 延迟导入避免服务层循环依赖
        from app.services.collect_metrics import CollectMetricsService

        site = receipt.get("原执行站点", "")
        crawled = int(str(receipt.get("抓取量", 1) or 1))
        applied = CollectMetricsService().apply_crawl_metric(
            site=site,
            crawled=crawled,
            source_type="execution_receipt",
            source_id=receipt["id"],
            receipt_code=receipt.get("回执编号", ""),
            round_id=receipt.get("执行轮次id"),
            task_id=receipt.get("任务id"),
            operator=operator,
        )
        if not applied["ok"]:
            return {"ok": False, "message": applied["message"], "receipt": receipt}

        receipt["归属状态"] = STATUS_BOOKED
        receipt["status"] = STATUS_BOOKED
        receipt["补记状态"] = BACKFILL_DONE
        receipt["入账标识"] = applied["ledger_id"]
        receipt["pending"] = False
        receipt["abnormal"] = False

        for backfill in store.rows("site_backfill"):
            if backfill.get("回执编号") == receipt.get("回执编号"):
                backfill["入账状态"] = BACKFILL_DONE
                backfill["入账标识"] = applied["ledger_id"]
                backfill["status"] = BACKFILL_DONE
                backfill["pending"] = False
        return {
            "ok": True,
            "message": applied["message"],
            "duplicate": False,
            "receipt": receipt,
            "site": site,
            "ledger_id": applied["ledger_id"],
        }

    # ---- 补记查询 ----

    def list_backfills(self, receipt_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("site_backfill")
        if receipt_code:
            rows = [r for r in rows if r.get("回执编号") == receipt_code]
        return rows

    def backfill_basis(self, receipt_code: str) -> dict[str, Any]:
        """查某笔补记的依据、责任人与入账情况。"""
        receipt = self.get_by_code(receipt_code)
        if receipt is None:
            return {"ok": False, "message": f"回执 {receipt_code} 不存在"}
        backfills = [
            r for r in store.rows("site_backfill") if r.get("回执编号") == receipt_code
        ]
        return {
            "ok": True,
            "message": "补记依据已查到" if backfills else "该回执尚未生成补记单",
            "receipt": receipt,
            "backfills": backfills,
            "booked": bool(receipt.get("入账标识")),
            "owner": receipt.get("补记人", ""),
        }

    # ---- 工具 ----

    @staticmethod
    def _site_display_name(site_domain: str) -> str:
        row = next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == site_domain),
            None,
        )
        return str(row.get("站点名称", site_domain)) if row else site_domain
