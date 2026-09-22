"""带来源、边、假设状态和原子快照的任务级 Evidence Graph。"""  # 图只保留可查询工作记忆，事件流仍是事实原件。

from __future__ import annotations  # 延迟解析图结构类型。

import json  # 以非可执行 JSON 保存图快照。
import os  # 使用原子替换避免写出半份图。
from dataclasses import asdict, dataclass, field  # 表示图节点、关系与可变容器。
from enum import StrEnum  # 定义稳定的图语义枚举。
from pathlib import Path  # 管理单次运行的快照路径。
from uuid import uuid4  # 为节点、边和临时文件生成唯一 ID。


class EvidenceKind(StrEnum):  # 限定图中可识别的证据对象类型。
    ISSUE_FACT = "issue_fact"  # 表示 Issue 原文中的事实或约束。
    FILE = "file"  # 表示已索引的仓库文件。
    SYMBOL = "symbol"  # 表示 AST 中的定义位置。
    TEST = "test"  # 表示一条公开测试命令或测试函数。
    FAILURE = "failure"  # 表示非零退出、超时或补丁拒绝。
    STACK_FRAME = "stack_frame"  # 表示错误栈中的文件与行号。
    HYPOTHESIS = "hypothesis"  # 表示模型提出的可检验根因判断。
    PLAN_STEP = "plan_step"  # 表示具体可验证的修复计划。
    PATCH_ATTEMPT = "patch_attempt"  # 表示一份候选补丁尝试。
    VERIFICATION = "verification_result"  # 表示候选对应的测试结果。
    DECISION = "decision"  # 表示反思或终止时的理由。


class EvidenceOrigin(StrEnum):  # 明确区分事实、确定性分析与模型推断。
    ISSUE = "issue"  # 来源为 TaskSpec 中的原始问题描述。
    DERIVED = "derived"  # 来源为 AST 或其他确定性代码分析。
    MODEL = "model"  # 来源为模型回复，不能冒充执行事实。
    RUNTIME = "runtime"  # 来源为 Runtime 命令或补丁结果。
    VERIFICATION = "verification"  # 来源为候选验证的实际测试结果。


class EvidenceRelation(StrEnum):  # 定义任务相关的关系类型。
    BELONGS_TO = "belongs_to"  # 符号或测试属于文件。
    DEFINES = "defines"  # 文件定义类或函数。
    IMPORTS = "imports"  # 一个文件导入另一个模块。
    CALLS = "calls"  # 静态分析发现调用名称。
    FAILS_AT = "fails_at"  # 失败关联到栈帧或测试。
    COVERS = "covers"  # 测试与目标代码关联。
    SUPPORTS = "supports"  # 证据支持某假设。
    REFUTES = "refutes"  # 验证证据反驳某假设。
    MODIFIES = "modifies"  # 补丁尝试修改文件。
    CAUSES = "causes"  # 某尝试产生验证结果。
    VERIFIES = "verifies"  # 测试结果验证某候选。
    ALTERNATIVE = "alternative"  # 后续方案替代前一方案。
    DERIVED_FROM = "derived_from"  # 推断或决定引用其依据。


class HypothesisStatus(StrEnum):  # 定义可检验假设的状态生命周期。
    PENDING = "pending"  # 尚无足够验证证据。
    PARTIALLY_SUPPORTED = "partially_supported"  # 有证据但尚未完成验证。
    CONFIRMED = "confirmed"  # 已获得直接验证支持。
    REFUTED = "refuted"  # 已获得直接验证反证。
    ABANDONED = "abandoned"  # 因预算或策略原因不再尝试。


@dataclass(slots=True)  # 节点状态可随着证据更新，但来源不可改写。
class EvidenceNode:  # 定义可追溯的单个事实、推断或尝试。
    node_id: str  # 稳定节点 ID，供事件和边引用。
    kind: EvidenceKind  # 保存受限节点类型。
    text: str  # 保存进入上下文的短文本而非完整命令输出。
    origin: EvidenceOrigin  # 标识 Issue、静态分析、模型或执行来源。
    source_ref: str  # 指向任务、索引版本或具体轨迹事件。
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)  # 保存紧凑机器可读字段。
    status: HypothesisStatus | None = None  # 只有假设节点使用状态字段。
    active: bool = True  # 逻辑失效不删除历史节点。


@dataclass(frozen=True, slots=True)  # 已记录的关系不允许被事后改写。
class EvidenceEdge:  # 定义两个证据节点之间的有向关系。
    edge_id: str  # 保存唯一边 ID。
    source_id: str  # 指向原因或所属方节点。
    target_id: str  # 指向结果或被支持的节点。
    relation: EvidenceRelation  # 限定关系语义。
    source_ref: str  # 保留创建该关系的轨迹事件或索引版本。


class EvidenceGraph:  # 管理一次运行内的结构化证据工作记忆。
    def __init__(self) -> None:  # 创建空图而不读取任何外部数据。
        self.nodes: dict[str, EvidenceNode] = {}  # 按 ID 保存节点以支持常数时间查询。
        self.edges: list[EvidenceEdge] = []  # 按建立顺序保存可审计关系。
        self.version = 0  # 每次有效变更递增图版本。

    def add_node(self, kind: EvidenceKind, text: str, origin: EvidenceOrigin, source_ref: str, *, metadata: dict[str, str | int | float | bool | None] | None = None) -> EvidenceNode:  # 创建有来源的新节点。
        if not text.strip() or len(text) > 2_000 or not source_ref.strip():  # 限制上下文文本并要求可追溯来源。
            raise ValueError("证据文本必须非空且不超过 2000 字符，来源不能为空")  # 拒绝无来源或无限长节点。
        if origin in {EvidenceOrigin.RUNTIME, EvidenceOrigin.VERIFICATION, EvidenceOrigin.MODEL} and not source_ref.startswith("event:"):  # 执行事实与模型推断必须指向事件。
            raise ValueError("运行时、验证和模型证据必须引用轨迹事件")  # 防止口头事实混入执行证据。
        if kind is EvidenceKind.HYPOTHESIS and origin is not EvidenceOrigin.MODEL:  # 根因假设必须保留模型推断身份。
            raise ValueError("根因假设只能来源于模型推断")  # 不允许自动把假设标成执行事实。
        node = EvidenceNode(uuid4().hex, kind, text, origin, source_ref, metadata or {}, HypothesisStatus.PENDING if kind is EvidenceKind.HYPOTHESIS else None)  # 构造来源不可变的节点。
        self.nodes[node.node_id] = node  # 将新节点加入任务图。
        self.version += 1  # 更新可引用的图版本。
        return node  # 返回节点供后续添加边。

    def add_edge(self, source_id: str, target_id: str, relation: EvidenceRelation, source_ref: str) -> EvidenceEdge:  # 在两个既有节点之间建立可追溯关系。
        if source_id not in self.nodes or target_id not in self.nodes or source_id == target_id:  # 防止悬空边和无意义自环。
            raise ValueError("证据边必须连接两个不同的既有节点")  # 拒绝关系不一致。
        if not source_ref.strip():  # 每条关系都需要明确来源。
            raise ValueError("证据边来源不能为空")  # 避免无法审计的推断关系。
        source = self.nodes[source_id]  # 读取关系源节点以检查候选身份。
        target = self.nodes[target_id]  # 读取关系目标节点以检查验证身份。
        if relation is EvidenceRelation.VERIFIES and (source.kind is not EvidenceKind.PATCH_ATTEMPT or target.kind is not EvidenceKind.VERIFICATION or source.metadata.get("candidate_id") != target.metadata.get("candidate_id")):  # 验证关系必须属于同一个候选。
            raise ValueError("验证结果必须关联同一候选的补丁尝试")  # 防止跨候选测试结果串线。
        if relation is EvidenceRelation.MODIFIES and (source.kind is not EvidenceKind.PATCH_ATTEMPT or target.kind is not EvidenceKind.FILE):  # 修改关系只能由补丁指向文件。
            raise ValueError("修改关系必须由补丁尝试指向文件")  # 防止图语义被错误使用。
        edge = EvidenceEdge(uuid4().hex, source_id, target_id, relation, source_ref)  # 生成稳定关系对象。
        self.edges.append(edge)  # 追加而不是覆盖旧关系。
        self.version += 1  # 记录一次图结构变更。
        return edge  # 返回关系以供事件引用。

    def update_hypothesis(self, node_id: str, status: HypothesisStatus, *, evidence_id: str | None = None) -> None:  # 更新可检验假设的证据状态。
        node = self.nodes.get(node_id)  # 读取指定假设节点。
        if node is None or node.kind is not EvidenceKind.HYPOTHESIS:  # 不允许修改普通事实的状态。
            raise ValueError("只能更新现有假设的状态")  # 防止事实和推断状态混淆。
        if node.status in {HypothesisStatus.REFUTED, HypothesisStatus.ABANDONED} and status not in {node.status, HypothesisStatus.ABANDONED}:  # 反驳后不能无新假设地恢复。
            raise ValueError("已反驳或已放弃假设不能恢复为主假设")  # 保留失败方案记忆。
        if status in {HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED}:  # 强状态必须由实际验证证据支撑。
            evidence = self.nodes.get(evidence_id or "")  # 查询调用方给出的验证节点。
            if evidence is None or evidence.origin is not EvidenceOrigin.VERIFICATION:  # 模型自述不能确认或反驳事实。
                raise ValueError("确认或反驳假设必须引用实际验证结果")  # 拒绝证据洗白。
            expected_passed = status is HypothesisStatus.CONFIRMED  # 确认要求通过，反驳要求失败。
            if evidence.metadata.get("passed") is not expected_passed:  # 验证方向必须与强状态一致。
                raise ValueError("验证结果方向与假设状态不一致")  # 防止失败测试被误用为确认依据。
            relation = EvidenceRelation.SUPPORTS if status is HypothesisStatus.CONFIRMED else EvidenceRelation.REFUTES  # 区分正向与反向验证。
            self.add_edge(evidence.node_id, node_id, relation, evidence.source_ref)  # 将验证结果直接连到假设。
        node.status = status  # 保存经过约束的假设新状态。
        self.version += 1  # 更新快照版本。

    def has_patch_digest(self, digest: str) -> bool:  # 查询是否已经尝试完全相同的补丁。
        return any(node.kind is EvidenceKind.PATCH_ATTEMPT and node.metadata.get("digest") == digest for node in self.nodes.values())  # 使用规范化摘要阻止重复修补。

    def latest(self, kind: EvidenceKind) -> EvidenceNode | None:  # 读取某类节点的最近一次记录。
        return next((node for node in reversed(tuple(self.nodes.values())) if node.kind is kind and node.active), None)  # 保留时间顺序并跳过失效节点。

    def validate(self) -> None:  # 在落盘前检查图的基本一致性。
        for node in self.nodes.values():  # 检查每个节点的身份与来源。
            if not node.source_ref or (node.kind is EvidenceKind.HYPOTHESIS) != (node.status is not None):  # 假设状态只能附着在假设上。
                raise ValueError("Evidence Graph 节点来源或假设状态不一致")  # 拒绝损坏图快照。
            if node.origin in {EvidenceOrigin.RUNTIME, EvidenceOrigin.VERIFICATION, EvidenceOrigin.MODEL} and not node.source_ref.startswith("event:"):  # 执行和推断记录必须有事件引用。
                raise ValueError("Evidence Graph 缺少事件来源")  # 防止来源降级。
        for edge in self.edges:  # 检查全部关系端点仍然存在。
            if edge.source_id not in self.nodes or edge.target_id not in self.nodes or not edge.source_ref:  # 发现悬空关系或无来源关系。
                raise ValueError("Evidence Graph 包含悬空或无来源关系")  # 阻止持久化不可解释图。

    def snapshot(self) -> dict[str, object]:  # 构造可安全 JSON 序列化的当前视图。
        self.validate()  # 快照必须先通过一致性检查。
        return {"schema_version": 1, "version": self.version, "nodes": [asdict(node) for node in self.nodes.values()], "edges": [asdict(edge) for edge in self.edges]}  # 保留节点、关系和版本。

    def save(self, path: Path) -> None:  # 原子保存单次运行的工作记忆快照。
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n"  # 先在内存中完整序列化 JSON。
        path.parent.mkdir(parents=True, exist_ok=True)  # 确保运行目录存在。
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")  # 在相同目录创建独立临时文件。
        try:  # 无论写盘结果如何都清理本次临时文件。
            temporary.write_text(payload, encoding="utf-8")  # 先写完整快照。
            os.replace(temporary, path)  # 原子发布新版本。
        finally:  # 即使原子替换失败也清理临时文件。
            temporary.unlink(missing_ok=True)  # 只删除当前调用产生的文件。
