"""执行回执归属治理端到端场景回归（直接调用领域服务与路由函数，无需 HTTP 客户端）。

复现链路：计划 39 的回执 RC-39-01、北站抓取量 5、同名南站新任务、
迟到的失去关联回执。覆盖需求中的全部硬性约束。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from app.routers.execution import reset_scenario  # noqa: E402
from app.services.collect_metrics import CollectMetricsService  # noqa: E402
from app.services.crawl_schedule import CrawlScheduleService  # noqa: E402
from app.services.execution_receipt import ExecutionReceiptService  # noqa: E402
from app.store import store  # noqa: E402

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} - {name}" + (f" :: {detail}" if detail and not cond else ""))


def get_metric(site: str) -> dict:
    return next(m for m in store.rows("collect_metrics") if m["站点名称"] == site)


def receipt_row() -> dict:
    return store.rows("execution_receipt")[0]


receipt_svc = ExecutionReceiptService()
metrics_svc = CollectMetricsService()
schedule_svc = CrawlScheduleService()

# 0. 复位场景
r = reset_scenario()
check("场景复位成功", r["ok"] is True)
check("初始北站抓取量为5", get_metric("north.example.com")["抓取量"] == "5")
check("初始南站抓取量为2", get_metric("south.example.com")["抓取量"] == "2")
check("初始无来源台账", store.rows("metrics_ledger") == [])

# 1. 失去关联的回执重复上报：返回受理成功，但无法确定站点、保留原因、不产生空站点
r = receipt_svc.submit_receipt(
    receipt_code="RC-39-01", round_id=None, plan_id=39, task_id=None,
    task_name="同名采集-北区行情",
)
check("重复提交返回成功", r["ok"] is True)
check("重复提交被识别为duplicate", r.get("duplicate") is True)
check("重复提交未入账", r.get("booked") is False)

# 直接重投原回执（无归属）：应失败并说明原因，原回执保留
r = receipt_svc.continue_receipt(1, operator="值班员甲")
check("未补正归属时继续被拒绝", r["ok"] is False)
check("拒绝信息说明无法匹配原因", "无法匹配" in r["message"], r["message"])
check("北站仍为5（未加量）", get_metric("north.example.com")["抓取量"] == "5")
check("南站仍为2（未加量）", get_metric("south.example.com")["抓取量"] == "2")
check("没有空站点指标行", all(m["站点名称"] for m in store.rows("collect_metrics")))
check("台账仍为空", store.rows("metrics_ledger") == [])

# 2. 无法确定站点的回执保留原因
unresolved = receipt_svc.list_unresolved()
check("无法确定站点清单含 RC-39-01", any(x["回执编号"] == "RC-39-01" for x in unresolved))
reason = receipt_row()["无法确定原因"]
check("保留无法确定原因", "名称相同不能证明归属相同" in reason and "同名" in reason, reason)

# 3. 趋势报告不显示空站点/默认站点
r = metrics_svc.trend_report("")
check("空站点趋势被拒绝", r["ok"] is False and "空站点" in r["message"], r.get("message"))
r = metrics_svc.trend_report("unknown.example.com")
check("未知站点趋势被拒绝", r["ok"] is False)
r = metrics_svc.trend_report("north.example.com")
check("北站趋势正常输出5", r["ok"] is True and r["crawled"] == "5", str(r))
r = metrics_svc.trend_report("south.example.com")
check("南站趋势仍是2", r["crawled"] == "2")

# 4. 重投无法匹配时可以用原回执继续核实（回执仍在、原因可查）
r = receipt_svc.get_receipt(1)
check("原回执仍可查询用于核实", r["回执编号"] == "RC-39-01" and r["归属状态"] == "无法确定站点")

# 5. 补正关联：用错误轮次（不存在）应拒绝
r = receipt_svc.correct_attribution(1, round_id=999999, operator="值班员乙", evidence="尝试错误轮次")
check("不存在轮次补正被拒绝", r["ok"] is False and "执行轮次" in r["message"], r.get("message"))
check("拒绝后回执仍挂起", receipt_row()["归属状态"] == "无法确定站点")

# 6. 正确补正：凭原执行轮次 3901（北站、旧任务 900）
r = receipt_svc.correct_attribution(1, round_id=3901, operator="值班员乙", evidence="核对调度原始执行记录")
check("补正成功", r["ok"] is True, r.get("message"))
check("补正归属为北站", r["site"] == "north.example.com")
check("补记单已生成", r["backfill"]["补记单号"] == "BF-RC-39-01")
check("补记依据排除了同名南站任务", "南站" in r["backfill"]["补记依据"], r["backfill"]["补记依据"])
check("补记依据含原任务ID", "900" in r["backfill"]["补记依据"])
check("补记责任人为值班员乙", receipt_row()["补记人"] == "值班员乙")
check("补正后仍未入账（待继续）", receipt_row()["入账标识"] == "")
check("补正后北站仍为5", get_metric("north.example.com")["抓取量"] == "5")
check("补正后南站仍为2", get_metric("south.example.com")["抓取量"] == "2")

# 7. 补记依据可查：谁负责在映射补好后恢复补记
r = receipt_svc.backfill_basis("RC-39-01")
check("可查补记责任人", r["owner"] == "值班员乙" and r["booked"] is False)
check("可查补记单明细", len(r["backfills"]) == 1 and r["backfills"][0]["入账状态"] == "已补记待重投")

# 8. 同名任务冲突核对：新任务 901 指向南站，绝不能借同名加量
south_task = next(t for t in store.rows("price_crawler") if t["id"] == 901)
check("同名新任务确实指向南站", south_task["目标站点"] == "south.example.com")

# 9. 走原回执继续一次 → 北站 5→6，南站不动
r = receipt_svc.continue_receipt(1, operator="值班员甲")
check("继续入账成功", r["ok"] is True, r.get("message"))
check("北站抓取量变为6", get_metric("north.example.com")["抓取量"] == "6",
      get_metric("north.example.com")["抓取量"])
check("南站抓取量任何情况下不加量", get_metric("south.example.com")["抓取量"] == "2")
check("回执状态为已入账", receipt_row()["归属状态"] == "已入账")
check("台账新增一条且来源可追溯", len(store.rows("metrics_ledger")) == 1)
ledger = store.rows("metrics_ledger")[0]
check("台账站点为北站", ledger["站点名称"] == "north.example.com")
check("台账增量为1", ledger["抓取增量"] == 1)
check("台账记录回执来源", ledger["来源类型"] == "execution_receipt" and ledger["回执编号"] == "RC-39-01")

# 10. 重复继续同一回执：只加一次
r = receipt_svc.continue_receipt(1, operator="值班员甲")
check("重复继续返回成功且幂等", r["ok"] is True and r.get("duplicate") is True)
check("重复继续北站仍为6", get_metric("north.example.com")["抓取量"] == "6")
check("重复继续南站仍为2", get_metric("south.example.com")["抓取量"] == "2")
check("台账仍只有一条", len(store.rows("metrics_ledger")) == 1)

# 11. 补记单状态同步为已入账，可查是否补记
backfills = receipt_svc.list_backfills("RC-39-01")
check("补记单已入账", backfills[0]["入账状态"] == "补记已入账" and backfills[0]["入账标识"])
r = receipt_svc.backfill_basis("RC-39-01")
check("补记依据显示已入账", r["booked"] is True)

# 12. 台账可追溯到执行来源
r = metrics_svc.trace_source(ledger["id"])
check("台账可追溯到回执", r["receipt"] is not None and r["receipt"]["回执编号"] == "RC-39-01")
check("台账可追溯到执行轮次", r["round"] is not None and r["round"]["轮次编号"] == "R39-01")
check("台账可追溯到原任务900", r["task"] is not None and r["task"]["id"] == 900)

# 13. 指标列表可打开实际站点
items, _ = metrics_svc.list_entries()
resolved_sites = [
    row["站点名称"] for row in items if metrics_svc.resolve_site(row.get("站点名称", ""))
]
check("指标列表含真实北站/南站", {"north.example.com", "south.example.com"} <= set(resolved_sites))
check("指标列表无空站点", all(site for site in resolved_sites))

# 14. 新上报一份无关联回执（模拟新的丢失关联）：受理但不产生指标
r = receipt_svc.submit_receipt(
    receipt_code="RC-NEW-ORPHAN", round_id=None, plan_id=None, task_id=None,
    task_name="同名采集-北区行情", crawled=3,
)
check("无关联回执受理成功", r["ok"] is True)
check("无关联回执挂起无法确定站点", r.get("unresolved") is True)
check("挂起回执不产生任何指标变化",
      get_metric("north.example.com")["抓取量"] == "6"
      and get_metric("south.example.com")["抓取量"] == "2")
check("挂起回执不写台账", len(store.rows("metrics_ledger")) == 1)
new_receipt = next(x for x in store.rows("execution_receipt") if x["回执编号"] == "RC-NEW-ORPHAN")
check("新挂起回执原因写明同名冲突", "名称相同不能证明归属相同" in new_receipt["无法确定原因"])

# 15. 调度计划直接上报：同名冲突时拒绝猜测
r = schedule_svc.execute_now(39)
check("计划39同名冲突时执行被拒绝", r["ok"] is False and "同名" in r["message"], r.get("message"))
r = schedule_svc.report_execution(39)
check("计划39同名冲突时上报被拒绝且不产生空站点", r["ok"] is False and "空站点" in r["message"],
      r.get("message"))
check("拒绝后南北站均不变",
      get_metric("north.example.com")["抓取量"] == "6"
      and get_metric("south.example.com")["抓取量"] == "2")

# 16. 调度计划凭执行轮次上报：以轮次来源（北站）为准，且重复上报幂等
r = schedule_svc.report_execution(39, round_id=3901)
check("凭轮次上报成功且归属北站", r["ok"] is True and r["site"] == "north.example.com", r.get("message"))
check("凭轮次上报后北站变为7", get_metric("north.example.com")["抓取量"] == "7")
check("凭轮次上报南站不动", get_metric("south.example.com")["抓取量"] == "2")
r2 = schedule_svc.report_execution(39, round_id=3901)
check("同一轮次重复上报幂等", r2.get("duplicate") is True)
check("重复上报北站仍为7", get_metric("north.example.com")["抓取量"] == "7")

# 17. 健康汇总不包含空站点
r = metrics_svc.aggregate_health()
check("健康汇总站点数只含真实站点", r["sites"] == 2, str(r))

# 18. 新回执补正到错误轮次来源（轮次 3901 属北站）后，尝试伪造成南站：服务层无该路径，
#     校验 correct 永远以轮次站点为准
r = receipt_svc.correct_attribution(new_receipt["id"], round_id=3901, operator="值班员丙", evidence="补核")
check("另一回执补正同样只能归属轮次北站", r["ok"] is True and r["site"] == "north.example.com")

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    sys.exit(1)
