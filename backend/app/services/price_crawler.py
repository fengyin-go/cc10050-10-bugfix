"""价格爬取任务领域服务：按关键词与抓取深度调度商品价格页面的抓取，负责任务启停与重试。

数据链路位置：采集执行层，把站点目录、频控规则和代理线路组合成可运行的爬取任务，
产出的页面样本写入样本库供解析层消费。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PriceCrawlerService(BaseModuleService):
    MODULE = "price_crawler"
    ENTITY = "爬取任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["任务名称", "目标站点", "商品关键词", "抓取深度", "采集间隔"]
    REQUIRED_FIELDS = ["任务名称", "目标站点", "商品关键词"]
    STATUS_ORDER = ["草稿", "运行中", "已暂停", "采集失败"]
    ACTION_RULES = {"启动任务": "运行中", "暂停任务": "已暂停", "重试采集": "采集失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：爬取调度与样本产出 ----

    def start_task(self, entry_id: int) -> dict[str, Any]:
        """启动爬取任务，校验站点有效性与频控状态后进入运行中。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        domain = entry.get("目标站点", "")
        site = next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == domain), None
        )
        if site is None or site.get("status") != "已验证":
            return {"ok": False, "message": f"目标站点 {domain} 未验证或已封禁"}
        limit = next(
            (r for r in store.rows("rate_limit") if r.get("目标站点") == domain
             and r.get("status") == "已生效"), None
        )
        if limit is None:
            return {"ok": False, "message": "目标站点缺少生效频控规则，拒绝启动"}
        entry["status"] = "运行中"
        entry["pending"] = True
        return {
            "ok": True,
            "message": "任务已启动",
            "concurrency": limit.get("并发上限", "3"),
            "interval": entry.get("采集间隔", "60"),
        }

    def pause_task(self, entry_id: int, reason: str) -> dict[str, Any]:
        """暂停运行中的任务并记录原因。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "运行中":
            return {"ok": False, "message": "任务不存在或不在运行中"}
        entry["status"] = "已暂停"
        entry["pending"] = True
        return {"ok": True, "message": f"任务已暂停: {reason}"}

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重试采集失败的任务，隔离失败样本后重新进入运行。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "采集失败":
            return {"ok": False, "message": "任务不存在或非失败状态"}
        qrows = store.rows("failure_quarantine")
        qid = max((int(r.get("id", 0)) for r in qrows), default=0) + 1
        qrows.append({
            "id": qid,
            "样本指纹": f"task-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "失败阶段": "采集",
            "失败原因": f"任务 {entry_id} 重试前快照",
            "隔离时间": datetime.now(timezone.utc).isoformat(),
            "重试次数": "1",
            "status": "待处理",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "运行中"
        entry["pending"] = True
        return {"ok": True, "message": "失败样本已隔离，任务重新运行", "quarantine_id": qid}

    def produce_samples(self, entry_id: int, pages: list[str]) -> dict[str, Any]:
        """把抓取到的页面写入样本库，形成任务→样本的数据链路。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "运行中":
            return {"ok": False, "message": "任务不在运行中，不能产出样本"}
        rows = store.rows("sample_store")
        base_id = max((int(r.get("id", 0)) for r in rows), default=0)
        batch = f"task-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}"
        for offset, page in enumerate(pages, start=1):
            rows.append({
                "id": base_id + offset,
                "样本批次": batch,
                "站点名称": entry.get("目标站点", ""),
                "原始HTML摘要": page[:80],
                "抓取时间": datetime.now(timezone.utc).isoformat(),
                "样本指纹": f"fp-{batch}-{offset}",
                "status": "已保存",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": "样本已写入样本库", "batch": batch, "count": len(pages)}

    def bind_proxy_route(self, entry_id: int) -> dict[str, Any]:
        """为任务选择可用代理线路并记录绑定。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        region = ""
        site = next(
            (s for s in store.rows("shop_site")
             if s.get("站点域名") == entry.get("目标站点", "")), None
        )
        if site:
            region = str(site.get("采集区域", ""))
        candidates = [
            r for r in store.rows("proxy_route")
            if r.get("status") == "可用" and (not region or region in str(r.get("线路区域", "")))
        ]
        if not candidates:
            return {"ok": False, "message": "没有可用代理线路"}
        best = max(candidates, key=lambda r: float(r.get("可用性评分", "0") or 0))
        return {
            "ok": True,
            "message": "代理线路已绑定",
            "route_id": best.get("id"),
            "region": best.get("线路区域", ""),
        }

    def report_metrics(self, entry_id: int) -> dict[str, Any]:
        """向采集量指标上报本任务的抓取量（可追溯、幂等，站点不明时拒绝）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        domain = entry.get("目标站点", "")
        from app.services.collect_metrics import CollectMetricsService

        result = CollectMetricsService().apply_crawl_metric(
            site=domain,
            crawled=1,
            source_type="price_crawler",
            source_id=entry_id,
            task_id=entry_id,
        )
        result["site"] = domain
        return result
