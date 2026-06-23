"""配置层。

加载策略：内置默认值 ← ~/.noval/settings.json 覆盖。文件缺失也能用默认值正常启动。
api_key 永不存明文：只记录「从哪个环境变量取」，运行时再解析。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .tools import Risk

# 默认配置：任何字段都可被 settings.json 覆盖
DEFAULTS: Dict[str, Any] = {
    "model": "deepseek-v4-pro",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",        # 从该环境变量读取 key
    "max_steps": 40,                          # 单轮用户输入内，工具循环的最大步数(build/调试类任务费步数)
    "max_tool_output_chars": 8000,            # 工具输出超过此长度即截断
    "auto_approve": ["read", "write"],        # 这些风险级别免确认；其余(dangerous)需确认
}
# 注：system_prompt 不在这里——它是 agent 的行为定义(属代码)，不是「全局稳定偏好」，
# 故不开放给 settings.json 覆盖。见 noval/agent.py 的 DEFAULT_SYSTEM_PROMPT。


def settings_path() -> Path:
    return Path.home() / ".noval" / "settings.json"


@dataclass
class Config:
    model: str
    base_url: str
    api_key_env: str
    max_steps: int
    max_tool_output_chars: int
    auto_approve: List[str]
    api_key: str = ""          # 可选：直接写在 ~/.noval/settings.json 里（该文件不在仓库内）
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        merged = dict(DEFAULTS)
        p = path or settings_path()
        if p.exists():
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SystemExit(f"settings.json 不是合法 JSON: {e}")  # 漏逗号等不该是难看的 traceback
            merged.update(user)  # 顶层覆盖；当前配置无深层嵌套，浅合并足够

        # 校验：错配置要给出清晰报错，而不是静默跑歪
        # （例如 auto_approve 写成字符串 "read" → list() 会拆成 ['r','e','a','d']）
        if not isinstance(merged["auto_approve"], list):
            raise SystemExit('settings.json: auto_approve 必须是数组，如 ["read", "write"]')
        for key in ("max_steps", "max_tool_output_chars"):
            try:
                merged[key] = int(merged[key])
            except (TypeError, ValueError):
                raise SystemExit(f"settings.json: {key} 必须是整数")

        return cls(
            model=merged["model"],
            base_url=merged["base_url"],
            api_key_env=merged["api_key_env"],
            max_steps=merged["max_steps"],
            max_tool_output_chars=merged["max_tool_output_chars"],
            auto_approve=list(merged["auto_approve"]),
            api_key=merged.get("api_key", ""),
            raw=merged,
        )

    def resolve_api_key(self) -> str:
        """解析 api_key，优先级：settings.json 里的 api_key → 环境变量 → 报错。

        settings.json 在用户主目录、不在仓库内，因此把 key 写在那里不会随代码泄露；
        但它仍是磁盘上的明文，别提交、别放进仓库内的 settings.example.json。
        """
        if self.api_key:
            return self.api_key
        key = os.environ.get(self.api_key_env)
        if key:
            return key
        raise SystemExit(
            "未找到 API key，二选一：\n"
            f"  1) 在 {settings_path()} 里加一行 \"api_key\": \"sk-...\"\n"
            f"  2) 设置环境变量 {self.api_key_env}"
            f"（PowerShell: $env:{self.api_key_env}=\"sk-...\"）"
        )

    def needs_confirmation(self, risk: Risk) -> bool:
        """确认门策略：风险级别不在白名单里，就需要用户确认。"""
        return risk.value not in self.auto_approve
