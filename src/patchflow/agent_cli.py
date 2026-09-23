"""第五周主策略的显式付费、Docker 隔离单任务命令行入口。"""  # 不改变第三周 baseline 批量评测命令。

from __future__ import annotations  # 延迟解析入口类型标注。

import argparse  # 要求用户显式选择任务、模型和付费许可。
import asyncio  # 驱动异步主策略与 DockerRuntime。
import json  # 读取 TaskSpec 清单并打印最小结果。
from pathlib import Path  # 处理任务文件、仓库和运行目录。

from patchflow.agent.patchflow import PatchFlowAgent  # 运行第五周显式状态机。
from patchflow.application.run_initializer import initialize_run  # 创建正式 manifest 与轨迹。
from patchflow.config.models import (  # 快照本次策略与服务配置。
    AgentConfig,  # 配置单候选和反思轮次。
    AppConfig,  # 保存正式运行快照。
    ModelConfig,  # 校验模型服务与价格字段。
    RuntimeConfig,  # 强制 Docker 后端。
)  # 完成配置类型导入。
from patchflow.domain.task import TaskSpec  # 严格校验任务 JSON。
from patchflow.model.openai_chat import OpenAIChatModel  # 只在明确允许付费后构造真实客户端。
from patchflow.runtime.docker import DockerRuntime  # 强制未知任务运行于隔离容器。


def main(argv: list[str] | None = None) -> int:  # 提供可测试的单任务 CLI 入口。
    parser = argparse.ArgumentParser(prog="python -m patchflow.agent_cli")  # 创建独立主策略命令。
    parser.add_argument("tasks", type=Path)  # 接收 TaskSpec JSON 数组文件。
    parser.add_argument("--task-id", required=True)  # 明确选择单个任务，避免无意批量收费。
    parser.add_argument("--provider", choices=("openai_chat", "openai_compatible"), required=True)  # 选择支持的模型服务类型。
    parser.add_argument("--model-id", required=True)  # 明确填写用户已核对的模型 ID。
    parser.add_argument("--base-url")  # 第三方兼容服务需要明确 HTTPS Base URL。
    parser.add_argument("--runs-root", type=Path, required=True)  # 指定独立运行 artifact 根目录。
    parser.add_argument("--max-reflections", type=int, default=2)  # 控制失败后最多允许的反思轮次。
    parser.add_argument("--max-candidates", type=int, default=1)  # 控制每轮最多生成的独立候选数量。
    parser.add_argument("--candidate-concurrency", type=int, default=2)  # 控制候选同时验证的数量。
    parser.add_argument("--allow-api-spend", action="store_true")  # 设置真实网络模型调用的硬门槛。
    args = parser.parse_args(argv)  # 解析全部用户输入。
    if not args.allow_api_spend:  # 没有付费许可时连客户端也不构造。
        parser.error("主策略真实模型调用需要 --allow-api-spend；离线验收请运行 pytest")  # 返回标准 CLI 错误。
    raw = json.loads(args.tasks.read_text(encoding="utf-8"))  # 用结构化 JSON 解析任务清单。
    if not isinstance(raw, list):  # 批次输入必须是数组。
        parser.error("任务文件必须是 TaskSpec 对象数组")  # 拒绝模糊数据格式。
    tasks = tuple(TaskSpec.model_validate(item) for item in raw)  # 严格验证任务与路径策略。
    selected = [task for task in tasks if task.task_id == args.task_id]  # 只允许任务 ID 精确命中。
    if len(selected) != 1:  # 未找到或重复任务都不能触发收费调用。
        parser.error("--task-id 必须恰好匹配一个任务")  # 避免错误选择仓库。
    task = selected[0]  # 固定本次唯一任务。
    if not task.public_commands:  # 无公开验证时第五周主策略无法报告成功。
        parser.error("第五周主策略要求至少一条公开测试命令")  # 避免不必要的付费试跑。
    model_config = ModelConfig(provider=args.provider, model=args.model_id, base_url=args.base_url)  # 校验模型服务地址与 ID。
    agent_config = AgentConfig(strategy="patchflow", max_candidates_per_round=args.max_candidates, candidate_concurrency=args.candidate_concurrency, max_reflection_rounds=args.max_reflections)  # 根据命令行选择第五周单候选或第六周多候选。
    config = AppConfig(agent=agent_config, model=model_config, runtime=RuntimeConfig(kind="docker"))  # 强制 Docker 隔离配置。
    model = OpenAIChatModel(model_config)  # 仅在显式许可后读取宿主密钥并构造客户端。
    runtime = DockerRuntime(Path(task.repo_spec.location))  # 只读挂载源仓库并在容器内创建候选副本。
    context = initialize_run(task, config, runs_root=args.runs_root)  # 保存不含明文密钥的运行快照。
    def branch_factory(workspace: Path, _root: Path) -> DockerRuntime:  # 定义第六周每个候选的 Docker Runtime 工厂。
        return DockerRuntime(workspace)  # 为当前候选创建独立容器。

    outcome = asyncio.run(PatchFlowAgent(model, config=agent_config, branch_runtime_factory=branch_factory, branch_python_executable="python").run(task, context, runtime))  # 执行显式阶段、证据图、候选分支和反思流程。
    print(json.dumps({"run_id": context.state.run_id, "status": outcome.status.value, "stop_reason": outcome.stop_reason, "final_patch": context.manifest.final_patch_path, "run_dir": str(context.layout.run_dir)}, ensure_ascii=False, indent=2))  # 输出可查阅的结果路径而不打印密钥。
    return 0 if outcome.patch is not None else 1  # 只有存在已验证补丁时返回成功退出码。


if __name__ == "__main__":  # 允许 python -m patchflow.agent_cli 调用。
    raise SystemExit(main())  # 将主函数状态交给操作系统。
