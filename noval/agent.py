"""对话循环 + CLI 入口。

只负责「编排对话」：调模型 → 有工具调用就交给 executor → 把结果喂回 → 再调模型。
单次工具调用的全部细节（错误/截断/确认/日志）都在 executor，这里不碰。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .client import LLMClient, tool_message
from .config import Config
from .executor import Approver, execute_tool_call
from .tools import Tool, all_tools

log = logging.getLogger("noval.agent")


class Agent:
    def __init__(
        self,
        client: LLMClient,
        config: Config,
        tools: Optional[List[Tool]] = None,
        approver: Optional[Approver] = None,
        workdir: Optional[str] = None,
    ):
        self.client = client
        self.config = config
        self.tools = tools if tools is not None else all_tools()
        self.approver = approver
        # workdir 是 per-invocation 状态：本次启动各自决定，不存进全局 settings.json
        self.workdir = Path(workdir).resolve() if workdir else Path.cwd()
        self.messages: List[Dict[str, Any]] = []
        if config.system_prompt:
            self.messages.append({"role": "system", "content": config.system_prompt})

    def send(self, user_input: str) -> str:
        """处理一条用户输入，跑完工具循环，返回最终助手文本。"""
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.config.max_steps):
            resp = self.client.complete(self.messages, self.tools)
            self.messages.append(resp.assistant_message)

            if not resp.tool_calls:                       # 没有工具调用 → 最终回复
                return resp.content or ""

            # OpenAI 协议要求：每个 tool_call 都必须有对应的 tool 消息回填
            for call in resp.tool_calls:
                log.info("calling tool %s args=%s", call.name, call.arguments)
                result = execute_tool_call(call.name, call.arguments, self.config, self.approver)
                self.messages.append(tool_message(call.id, result.content))

        log.warning("达到 max_steps=%s，强制停止", self.config.max_steps)
        return "（已达到最大工具调用步数，已停止。）"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli_approver(tool: Tool, args: Dict[str, Any]) -> bool:
    print(f"\n⚠️  工具 '{tool.name}' (风险: {tool.risk.value}) 请求执行")
    print(f"    参数: {args}")
    return input("    允许执行? [y/N] ").strip().lower() in ("y", "yes")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def run_cli(argv: Optional[List[str]] = None) -> None:
    import argparse

    from .client import OpenAICompatibleClient

    parser = argparse.ArgumentParser(prog="noval")
    parser.add_argument("--workdir", help="工作目录；不指定则用当前启动目录(os.getcwd)")
    args = parser.parse_args(argv)

    # workdir 解析：显式 --workdir 优先，否则用启动目录
    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd()
    if not workdir.is_dir():
        raise SystemExit(f"--workdir 不是有效目录: {workdir}")
    os.chdir(workdir)  # 让文件工具/将来 run_bash 的子进程，相对路径都落在 workdir

    setup_logging()
    log.info("workdir = %s", workdir)
    config = Config.load()
    client = OpenAICompatibleClient(config.base_url, config.resolve_api_key(), config.model)
    agent = Agent(client, config, approver=_cli_approver, workdir=str(workdir))

    print(f"Noval 已就绪 (workdir: {workdir})。输入 'exit' 退出。")
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() == "exit":
            break
        # 模型调用/网络异常不该掀翻整个会话：兜住、报错、保留历史、继续
        try:
            reply = agent.send(user_input)
        except Exception as e:
            log.exception("处理输入时出错")
            print(f"Noval: [出错 {type(e).__name__}: {e}]（会话已保留，可继续输入）")
            continue
        print(f"Noval: {reply}")
    print("再见！")


if __name__ == "__main__":
    run_cli()
