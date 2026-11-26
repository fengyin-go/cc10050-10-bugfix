"""采集量指标领域服务：统计各站点采集-解析-产出的量级健康度，负责波动检测与断采告警。

数据链路位置：观测层，汇聚爬取任务、调度计划、样本库与隔离区上报的量级数据，
产出的健康度结论供采集治理决策与告警联动。

归属治理约定：
- 站点抓取指标只消费**可追溯到执行来源**（执行轮次/回执/任务）的上报，
  每次入账在 metrics_ledger 留痕，来源重复时幂等，不重复加量；
- 站点名为空或在站点目录中无法确定的上报一律拒绝，不产生空站点/默认站点记录；
- 趋势报告只输出真实站点的数据，无法确定站点的回执不会出现在任何站点趋势里。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CollectMetricsService(BaseModuleService):
    MODULE = "collect_metrics"
    ENTITY = "指标记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["统计时间", "站点名称", "抓取量", "解析量", "产出量"]
    REQUIRED_FIELDS = ["统计时间", "站点名称", "抓取量"]
    STATUS_ORDER = ["正常", "量级波动", "断采"]
    ACTION_RULES = {"查看趋势": "量级波动", "忽略波动": "断采"}
    NEGATIVE_ACTIONS = ["忽略波动"]

    # ---- 站点解析与入账（归属治理核心） ----

    def resolve_site(self, site_domain: str) -> dict[str, Any] | None:
        """按站点域名解析真实站点；解析不到返回 None，调用方必须拒绝而不是猜默认站点。"""
        if not str(site_domain or "").strip():
            return None
        return next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == site_domain),
            None,
        )

    def _find_metric_row(self, site_domain: str) -> dict[str, Any] | None:
        return next(
            (r for r in store.rows(self.MODULE) if r.get("站点名称") == site_domain),
            None,
        )

    def apply_crawl_metric(
        self,
        *,
        site: str,
        crawled: int,
        source_type: str,
        source_id: int,
        receipt_code: str = "",
        round_id: int | None = None,
        task_id: int | None = None,
        operator: str = "",
    ) -> dict[str, Any]:
        """消费一笔可追溯到执行来源的站点抓取指标。

        - site 为空或站点目录不存在：拒绝，绝不写空站点/默认站点；
        - 同一来源（source_type + source_id）重复上报：幂等返回既有台账，不加量；
        - 入账成功后在 metrics_ledger 留痕，并同步刷新站点趋势行。
        """
        if crawled < 0:
            return {"ok": False, "message": "抓取量不能为负数"}
        site_row = self.resolve_site(site)
        if site_row is None:
            return {
                "ok": False,
                "message": f"站点 {site or '（空）'} 无法在站点目录确定，拒绝写入抓取指标（不产生空站点）",
            }

        ledger = store.rows("metrics_ledger")
        fingerprint = f"{source_type}:{source_id}"
        existed = next((r for r in ledger if r.get("来源标识") == fingerprint), None)
        if existed is not None:
            return {
                "ok": True,
                "message": f"来源 {fingerprint} 已入账，重复上报不重复增加抓取量",
                "duplicate": True,
                "ledger_id": existed["id"],
                "site": site,
            }

        row = self._find_metric_row(site)
        now = _now()
        if row is None:
            row = {
                "id": max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1,
                "统计时间": now,
                "站点名称": site,
                "站点显示名": site_row.get("站点名称", ""),
                "抓取量": str(crawled),
                "解析量": "0",
                "产出量": "0",
                "status": "正常",
                "pending": True,
                "abnormal": False,
            }
            store.rows(self.MODULE).append(row)
        else:
            row["抓取量"] = str(_safe_int(row.get("抓取量")) + crawled)
            row["统计时间"] = now
            row["站点显示名"] = site_row.get("站点名称", row.get("站点显示名", ""))

        ledger_id = max((int(r.get("id", 0)) for r in ledger), default=0) + 1
        ledger.append({
            "id": ledger_id,
            "入账时间": now,
            "站点名称": site,
            "站点显示名": site_row.get("站点名称", ""),
            "抓取增量": crawled,
            "来源类型": source_type,
            "来源id": source_id,
            "来源标识": fingerprint,
            "回执编号": receipt_code,
            "执行轮次id": round_id,
            "任务id": task_id,
            "操作人": operator,
        })
        return {
            "ok": True,
            "message": f"{site_row.get('站点名称', site)} 抓取量已增加 {crawled}（来源 {fingerprint}）",
            "duplicate": False,
            "ledger_id": ledger_id,
            "site": site,
            "crawled_total": _safe_int(row.get("抓取量")),
        }

    def list_ledger(self, site: str | None = None) -> list[dict[str, Any]]:
        """站点抓取指标的来源台账：每条抓取量都可追溯到执行来源。"""
        rows = store.rows("metrics_ledger")
        if site:
            rows = [r for r in rows if r.get("站点名称") == site]
        return rows

    def trace_source(self, ledger_id: int) -> dict[str, Any]:
        """按台账记录追溯执行来源（回执/轮次/任务）。"""
        entry = store.find("metrics_ledger", ledger_id)
        if entry is None:
            return {"ok": False, "message": f"台账 {ledger_id} 不存在"}
        trace: dict[str, Any] = {
            "ok": True,
            "message": "来源已追溯",
            "ledger": entry,
            "receipt": None,
            "round": None,
            "task": None,
        }
        if entry.get("来源类型") == "execution_receipt":
            trace["receipt"] = store.find("execution_receipt", int(entry.get("来源id")))
        if entry.get("执行轮次id") is not None:
            trace["round"] = store.find("execution_round", int(entry.get("执行轮次id")))
        if entry.get("任务id") is not None:
            trace["task"] = store.find("price_crawler", int(entry.get("任务id")))
        return trace

    # ---- 业务扩展：量级统计与健康度判定 ----

    def record_stats(self, site: str, crawled: int, parsed: int, output: int) -> dict[str, Any]:
        """记录一次站点量级统计；站点无法确定时拒绝，不产生空站点记录。"""
        if crawled < 0 or parsed < 0 or output < 0:
            return {"ok": False, "message": "量级不能为负数"}
        site_row = self.resolve_site(site)
        if site_row is None:
            return {
                "ok": False,
                "message": f"站点 {site or '（空）'} 无法确定，拒绝记录空站点指标",
            }
        rows = store.rows(self.MODULE)
        row = self._find_metric_row(site)
        now = _now()
        if row is None:
            row = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "统计时间": now,
                "站点名称": site,
                "站点显示名": site_row.get("站点名称", ""),
                "抓取量": str(crawled),
                "解析量": str(parsed),
                "产出量": str(output),
                "status": "正常",
                "pending": True,
                "abnormal": False,
            }
            rows.append(row)
        else:
            row["抓取量"] = str(_safe_int(row.get("抓取量")) + crawled)
            row["解析量"] = str(_safe_int(row.get("解析量")) + parsed)
            row["产出量"] = str(_safe_int(row.get("产出量")) + output)
            row["统计时间"] = now
        return {"ok": True, "message": "量级已记录", "site": site}

    def detect_anomaly(self, entry_id: int) -> dict[str, Any]:
        """检测站点量级异常：断采与波动判定。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        crawled = _safe_int(entry.get("抓取量"))
        parsed = _safe_int(entry.get("解析量"))
        if crawled == 0:
            entry["status"] = "断采"
            entry["abnormal"] = True
            return {"ok": True, "message": "检测到断采", "anomaly": "断采"}
        if parsed < crawled * 0.5:
            entry["status"] = "量级波动"
            entry["pending"] = True
            return {"ok": True, "message": "解析量占比过低，量级波动", "anomaly": "量级波动"}
        entry["status"] = "正常"
        entry["abnormal"] = False
        entry["pending"] = False
        return {"ok": True, "message": "量级健康", "anomaly": ""}

    def cross_check_site(self, entry_id: int) -> dict[str, Any]:
        """与站点目录交叉核对指标站点是否仍有效。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "指标记录不存在"}
        site = entry.get("站点名称", "")
        site_row = self.resolve_site(site)
        if site_row is None:
            return {"ok": False, "message": f"站点 {site or '（空）'} 不在站点目录，指标归属无法确认"}
        if site_row.get("status") == "已封禁":
            entry["status"] = "断采"
            entry["abnormal"] = True
            return {"ok": True, "message": "站点已封禁，指标标记断采"}
        return {"ok": True, "message": "站点有效", "site_status": site_row.get("status")}

    def aggregate_health(self) -> dict[str, Any]:
        """汇总全部站点的量级健康度（跳过无法确定站点的脏行）。"""
        rows = [r for r in store.rows(self.MODULE) if self.resolve_site(r.get("站点名称", ""))]
        normal = sum(1 for r in rows if r.get("status") == "正常")
        broken = sum(1 for r in rows if r.get("status") == "断采")
        total_crawled = sum(_safe_int(r.get("抓取量")) for r in rows)
        total_output = sum(_safe_int(r.get("产出量")) for r in rows)
        return {
            "ok": True,
            "message": "健康度汇总完成",
            "sites": len(rows),
            "normal": normal,
            "broken": broken,
            "total_crawled": total_crawled,
            "total_output": total_output,
            "yield_rate": f"{(total_output / total_crawled):.0%}" if total_crawled else "0%",
        }

    def notify_quarantine_pressure(self, entry_id: int) -> dict[str, Any]:
        """隔离区压力大时把压力传导到指标异常标记。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "指标记录不存在"}
        pending_quarantine = sum(
            1 for q in store.rows("failure_quarantine")
            if q.get("status") in ("待处理", "重试中")
        )
        output = _safe_int(entry.get("产出量"))
        if pending_quarantine > max(output * 0.2, 5):
            entry["status"] = "量级波动"
            entry["abnormal"] = True
            return {
                "ok": True,
                "message": "隔离区压力已传导",
                "pending_quarantine": pending_quarantine,
            }
        return {"ok": True, "message": "隔离区压力正常", "pending_quarantine": pending_quarantine}

    def trend_report(self, site: str) -> dict[str, Any]:
        """输出站点量级趋势报告；空站点/默认站点/无法确定的站点一律不输出抓取数据。"""
        site_row = self.resolve_site(site)
        if site_row is None:
            return {
                "ok": False,
                "message": f"站点 {site or '（空）'} 无法确定，趋势报告不展示空站点或默认站点抓取",
            }
        rows = [r for r in store.rows(self.MODULE) if r.get("站点名称") == site]
        if not rows:
            return {"ok": False, "message": f"站点 {site_row.get('站点名称', site)} 没有指标记录"}
        latest = rows[-1]
        ledger_rows = [r for r in self.list_ledger(site)]
        return {
            "ok": True,
            "message": "趋势报告已生成",
            "site": site,
            "site_name": site_row.get("站点名称", ""),
            "crawled": str(_safe_int(latest.get("抓取量"))),
            "parsed": str(_safe_int(latest.get("解析量"))),
            "output": str(_safe_int(latest.get("产出量"))),
            "status": latest.get("status", ""),
            "traceable_sources": len(ledger_rows),
        }
