"""官方 SWE-bench Evaluation Harness 的受控进程适配器。"""  # 本模块只编排官方工具，不重新实现 resolved 判定。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 异步运行可能持续数小时的官方评测进程。
import codecs  # 增量解码跨读取块的 UTF-8 字符。
import json  # 解析官方 Harness 生成的 JSON 报告。
import os  # 构造最小环境并管理 Linux 进程组。
import signal  # 在超时时终止整个 Harness 进程组。
import sys  # 默认使用当前 Python 解释器调用官方模块。
import time  # 记录真实墙钟耗时。
from enum import StrEnum  # 定义稳定机器可读的评测状态。
from pathlib import Path  # 管理预测文件、工作目录和结果目录。
from typing import Any  # 解析不同 SWE-bench 版本的报告结构。

from pydantic import (  # 严格校验官方 Harness 配置和报告。
    BaseModel,  # 构造可序列化的配置与结果基类。
    ConfigDict,  # 声明冻结和拒绝额外字段策略。
    Field,  # 声明数值范围与文本模式。
    field_validator,  # 对文本和实例 ID 集合做跨格式校验。
)  # 完成 Pydantic 边界工具导入。

from patchflow.runtime.output import OutputAccumulator  # 复用项目已有的常量内存输出收集器。
from patchflow.swebench.models import read_predictions  # 在启动昂贵评测前验证 prediction JSONL。

_SENSITIVE_ENVIRONMENT_KEYS = frozenset(  # 明确不传给官方评测子进程的模型服务凭据。
    {  # 开始敏感变量集合。
        "AGICTO_API_KEY",  # 移除用户可能为兼容服务设置的密钥。
        "ANTHROPIC_API_KEY",  # 移除与代码生成相关的第三方密钥。
        "OPENAI_API_KEY",  # 移除 OpenAI SDK 默认密钥。
        "PATCHFLOW_MODEL_API_KEY",  # 移除 PatchFlow 自定义模型密钥。
    }  # 结束敏感变量集合。
)  # 冻结集合避免运行时误改。


class SweBenchInstanceStatus(StrEnum):  # 定义单个官方实例的最终分类。
    """区分能力失败、基础设施错误和报告缺失。"""  # resolved 仍完全来自官方报告。

    RESOLVED = "resolved"  # 官方报告明确给出 resolved=true。
    UNRESOLVED = "unresolved"  # 官方报告明确给出 resolved=false。
    ERROR = "error"  # 官方报告将实例归为评测错误。
    EMPTY_PATCH = "empty_patch"  # 官方报告指出没有可执行补丁。
    MISSING = "missing"  # 预期实例没有出现在任何可解析报告中。


class HarnessProcessStatus(StrEnum):  # 定义官方进程本身的执行状态。
    """防止把 Harness 崩溃计成 Agent 未修复。"""  # 这是实验可信度的关键分类。

    COMPLETED = "completed"  # 子进程成功退出且结果可以解析。
    PROCESS_FAILED = "process_failed"  # 官方命令以非零状态退出。
    TIMED_OUT = "timed_out"  # 外层硬超时终止了官方进程组。
    RESULTS_MISSING = "results_missing"  # 进程成功但没有找到实例结果。


class SweBenchHarnessConfig(BaseModel):  # 保存一次可复现官方评测的全部公开参数。
    """官方 run_evaluation 命令的受控配置。"""  # 不包含模型 API 密钥和金补丁。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 拒绝拼写错误并防止运行中改参。
    dataset_name: str = Field(min_length=1)  # 例如 princeton-nlp/SWE-bench_Lite。
    split: str = Field(default="test", min_length=1)  # 指定官方数据集分片。
    run_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")  # 关联官方日志目录。
    workdir: Path  # 指定官方 Harness 写 logs/evaluation 的工作目录。
    instance_ids: tuple[str, ...]  # 固定本次评测分母和顺序。
    python_executable: str = Field(default=sys.executable, min_length=1)  # 支持在独立 Conda 环境运行 swebench。
    max_workers: int = Field(default=1, ge=1, le=64)  # 限制同时启动的 Docker 实例数量。
    timeout_seconds: int = Field(default=1_800, ge=1)  # 传给官方 Harness 的单实例超时。
    process_timeout_seconds: float = Field(default=21_600.0, gt=0)  # 限制整批官方命令总墙钟时间。
    termination_grace_seconds: float = Field(default=5.0, gt=0)  # 给官方进程清理容器和日志的宽限时间。
    max_output_chars: int = Field(default=100_000, ge=1)  # 限制 stdout 和 stderr 各自占用内存。

    @field_validator("dataset_name", "split", "python_executable")  # 统一处理不允许为空的自由文本。
    @classmethod  # 校验器不依赖实例状态。
    def normalize_text(cls, value: str) -> str:  # 去除命令参数首尾空白。
        cleaned = value.strip()  # 生成规范化参数。
        if not cleaned:  # 防止纯空白绕过最小长度。
            raise ValueError("Harness 文本配置不能为空")  # 给出明确配置错误。
        return cleaned  # 返回规范化值。

    @field_validator("instance_ids")  # 检查正式评测分母明确且唯一。
    @classmethod  # 校验器不读取其他字段。
    def validate_instance_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:  # 规范化实例 ID 集合。
        cleaned = tuple(value.strip() for value in values)  # 清除复制命令带来的空白。
        if not cleaned or any(not value for value in cleaned):  # 不允许空批次或空实例 ID。
            raise ValueError("Harness instance_ids 不能为空")  # 在 Docker 启动前失败。
        if len(set(cleaned)) != len(cleaned):  # 同一实例只能评测一次。
            raise ValueError("Harness instance_ids 不能重复")  # 保护统计分母。
        return cleaned  # 保持调用方给定的稳定顺序。


class SweBenchInstanceResult(BaseModel):  # 保存官方报告中一个实例的归一化结果。
    """不对官方 resolved 结论做二次推导。"""  # 只做格式适配和缺失标记。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 保持报告模式稳定。
    instance_id: str  # 保存官方实例主键。
    status: SweBenchInstanceStatus  # 保存解析出的互斥状态。
    patch_applied: bool | None = None  # 在官方报告提供该信息时保留诊断值。
    source_file: str | None = None  # 记录状态来自哪个官方 JSON 文件。


class SweBenchHarnessReport(BaseModel):  # 保存整次官方评测的归一化报告。
    """保留逐实例结果与可重算聚合计数。"""  # 不隐藏缺失实例。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 拒绝随意扩展不可追溯指标。
    run_id: str  # 关联官方运行标识。
    expected_instances: int = Field(ge=1)  # 保存固定评测分母。
    resolved: int = Field(ge=0)  # 保存 resolved 实例数量。
    unresolved: int = Field(ge=0)  # 保存确定未解决实例数量。
    errors: int = Field(ge=0)  # 保存官方评测错误数量。
    empty_patches: int = Field(ge=0)  # 保存空补丁数量。
    missing: int = Field(ge=0)  # 保存未出现在报告中的实例数量。
    instances: tuple[SweBenchInstanceResult, ...]  # 按预期实例顺序保存全部结果。
    source_files: tuple[str, ...]  # 列出实际解析的官方 JSON 文件。


class SweBenchHarnessExecution(BaseModel):  # 汇总进程执行和官方结果两个层次。
    """一次官方 Harness 调用的完整可审计结果。"""  # 调用方据此区分能力与基础设施失败。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 固定机器可读结果格式。
    status: HarnessProcessStatus  # 保存外层进程状态。
    command: tuple[str, ...]  # 保存不含密钥的实际参数数组。
    return_code: int | None  # 超时时进程可能没有正常退出码。
    timed_out: bool  # 明确记录是否由外层超时终止。
    duration_seconds: float = Field(ge=0)  # 保存完整墙钟耗时。
    stdout: str  # 保存有界标准输出用于诊断。
    stderr: str  # 保存有界错误输出用于诊断。
    output_truncated: bool  # 标记任一输出是否发生截断。
    report: SweBenchHarnessReport | None = None  # 成功解析时附带逐实例报告。


def build_harness_command(config: SweBenchHarnessConfig, predictions_path: Path | str) -> tuple[str, ...]:  # 构造官方模块命令但不执行。
    prediction_argument = str(predictions_path)  # 保留官方特殊值 gold 或具体 JSONL 路径。
    command = [  # 使用参数数组完全避免 Shell 拼接。
        config.python_executable,  # 选择安装了官方 swebench 包的 Python。
        "-m",  # 通过模块入口调用官方实现。
        "swebench.harness.run_evaluation",  # 复用官方最终评分逻辑。
        "--dataset_name",  # 指定数据集参数名。
        config.dataset_name,  # 传入固定数据集。
        "--predictions_path",  # 指定 prediction 来源参数名。
        prediction_argument,  # 传入标准 JSONL 路径或 gold 特殊值。
        "--max_workers",  # 指定官方并发参数名。
        str(config.max_workers),  # 把受限整数转换为命令文本。
        "--run_id",  # 指定唯一运行标识参数名。
        config.run_id,  # 传入可用于定位日志的 run ID。
        "--split",  # 指定数据集分片参数名。
        config.split,  # 传入固定分片。
        "--timeout",  # 指定单实例超时参数名。
        str(config.timeout_seconds),  # 传入官方超时秒数。
        "--instance_ids",  # 限制评测到显式实例集合。
        *config.instance_ids,  # 逐个追加实例 ID 而不经过 Shell 展开。
    ]  # 完成官方命令构造。
    return tuple(command)  # 返回不可变命令便于测试和审计。


def _evaluation_environment() -> dict[str, str]:  # 构造官方 Harness 子进程环境。
    environment = dict(os.environ)  # 保留 Docker、缓存和数据集工具需要的普通环境。
    for key in _SENSITIVE_ENVIRONMENT_KEYS:  # 遍历明确的模型服务凭据。
        environment.pop(key, None)  # 确保评测进程无法继承 Agent API 密钥。
    return environment  # 返回隔离后的环境副本。


async def _capture_stream(stream: asyncio.StreamReader | None, accumulator: OutputAccumulator) -> None:  # 常量内存读取一个子进程流。
    if stream is None:  # PIPE 理论上总会存在，但保持防御性。
        return  # 没有流时无需处理。
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")  # 防止多字节字符跨块时被错误替换。
    while True:  # 持续读取直到官方进程关闭流。
        chunk = await stream.read(65_536)  # 以固定块大小平衡吞吐和内存。
        if not chunk:  # 空字节表示 EOF。
            break  # 结束读取循环。
        accumulator.append(decoder.decode(chunk, final=False))  # 增量解码并仅保留有界头尾。
    accumulator.append(decoder.decode(b"", final=True))  # 刷新解码器中残留的半个字符。


async def _terminate_process_group(process: asyncio.subprocess.Process, grace_seconds: float) -> None:  # 超时时回收官方进程及其直接子进程。
    if process.returncode is not None:  # 已退出进程不需要重复发送信号。
        return  # 保持终止操作幂等。
    if os.name == "posix":  # WSL 和 Linux 可以使用独立进程组。
        os.killpg(process.pid, signal.SIGTERM)  # 先请求官方 Harness 正常清理。
    else:  # Windows 原生环境缺少相同的 POSIX 进程组语义。
        process.terminate()  # 至少终止直接子进程。
    try:  # 给清理逻辑有限宽限期。
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)  # 等待正常退出。
        return  # 进程已退出时结束函数。
    except TimeoutError:  # 宽限期后仍存活则执行强制终止。
        pass  # 继续进入 kill 分支。
    if os.name == "posix":  # Linux 下强制杀死整个进程组。
        os.killpg(process.pid, signal.SIGKILL)  # 避免残留后台 Python 进程。
    else:  # Windows 下只能强制终止直接子进程。
        process.kill()  # 请求操作系统立即结束进程。
    await process.wait()  # 等待系统回收子进程句柄。


def _merge_result(  # 把一个来源文件中的实例状态合并到统一映射。
    results: dict[str, SweBenchInstanceResult],  # 接收当前已解析结果映射。
    *,  # 强制使用具名参数降低状态顺序出错风险。
    instance_id: str,  # 接收官方实例 ID。
    status: SweBenchInstanceStatus,  # 接收该来源报告给出的状态。
    patch_applied: bool | None,  # 接收可选补丁应用诊断。
    source_file: Path,  # 接收实际 JSON 来源路径。
) -> None:  # 原地更新结果或拒绝矛盾报告。
    candidate = SweBenchInstanceResult(instance_id=instance_id, status=status, patch_applied=patch_applied, source_file=str(source_file))  # 构造规范结果。
    previous = results.get(instance_id)  # 查询其他报告是否已经给出该实例结论。
    if previous is not None and previous.status is not status:  # 同一 run 的互斥状态不能互相矛盾。
        raise ValueError(f"官方报告对 {instance_id} 给出矛盾状态：{previous.status} 与 {status}")  # 阻止随意选择一个结果。
    if previous is None or (previous.patch_applied is None and patch_applied is not None):  # 优先保留诊断信息更完整的结果。
        results[instance_id] = candidate  # 保存新的规范结果。


def _status_from_payload(payload: dict[str, Any]) -> tuple[SweBenchInstanceStatus, bool | None] | None:  # 解析官方 per-instance report。
    patch_applied_value = payload.get("patch_successfully_applied", payload.get("patch_applied"))  # 兼容官方版本字段命名。
    patch_applied = patch_applied_value if isinstance(patch_applied_value, bool) else None  # 只接受明确布尔值。
    if isinstance(payload.get("resolved"), bool):  # 当前官方 report 使用 resolved 布尔值。
        status = SweBenchInstanceStatus.RESOLVED if payload["resolved"] else SweBenchInstanceStatus.UNRESOLVED  # 直接映射官方结论。
        return status, patch_applied  # 返回状态和诊断字段。
    textual = payload.get("status")  # 某些汇总工具使用字符串状态。
    if isinstance(textual, str):  # 只处理明确文本。
        normalized = textual.strip().lower()  # 大小写无关地解析。
        aliases = {  # 声明可接受的稳定状态别名。
            "resolved": SweBenchInstanceStatus.RESOLVED,  # 映射成功状态。
            "unresolved": SweBenchInstanceStatus.UNRESOLVED,  # 映射能力失败状态。
            "error": SweBenchInstanceStatus.ERROR,  # 映射评测错误状态。
            "empty_patch": SweBenchInstanceStatus.EMPTY_PATCH,  # 映射空补丁状态。
        }  # 完成别名映射。
        if normalized in aliases:  # 只接受已知状态避免猜测。
            return aliases[normalized], patch_applied  # 返回解析结果。
    return None  # 未识别结构交给其他汇总规则处理。


def _collect_report_payload(payload: Any, source_file: Path, results: dict[str, SweBenchInstanceResult]) -> None:  # 从多版本 JSON 结构提取实例状态。
    if not isinstance(payload, dict):  # 官方结果根通常是对象，其他类型没有稳定实例语义。
        return  # 忽略不相关 JSON。
    list_fields = {  # 兼容官方汇总报告中的实例 ID 数组。
        "resolved_ids": SweBenchInstanceStatus.RESOLVED,  # 当前或旧版成功 ID 集合。
        "resolved_instances": SweBenchInstanceStatus.RESOLVED,  # 兼容另一种成功字段名。
        "unresolved_ids": SweBenchInstanceStatus.UNRESOLVED,  # 当前或旧版失败 ID 集合。
        "unresolved_instances": SweBenchInstanceStatus.UNRESOLVED,  # 兼容另一种失败字段名。
        "error_ids": SweBenchInstanceStatus.ERROR,  # 显式评测错误 ID 集合。
        "error_instances": SweBenchInstanceStatus.ERROR,  # 兼容另一种错误字段名。
        "empty_patch_ids": SweBenchInstanceStatus.EMPTY_PATCH,  # 空补丁 ID 集合。
        "empty_patch_instances": SweBenchInstanceStatus.EMPTY_PATCH,  # 兼容官方空补丁字段名。
    }  # 完成汇总字段映射。
    for field_name, status in list_fields.items():  # 遍历全部已知汇总字段。
        values = payload.get(field_name)  # 读取可能存在的实例列表。
        if isinstance(values, list):  # 只接受明确数组结构。
            for value in values:  # 遍历每个实例 ID。
                if isinstance(value, str) and value.strip():  # 忽略无法关联的非字符串值。
                    _merge_result(results, instance_id=value.strip(), status=status, patch_applied=None, source_file=source_file)  # 合并汇总结论。
    instances_payload = payload.get("instances")  # 检查部分封装工具使用的 instances 容器。
    if isinstance(instances_payload, dict):  # 字典形式以实例 ID 为键。
        _collect_report_payload(instances_payload, source_file, results)  # 递归复用顶层实例映射逻辑。
    elif isinstance(instances_payload, list):  # 列表形式通常在对象内保存 instance_id。
        for item in instances_payload:  # 遍历逐实例对象。
            if not isinstance(item, dict):  # 跳过不可解释条目。
                continue  # 处理下一个条目。
            identifier = item.get("instance_id")  # 读取显式实例 ID。
            parsed = _status_from_payload(item)  # 读取实例状态。
            if isinstance(identifier, str) and parsed is not None:  # 只有 ID 和状态都明确时合并。
                _merge_result(results, instance_id=identifier, status=parsed[0], patch_applied=parsed[1], source_file=source_file)  # 保存列表条目。
    for key, value in payload.items():  # 兼容官方 report.json 的 instance_id 到详情映射。
        if not isinstance(value, dict):  # 非对象值已由汇总字段逻辑处理。
            continue  # 跳过标量和数组。
        parsed = _status_from_payload(value)  # 尝试把子对象解释为实例结果。
        if parsed is not None and isinstance(key, str):  # 键即实例 ID 且值包含明确状态。
            _merge_result(results, instance_id=key, status=parsed[0], patch_applied=parsed[1], source_file=source_file)  # 合并逐实例结果。


def parse_harness_results(results_root: Path, *, run_id: str, expected_instance_ids: tuple[str, ...]) -> SweBenchHarnessReport:  # 解析官方结果目录。
    if not expected_instance_ids or len(set(expected_instance_ids)) != len(expected_instance_ids):  # 结果解析必须有明确唯一分母。
        raise ValueError("expected_instance_ids 必须非空且唯一")  # 防止缺失数量失真。
    if not results_root.exists():  # 进程可能在创建日志前就失败。
        raise FileNotFoundError(f"SWE-bench 结果目录不存在：{results_root}")  # 将基础设施问题交给调用方分类。
    source_files = tuple(sorted(path for path in results_root.rglob("*.json") if path.is_file()))  # 稳定枚举所有可能的官方 JSON 报告。
    if not source_files:  # 空目录不能推导 resolved 结果。
        raise FileNotFoundError(f"SWE-bench 结果目录没有 JSON 报告：{results_root}")  # 明确结果缺失。
    parsed_sources: list[str] = []  # 只记录成功解析且含 JSON 的来源文件。
    results: dict[str, SweBenchInstanceResult] = {}  # 收集逐实例规范结果。
    for source_file in source_files:  # 顺序解析所有官方输出。
        try:  # 单个日志旁路 JSON 损坏不应遮蔽其他有效报告。
            payload = json.loads(source_file.read_text(encoding="utf-8"))  # 使用标准 JSON 解析器。
        except (OSError, UnicodeError, json.JSONDecodeError):  # 忽略非报告或写入中断的 JSON 文件。
            continue  # 继续搜索真实 report.json 或 results.json。
        parsed_sources.append(str(source_file))  # 记录可读取来源便于审计。
        _collect_report_payload(payload, source_file, results)  # 提取该文件中的实例状态。
    expected_set = set(expected_instance_ids)  # 构造快速过滤集合。
    unknown_ids = set(results) - expected_set  # 检查结果目录是否混入其他运行实例。
    if unknown_ids:  # 错误 run 目录会污染正式分母。
        raise ValueError(f"官方结果包含非预期实例：{sorted(unknown_ids)}")  # 拒绝跨运行混合。
    instances: list[SweBenchInstanceResult] = []  # 按配置顺序构造完整结果。
    for instance_id in expected_instance_ids:  # 遍历固定分母中的每个实例。
        result = results.get(instance_id)  # 查询官方报告是否包含该实例。
        if result is None:  # 缺失不能静默算 unresolved。
            result = SweBenchInstanceResult(instance_id=instance_id, status=SweBenchInstanceStatus.MISSING)  # 单独标记报告缺失。
        instances.append(result)  # 保存当前实例结果。
    frozen_instances = tuple(instances)  # 固定完整逐实例序列。
    return SweBenchHarnessReport(  # 从逐实例状态重算所有聚合计数。
        run_id=run_id,  # 保存调用方指定的运行标识。
        expected_instances=len(expected_instance_ids),  # 保存固定分母。
        resolved=sum(item.status is SweBenchInstanceStatus.RESOLVED for item in frozen_instances),  # 统计官方成功。
        unresolved=sum(item.status is SweBenchInstanceStatus.UNRESOLVED for item in frozen_instances),  # 统计官方未解决。
        errors=sum(item.status is SweBenchInstanceStatus.ERROR for item in frozen_instances),  # 统计评测错误。
        empty_patches=sum(item.status is SweBenchInstanceStatus.EMPTY_PATCH for item in frozen_instances),  # 统计空补丁。
        missing=sum(item.status is SweBenchInstanceStatus.MISSING for item in frozen_instances),  # 统计报告缺失。
        instances=frozen_instances,  # 保存完整结果。
        source_files=tuple(parsed_sources),  # 保存可追溯来源列表。
    )  # 完成规范报告构造。


async def run_harness(  # 在显式授权后调用官方 SWE-bench Evaluation Harness。
    config: SweBenchHarnessConfig,  # 接收完整受控配置。
    predictions_path: Path | str,  # 接收标准 prediction JSONL 或官方 gold 特殊值。
    *,  # 强制危险开关具名传递。
    allow_execution: bool = False,  # 默认禁止启动大量 Docker 评测。
) -> SweBenchHarnessExecution:  # 返回区分进程和实例结果的结构化报告。
    if not allow_execution:  # 任何真实 Harness 运行都必须由用户显式确认。
        raise PermissionError("运行官方 SWE-bench Harness 需要 allow_execution=True")  # 防止测试或导入时意外消耗资源。
    config.workdir.resolve(strict=True)  # 在启动进程前确认工作目录存在。
    prediction_argument = str(predictions_path)  # 统一处理 Path 和 gold 特殊值。
    if prediction_argument != "gold":  # 自定义预测必须先完成格式与分母检查。
        prediction_file = Path(prediction_argument).resolve(strict=True)  # 确认 prediction 文件真实存在。
        predictions = read_predictions(prediction_file)  # 严格拒绝多余字段、空补丁和重复实例。
        predicted_ids = tuple(item.instance_id for item in predictions)  # 提取实际预测分母。
        if set(predicted_ids) != set(config.instance_ids):  # 要求文件与命令实例集合完全一致。
            raise ValueError("prediction 文件实例与 Harness instance_ids 不一致")  # 防止遗漏被误算或额外实例混入。
        prediction_argument = str(prediction_file)  # 命令使用规范化绝对路径。
    command = build_harness_command(config, prediction_argument)  # 构造可记录且无 Shell 的官方命令。
    started_at = time.monotonic()  # 在创建子进程前记录墙钟起点。
    process = await asyncio.create_subprocess_exec(  # 启动官方模块而不经过 Shell。
        *command,  # 逐参数传入已校验命令。
        cwd=config.workdir,  # 让官方日志稳定写入指定工作目录。
        env=_evaluation_environment(),  # 移除 Agent 模型密钥后再继承普通环境。
        stdout=asyncio.subprocess.PIPE,  # 捕获标准输出供基础设施诊断。
        stderr=asyncio.subprocess.PIPE,  # 捕获标准错误供基础设施诊断。
        start_new_session=os.name == "posix",  # Linux 下创建可整体终止的独立进程组。
    )  # 完成子进程创建。
    stdout_accumulator = OutputAccumulator(config.max_output_chars)  # 限制标准输出内存。
    stderr_accumulator = OutputAccumulator(config.max_output_chars)  # 限制错误输出内存。
    stdout_task = asyncio.create_task(_capture_stream(process.stdout, stdout_accumulator))  # 并发排空 stdout 防止管道阻塞。
    stderr_task = asyncio.create_task(_capture_stream(process.stderr, stderr_accumulator))  # 并发排空 stderr 防止管道阻塞。
    timed_out = False  # 初始化外层超时标记。
    try:  # 等待官方进程并应用整批硬超时。
        await asyncio.wait_for(process.wait(), timeout=config.process_timeout_seconds)  # 限制正式评测最长墙钟时间。
    except TimeoutError:  # 官方进程超过整批预算。
        timed_out = True  # 保存超时事实供稳定分类。
        await _terminate_process_group(process, config.termination_grace_seconds)  # 终止整个官方进程组。
    await asyncio.gather(stdout_task, stderr_task)  # 确保两个输出流都读到 EOF。
    stdout, stdout_truncated = stdout_accumulator.finish()  # 生成有界 stdout 和截断标志。
    stderr, stderr_truncated = stderr_accumulator.finish()  # 生成有界 stderr 和截断标志。
    duration = time.monotonic() - started_at  # 计算完整真实耗时。
    results_root = config.workdir / "logs" / "evaluation" / config.run_id  # 按官方默认布局定位本次结果目录。
    report: SweBenchHarnessReport | None = None  # 初始化可选解析报告。
    if results_root.exists():  # 即使进程非零也保留已经完成实例的官方结果。
        try:  # 结果缺失或格式异常由进程状态明确表达。
            report = parse_harness_results(results_root, run_id=config.run_id, expected_instance_ids=config.instance_ids)  # 解析逐实例结论。
        except (FileNotFoundError, ValueError):  # 不把解析异常伪装成 Agent unresolved。
            report = None  # 交给外层状态归类为基础设施问题。
    if timed_out:  # 外层硬超时优先级最高。
        status = HarnessProcessStatus.TIMED_OUT  # 明确归为基础设施超时。
    elif process.returncode != 0:  # 官方命令非零退出不是 Agent 能力失败。
        status = HarnessProcessStatus.PROCESS_FAILED  # 保留非零退出分类。
    elif report is None or report.missing > 0:  # 成功退出但结果不完整仍不能算完整评测。
        status = HarnessProcessStatus.RESULTS_MISSING  # 明确结果缺失。
    else:  # 进程成功且每个预期实例都有官方结论。
        status = HarnessProcessStatus.COMPLETED  # 标记完整评测完成。
    return SweBenchHarnessExecution(  # 构造不泄漏任何环境变量的最终执行结果。
        status=status,  # 保存进程级分类。
        command=command,  # 保存实际官方命令供复现。
        return_code=process.returncode,  # 保存真实退出码。
        timed_out=timed_out,  # 保存硬超时事实。
        duration_seconds=duration,  # 保存墙钟耗时。
        stdout=stdout,  # 保存有界标准输出。
        stderr=stderr,  # 保存有界错误输出。
        output_truncated=stdout_truncated or stderr_truncated,  # 合并两个流的截断标志。
        report=report,  # 附带可选官方结果。
    )  # 完成结构化返回。
