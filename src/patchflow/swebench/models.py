"""SWE-bench 数据转换、隔离校验与预测文件模型。"""  # 集中定义第七周不依赖官方重型包的纯数据层。

from __future__ import annotations  # 延迟解析类型标注以支持现代容器类型。

import json  # 解析数据集中的 JSON 字符串列表并写出标准文件。
import os  # 使用原子替换避免中途失败留下半个评测文件。
import tempfile  # 在目标目录创建同文件系统临时文件。
from collections.abc import Iterable, Mapping, Sequence  # 标注批量记录和递归安全检查输入。
from pathlib import Path  # 表示任务清单与 prediction 的输出路径。
from typing import Any  # 接收第三方数据集尚未结构化的字段值。

from pydantic import BaseModel, ConfigDict, Field, field_validator  # 构造拒绝模糊输入的边界模型。

from patchflow.domain.enums import RepositoryKind, TaskSource  # 复用统一任务来源和仓库类型。
from patchflow.domain.task import RepositorySpec, TaskSpec  # 将 benchmark 样本转换为既有领域任务。

_FORBIDDEN_INFERENCE_KEYS = frozenset(  # 声明任何 Agent 输入中都不能出现的评测答案字段。
    {  # 开始不可泄漏字段集合。
        "patch",  # 官方 gold patch 会直接泄漏参考修复。
        "gold_patch",  # 兼容其他导出工具使用的显式金标名称。
        "test_patch",  # 官方测试补丁包含隐藏断言和目标行为。
        "fail_to_pass",  # 目标失败测试列表会向 Agent 泄漏评测答案。
        "pass_to_pass",  # 回归测试列表同样只能由 Evaluation Harness 使用。
    }  # 结束不可泄漏字段集合。
)  # 冻结集合防止运行时被意外修改。


def _test_names(value: Any) -> tuple[str, ...]:  # 统一解析 Hugging Face 中可能是 JSON 字符串或数组的测试名字段。
    if value is None:  # 某些非正式样本可能没有测试分类。
        return ()  # 使用空元组保持不可变语义。
    parsed = json.loads(value) if isinstance(value, str) else value  # 官方导出常把列表编码成 JSON 字符串。
    if not isinstance(parsed, (list, tuple)):  # 测试集合必须保持有序序列结构。
        raise ValueError("SWE-bench 测试列表必须是 JSON 数组或字符串数组")  # 拒绝逗号拼接等歧义格式。
    names = tuple(str(item).strip() for item in parsed)  # 把每个测试标识规范化为去空白字符串。
    if any(not item for item in names):  # 空测试名无法交给官方 Harness 解释。
        raise ValueError("SWE-bench 测试列表不能包含空值")  # 在适配边界尽早失败。
    return names  # 返回稳定且不可变的测试名集合。


class SweBenchRecord(BaseModel):  # 只建模转换所需字段并丢弃数据集中的其他元数据。
    """官方数据集单条记录的受控视图。"""  # 明确该对象同时含推理字段和评测私有字段。

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)  # 忽略版本差异字段但禁止实例被修改。
    instance_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")  # 使用官方实例 ID 作为稳定主键。
    repo: str = Field(min_length=1)  # 保存 GitHub 的 owner/repository 标识。
    base_commit: str = Field(min_length=1)  # 保存修复前必须检出的提交。
    problem_statement: str = Field(min_length=10)  # 保存 Agent 唯一可见的问题描述。
    version: str | None = None  # 保留可用于环境诊断的项目版本但不把它当成答案。
    environment_setup_commit: str | None = None  # 保留官方环境提交供评测侧追溯。
    patch: str = Field(default="", description="仅评测侧可见的官方 gold patch")  # 保存金补丁但禁止进入 TaskSpec。
    test_patch: str = Field(default="", description="仅评测侧可见的官方测试补丁")  # 保存隐藏测试变更但禁止进入 TaskSpec。
    fail_to_pass: tuple[str, ...] = Field(default=(), alias="FAIL_TO_PASS")  # 保存官方目标测试集合。
    pass_to_pass: tuple[str, ...] = Field(default=(), alias="PASS_TO_PASS")  # 保存官方回归测试集合。

    @field_validator("repo", "base_commit", "problem_statement")  # 对关键文本执行相同空白规范化。
    @classmethod  # 告诉 Pydantic 校验器不依赖具体实例。
    def normalize_required_text(cls, value: str) -> str:  # 剔除容易导致提交或仓库定位失败的首尾空白。
        cleaned = value.strip()  # 生成规范化文本。
        if not cleaned:  # 防止只包含空白的值绕过最小长度约束。
            raise ValueError("SWE-bench 必填文本字段不能为空")  # 返回明确的数据错误。
        return cleaned  # 保存规范化后的字段值。

    @field_validator("fail_to_pass", "pass_to_pass", mode="before")  # 在 Pydantic 转换 tuple 之前兼容官方字符串格式。
    @classmethod  # 校验逻辑不访问实例状态。
    def parse_test_names(cls, value: Any) -> tuple[str, ...]:  # 将两类测试标识统一为元组。
        return _test_names(value)  # 委托共享解析器并保留一致错误信息。


class SweBenchEvaluationRecord(BaseModel):  # 将答案信息保存在与 Agent 任务不同的模型中。
    """只能存放在 Evaluation Phase 的私有记录。"""  # 提醒调用者不要将该模型传给 Agent。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 拒绝未知字段并防止评测答案被篡改。
    instance_id: str  # 关联官方实例 ID。
    gold_patch: str  # 保存官方参考补丁用于环境验证而非 Agent 推理。
    test_patch: str  # 保存官方测试补丁用于隔离审计。
    fail_to_pass: tuple[str, ...]  # 保存修复后必须由失败转为通过的测试。
    pass_to_pass: tuple[str, ...]  # 保存修复后仍必须通过的回归测试。
    version: str | None = None  # 保存环境版本用于排查基础设施问题。
    environment_setup_commit: str | None = None  # 保存环境构建提交用于结果复现。


class InferenceBundle(BaseModel):  # 定义可直接交给 PatchFlow Agent 的安全任务清单。
    """不含任何 gold 或隐藏测试信息的推理输入。"""  # 说明该模型的安全承诺。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 防止调用方悄悄附加私有字段。
    schema_version: int = Field(default=1, ge=1)  # 为后续格式迁移保留显式版本。
    dataset_name: str = Field(min_length=1)  # 记录数据集来源以便实验追溯。
    split: str = Field(min_length=1)  # 记录 train、dev 或 test 分片。
    tasks: tuple[TaskSpec, ...]  # 保存严格校验后的公开 TaskSpec。

    @field_validator("tasks")  # 验证任务集合非空且实例 ID 唯一。
    @classmethod  # 集合校验无需读取其他实例字段。
    def validate_tasks(cls, tasks: tuple[TaskSpec, ...]) -> tuple[TaskSpec, ...]:  # 强制 benchmark 批次具有稳定分母。
        if not tasks:  # 空 bundle 无法执行或评测。
            raise ValueError("SWE-bench 推理任务不能为空")  # 尽早报告错误。
        identifiers = tuple(task.task_id for task in tasks)  # 提取任务主键。
        if len(set(identifiers)) != len(identifiers):  # 检查同一实例是否被重复写入。
            raise ValueError("SWE-bench 推理任务包含重复 instance_id")  # 防止预测覆盖和错误分母。
        if any(task.source is not TaskSource.SWE_BENCH for task in tasks):  # 检查任务来源不会混入本地数据。
            raise ValueError("InferenceBundle 只接受 SWE-bench TaskSpec")  # 保持适配器边界单一。
        return tasks  # 返回经过验证的原始顺序。


class SweBenchPrediction(BaseModel):  # 精确对应官方 predictions JSONL 的三个字段。
    """官方 Evaluation Harness 接受的单条预测。"""  # 不允许附带轨迹或评测答案。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 防止隐藏字段被写入官方提交文件。
    instance_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")  # 关联唯一 benchmark 实例。
    model_name_or_path: str = Field(min_length=1)  # 保存模型与方法标识用于官方报告命名。
    model_patch: str = Field(min_length=1)  # 保存 Agent 相对基础提交导出的最终 diff。

    @field_validator("model_name_or_path", "model_patch")  # 对两个自由文本字段执行非空和控制字符检查。
    @classmethod  # 校验器不依赖实例状态。
    def validate_non_blank_text(cls, value: str) -> str:  # 保留 patch 原始换行但拒绝无效文本。
        if not value.strip():  # 空白模型名或补丁没有评测意义。
            raise ValueError("prediction 文本字段不能为空")  # 给出稳定输入错误。
        if "\x00" in value:  # NUL 字节不应进入 JSONL 或 git apply。
            raise ValueError("prediction 文本字段不能包含 NUL 字节")  # 阻止损坏评测文件。
        return value  # 原样返回以保持补丁字节语义。


def adapt_record_to_task(  # 将一条官方记录转换为不含答案信息的领域任务。
    record: SweBenchRecord,  # 接收已经严格解析的 benchmark 记录。
    *,  # 强制仓库和数据集信息使用具名参数避免顺序错误。
    dataset_name: str,  # 记录真实数据集名称。
    split: str,  # 记录真实数据集分片。
    repository_location: str | None = None,  # 允许调用方传入已准备好的本地仓库或镜像位置。
    repository_kind: RepositoryKind | None = None,  # 允许调用方明确仓库定位方式。
) -> TaskSpec:  # 返回现有 Agent 能消费的统一任务。
    location = repository_location or f"https://github.com/{record.repo}.git"  # 缺省时使用可追溯官方仓库地址。
    kind = repository_kind or (RepositoryKind.LOCAL if repository_location else RepositoryKind.GIT)  # 本地映射默认视为本地仓库。
    task = TaskSpec(  # 构造只包含推理阶段允许字段的任务。
        task_id=record.instance_id,  # 保持官方主键以便预测回连。
        source=TaskSource.SWE_BENCH,  # 标记任务来源用于导出边界检查。
        repo_spec=RepositorySpec(kind=kind, location=location),  # 保存基础仓库位置但不准备环境。
        base_commit=record.base_commit,  # 要求 Agent 从官方基础提交开始。
        problem_statement=record.problem_statement,  # 只公开 Issue 描述。
        language="python",  # 当前项目和 SWE-bench 首版都以 Python 仓库为目标。
        public_commands=(),  # 不把 FAIL_TO_PASS 或隐藏测试命令伪装成公开测试。
        tags=frozenset({"swe-bench", record.repo.lower()}),  # 保存可用于分层统计的公开标签。
        evaluation_ref=f"swebench://{dataset_name}/{split}/{record.instance_id}",  # 只保存不含答案的不可执行引用。
    )  # 完成安全 TaskSpec 构造。
    assert_inference_payload_safe(task.model_dump(mode="json"))  # 对最终公开对象执行递归泄漏检查。
    return task  # 返回可交给 Agent Harness 的任务。


def extract_evaluation_record(record: SweBenchRecord) -> SweBenchEvaluationRecord:  # 显式提取只能由评测侧保管的信息。
    return SweBenchEvaluationRecord(  # 构造与 TaskSpec 完全分离的私有记录。
        instance_id=record.instance_id,  # 保存实例关联键。
        gold_patch=record.patch,  # 将官方 patch 改名为更醒目的 gold_patch。
        test_patch=record.test_patch,  # 保存隐藏测试变更。
        fail_to_pass=record.fail_to_pass,  # 保存目标测试答案。
        pass_to_pass=record.pass_to_pass,  # 保存回归测试答案。
        version=record.version,  # 保存公开环境版本供诊断。
        environment_setup_commit=record.environment_setup_commit,  # 保存环境构建依据。
    )  # 完成私有评测记录构造。


def assert_inference_payload_safe(payload: Any, *, path: str = "$") -> None:  # 递归检查任意待写入 Agent 输入的结构。
    if isinstance(payload, Mapping):  # 字典键是答案字段泄漏的主要入口。
        for key, value in payload.items():  # 深度优先遍历每个字段。
            normalized = str(key).strip().lower()  # 大小写无关地比较官方字段名。
            child_path = f"{path}.{key}"  # 构造可定位泄漏位置的路径。
            if normalized in _FORBIDDEN_INFERENCE_KEYS:  # 检查当前键是否属于评测私有字段。
                raise ValueError(f"推理输入包含评测私有字段：{child_path}")  # 立即拒绝而不是静默删除。
            assert_inference_payload_safe(value, path=child_path)  # 继续检查嵌套对象。
        return  # 当前映射检查完成后结束分支。
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):  # 列表中也可能嵌入答案对象。
        for index, value in enumerate(payload):  # 按稳定下标遍历序列。
            assert_inference_payload_safe(value, path=f"{path}[{index}]")  # 递归检查每个元素并保留下标。


def _atomic_write(path: Path, content: str) -> None:  # 用同目录临时文件原子写出关键评测 artifact。
    path.parent.mkdir(parents=True, exist_ok=True)  # 确保输出父目录存在。
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)  # 创建不会冲突的临时文件。
    temporary_path = Path(temporary_name)  # 将底层名称转换为 Path 方便清理。
    try:  # 确保写入或替换失败时删除临时文件。
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:  # 以固定 UTF-8 和 LF 写入。
            handle.write(content)  # 一次写入已经完整构造的内容。
            handle.flush()  # 把 Python 缓冲推送给操作系统。
            os.fsync(handle.fileno())  # 尽可能保证替换前数据已经落盘。
        os.replace(temporary_path, path)  # 在同一文件系统原子替换目标文件。
    finally:  # 无论成功与否都处理残留临时文件。
        temporary_path.unlink(missing_ok=True)  # 成功替换后路径已不存在，失败时负责清理。


def write_inference_bundle(bundle: InferenceBundle, path: Path) -> None:  # 保存可审计且经过隔离检查的 Agent 输入。
    payload = bundle.model_dump(mode="json")  # 只序列化模型声明的公开字段。
    assert_inference_payload_safe(payload)  # 写盘前再次阻断任何嵌套答案字段。
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")  # 输出稳定可读 JSON。


def write_task_specs(tasks: Iterable[TaskSpec], path: Path) -> None:  # 保存现有 PatchFlow CLI 可直接读取的 TaskSpec 数组。
    materialized = tuple(tasks)  # 固定任务集合以便完成全局唯一性检查。
    InferenceBundle(dataset_name="validation", split="validation", tasks=materialized)  # 复用 bundle 对来源、空集合和重复 ID 的校验。
    payload = [task.model_dump(mode="json") for task in materialized]  # 只导出 TaskSpec 声明的公开字段。
    assert_inference_payload_safe(payload)  # 写盘前递归阻断 gold 和隐藏测试字段。
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")  # 原子写出兼容现有 CLI 的 JSON 数组。


def write_private_evaluation_records(records: Iterable[SweBenchEvaluationRecord], path: Path) -> None:  # 将答案信息写入显式私有文件。
    materialized = tuple(records)  # 先完整收集以检查重复实例。
    identifiers = tuple(item.instance_id for item in materialized)  # 提取所有实例 ID。
    if len(set(identifiers)) != len(identifiers):  # 防止私有记录互相覆盖。
        raise ValueError("评测私有记录包含重复 instance_id")  # 对错误数据集立即失败。
    payload = [item.model_dump(mode="json") for item in materialized]  # 序列化明确命名的私有模型。
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")  # 原子保存评测侧文件。


def prediction_from_task(task: TaskSpec, *, model_name_or_path: str, model_patch: str) -> SweBenchPrediction:  # 从 Agent 输出构造官方预测。
    if task.source is not TaskSource.SWE_BENCH:  # 本地任务 ID 不能误提交到官方 Harness。
        raise ValueError("只有 SWE-bench TaskSpec 可以导出官方 prediction")  # 保护评测分母和实例对应关系。
    return SweBenchPrediction(instance_id=task.task_id, model_name_or_path=model_name_or_path, model_patch=model_patch)  # 仅复制官方允许的三个字段。


def write_predictions(predictions: Iterable[SweBenchPrediction], path: Path) -> None:  # 导出官方 Harness 可读取的 JSONL。
    materialized = tuple(predictions)  # 固定迭代器内容以便先完成全局校验。
    if not materialized:  # 空预测文件会导致含义模糊的官方运行。
        raise ValueError("prediction 集合不能为空")  # 要求调用方明确处理 Agent 无补丁情况。
    identifiers = tuple(item.instance_id for item in materialized)  # 提取实例主键检查唯一性。
    if len(set(identifiers)) != len(identifiers):  # 同一实例不能有两个最终补丁。
        raise ValueError("prediction 集合包含重复 instance_id")  # 防止官方工具采用不明确的最后一条。
    lines = tuple(item.model_dump_json() for item in materialized)  # Pydantic 保证每行只有三个官方字段。
    _atomic_write(path, "\n".join(lines) + "\n")  # 使用 JSON Lines 格式原子写盘。


def read_predictions(path: Path) -> tuple[SweBenchPrediction, ...]:  # 加载并严格校验即将提交的官方预测文件。
    predictions: list[SweBenchPrediction] = []  # 按文件顺序收集预测。
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):  # 逐行解析以报告准确位置。
        if not line.strip():  # 空行不应偷偷改变评测数量。
            raise ValueError(f"prediction 文件第 {line_number} 行为空")  # 要求文件格式完全明确。
        try:  # 将 JSON 或字段错误补充行号上下文。
            predictions.append(SweBenchPrediction.model_validate_json(line))  # 严格拒绝多余字段和空补丁。
        except ValueError as error:  # 捕获 Pydantic 与 JSON 解析错误。
            raise ValueError(f"prediction 文件第 {line_number} 行无效：{error}") from error  # 保留原始异常链。
    materialized = tuple(predictions)  # 将结果冻结为不可变元组。
    if not materialized:  # 空文件不能作为正式评测输入。
        raise ValueError("prediction 文件不能为空")  # 给出直接错误。
    identifiers = tuple(item.instance_id for item in materialized)  # 提取实例 ID。
    if len(set(identifiers)) != len(identifiers):  # 检查跨行重复。
        raise ValueError("prediction 文件包含重复 instance_id")  # 拒绝不确定输入。
    return materialized  # 返回校验后的预测集合。
