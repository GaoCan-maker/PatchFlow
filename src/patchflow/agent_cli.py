"""第五周主策略的显式付费、Docker 隔离单任务命令行入口。"""  # 不改变第三周 baseline 批量评测命令。

from __future__ import annotations  # 延迟解析入口类型标注。

import argparse  # 要求用户显式选择任务、模型和付费许可。
import asyncio  # 驱动异步主策略与 DockerRuntime。
import json  # 读取 TaskSpec 清单并打印最小结果。
from pathlib import Path  # 处理任务文件、仓库和运行目录。

from patchflow.agent.one_shot import OneShotAgent  # 提供同模型、同镜像的一次性补丁基线。
from patchflow.agent.patchflow import PatchFlowAgent  # 运行第五周显式状态机。
from patchflow.application.run_initializer import initialize_run  # 创建正式 manifest 与轨迹。
from patchflow.config.models import (  # 快照本次策略与服务配置。
    AgentConfig,  # 配置单候选和反思轮次。
    AppConfig,  # 保存正式运行快照。
    ModelConfig,  # 校验模型服务与价格字段。
    RuntimeConfig,  # 强制 Docker 后端。
)  # 完成配置类型导入。
from patchflow.domain.enums import TaskSource  # 区分普通任务成功与 SWE-bench prediction 生成。
from patchflow.domain.task import TaskSpec  # 严格校验任务 JSON。
from patchflow.localization import IndexSettings  # 为大型官方仓库配置完整文件枚举和索引容量。
from patchflow.model.openai_chat import OpenAIChatModel  # 只在明确允许付费后构造真实客户端。
from patchflow.runtime.docker import (  # 强制未知任务运行于受限容器并允许选择官方实例镜像。
    DockerRuntime,  # 提供每次运行独立的受限容器工作区。
    DockerRuntimeConfig,  # 配置官方实例镜像和资源上限。
)  # 完成 Docker Runtime 导入。
from patchflow.swebench.inference import (  # 导入 benchmark 推理环境辅助函数。
    official_instance_image,  # 按官方规则生成当前实例的环境镜像名。
)  # 完成 SWE-bench 推理辅助函数导入。


def main(argv: list[str] | None = None) -> int:  # 提供可测试的单任务 CLI 入口。
    parser = argparse.ArgumentParser(prog="python -m patchflow.agent_cli")  # 创建独立主策略命令。
    parser.add_argument("tasks", type=Path)  # 接收 TaskSpec JSON 数组文件。
    parser.add_argument("--task-id", required=True)  # 明确选择单个任务，避免无意批量收费。
    parser.add_argument("--provider", choices=("openai_chat", "openai_compatible"), required=True)  # 选择支持的模型服务类型。
    parser.add_argument("--strategy", choices=("patchflow", "one_shot"), default="patchflow")  # 在主方法和必要单轮基线之间显式选择。
    parser.add_argument("--model-id", required=True)  # 明确填写用户已核对的模型 ID。
    parser.add_argument("--base-url")  # 第三方兼容服务需要明确 HTTPS Base URL。
    parser.add_argument("--runs-root", type=Path, required=True)  # 指定独立运行 artifact 根目录。
    parser.add_argument("--max-reflections", type=int, default=2)  # 控制失败后最多允许的反思轮次。
    parser.add_argument("--max-candidates", type=int, default=1)  # 控制每轮最多生成的独立候选数量。
    parser.add_argument("--candidate-concurrency", type=int, default=2)  # 控制候选同时验证的数量。
    parser.add_argument("--benchmark-prediction-mode", action="store_true")  # 显式允许 SWE-bench 在不知道隐藏测试时输出待评分补丁。
    parser.add_argument("--docker-image")  # 允许覆盖自动推导的官方实例镜像或普通 PatchFlow 镜像。
    parser.add_argument("--docker-cpus", type=float, default=2.0)  # 为大型官方仓库设置受限 CPU 配额。
    parser.add_argument("--docker-memory-mb", type=int, default=4096)  # 为官方依赖和索引设置受限内存。
    parser.add_argument("--docker-workspace-mb", type=int, default=2048)  # 为容器内仓库副本设置 tmpfs 上限。
    parser.add_argument("--index-max-files", type=int)  # 允许为大型仓库显式设置可索引 Python 文件上限。
    parser.add_argument("--docker-output-chars", type=int)  # 允许完整接收大型仓库的 Git 文件列表。
    parser.add_argument("--allow-api-spend", action="store_true")  # 设置真实网络模型调用的硬门槛。
    args = parser.parse_args(argv)  # 解析全部用户输入。
    if not args.allow_api_spend:  # 没有付费许可时连客户端也不构造。
        parser.error("真实模型调用需要 --allow-api-spend；离线验收请运行 pytest")  # 主方法与 one-shot 基线都遵守同一付费门槛。
    raw = json.loads(args.tasks.read_text(encoding="utf-8"))  # 用结构化 JSON 解析任务清单。
    if not isinstance(raw, list):  # 批次输入必须是数组。
        parser.error("任务文件必须是 TaskSpec 对象数组")  # 拒绝模糊数据格式。
    tasks = tuple(TaskSpec.model_validate(item) for item in raw)  # 严格验证任务与路径策略。
    selected = [task for task in tasks if task.task_id == args.task_id]  # 只允许任务 ID 精确命中。
    if len(selected) != 1:  # 未找到或重复任务都不能触发收费调用。
        parser.error("--task-id 必须恰好匹配一个任务")  # 避免错误选择仓库。
    task = selected[0]  # 固定本次唯一任务。
    is_swebench = task.source is TaskSource.SWE_BENCH  # 缓存任务来源用于后续安全条件和运行快照。
    if args.benchmark_prediction_mode and not is_swebench:  # 普通任务不能利用预测模式绕过公开验证。
        parser.error("--benchmark-prediction-mode 只允许 SWE-bench 任务")  # 在读取 API key 前拒绝错误配置。
    if not task.public_commands and not (is_swebench and args.benchmark_prediction_mode):  # 没有测试的普通运行仍不能报告成功。
        parser.error("无公开测试的 SWE-bench 任务必须显式设置 --benchmark-prediction-mode")  # 要求用户确认补丁只是待官方评分 prediction。
    index_max_files = args.index_max_files if args.index_max_files is not None else (2_000 if is_swebench else 400)  # 官方仓库默认容纳已确认的 SymPy 1,449 个 Python 文件。
    docker_output_chars = args.docker_output_chars if args.docker_output_chars is not None else (100_000 if is_swebench else 20_000)  # 官方仓库默认完整接收已测得约 51 KB 的文件列表。
    if index_max_files < 1 or not 1 <= docker_output_chars <= 1_000_000:  # 在任何付费模型客户端构造之前校验预算边界。
        parser.error("--index-max-files 必须大于零，--docker-output-chars 必须在 1 到 1000000 之间")  # 防止无效或无限输出配置。
    model_config = ModelConfig(provider=args.provider, model=args.model_id, base_url=args.base_url)  # 校验模型服务地址与 ID。
    agent_config = AgentConfig(strategy=args.strategy, max_candidates_per_round=args.max_candidates, candidate_concurrency=args.candidate_concurrency, max_reflection_rounds=args.max_reflections)  # 准确快照主方法或单轮基线及其预算配置。
    runtime_kind = "swe_bench" if is_swebench else "docker"  # 在运行快照中准确记录环境类型。
    config = AppConfig(agent=agent_config, model=model_config, runtime=RuntimeConfig(kind=runtime_kind, cpu_limit=args.docker_cpus, memory_limit_mb=args.docker_memory_mb))  # 保存不含密钥的 Docker 资源配置。
    model = OpenAIChatModel(model_config)  # 仅在显式许可后读取宿主密钥并构造客户端。
    image = args.docker_image or (official_instance_image(task.task_id) if is_swebench else "patchflow-runtime:py311")  # SWE-bench 默认使用与实例匹配的官方依赖镜像。
    branch_python = "/opt/miniconda3/envs/testbed/bin/python" if is_swebench else "python"  # 官方镜像的默认 python 属于 base 环境，语法检查必须使用任务 testbed 环境。
    docker_config = DockerRuntimeConfig(image=image, cpus=args.docker_cpus, memory_mb=args.docker_memory_mb, workspace_mb=args.docker_workspace_mb, temp_mb=512, max_output_chars=docker_output_chars)  # 对主候选和所有分支复用同一环境与输出限制。
    runtime = DockerRuntime(Path(task.repo_spec.location), docker_config)  # 只读挂载源仓库并在指定镜像内创建候选副本。
    context = initialize_run(task, config, runs_root=args.runs_root)  # 保存不含明文密钥的运行快照。
    def branch_factory(workspace: Path, _root: Path) -> DockerRuntime:  # 定义第六周每个候选的 Docker Runtime 工厂。
        return DockerRuntime(workspace, docker_config)  # 为当前候选创建相同官方环境下的独立容器。

    agent = OneShotAgent(model) if args.strategy == "one_shot" else PatchFlowAgent(model, config=agent_config, branch_runtime_factory=branch_factory, branch_python_executable=branch_python, benchmark_prediction_mode=args.benchmark_prediction_mode, index_settings=IndexSettings(max_files=index_max_files))  # 主方法按本次仓库规模索引，基线使用相同 Docker 输出预算。
    outcome = asyncio.run(agent.run(task, context, runtime))  # 在相同任务、模型和官方实例镜像中执行所选策略。
    print(json.dumps({"run_id": context.state.run_id, "status": outcome.status.value, "stop_reason": outcome.stop_reason, "final_patch": context.manifest.final_patch_path, "run_dir": str(context.layout.run_dir)}, ensure_ascii=False, indent=2))  # 输出可查阅的结果路径而不打印密钥。
    return 0 if outcome.patch is not None else 1  # 公开测试通过或已生成待官方评分 prediction 时返回成功退出码。


if __name__ == "__main__":  # 允许 python -m patchflow.agent_cli 调用。
    raise SystemExit(main())  # 将主函数状态交给操作系统。
