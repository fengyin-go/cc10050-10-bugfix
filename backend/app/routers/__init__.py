"""业务路由汇总：专用治理路由优先注册，通用资源路由兜底。"""
from __future__ import annotations

from app.routers import execution, metrics_governance, resource

# 专用前缀路由必须排在通用 /api/{module} 资源路由之前，避免被动态路径抢先匹配。
ROUTERS = [execution, metrics_governance, resource]
