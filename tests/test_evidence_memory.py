"""第五周 Evidence Graph 溯源、一致性与分区上下文测试。"""  # 完全离线，不访问仓库或模型服务。

from __future__ import annotations  # 延迟解析临时目录类型。

import json  # 验证落盘图快照和模型上下文。
from pathlib import Path  # 接收 pytest 临时目录。

import pytest  # 检查强状态与图一致性拒绝。

from patchflow.domain.enums import AgentPhase  # 构造计划阶段上下文。
from patchflow.memory.context import EvidenceContextBuilder  # 测试分区预算选择。
from patchflow.memory.evidence import (  # 导入证据图节点、关系和状态类型。
    EvidenceGraph,  # 创建独立任务图。
    EvidenceKind,  # 声明不同节点类别。
    EvidenceOrigin,  # 区分执行事实与模型推断。
    EvidenceRelation,  # 构造可追溯证据关系。
    HypothesisStatus,  # 验证假设状态迁移。
)  # 完成证据图导入。


def test_graph_requires_verification_to_refute_and_saves_snapshot(tmp_path: Path) -> None:  # 验证强状态和原子 JSON 快照。
    graph = EvidenceGraph()  # 创建空任务图。
    issue = graph.add_node(EvidenceKind.ISSUE_FACT, "greet 应处理姓名空白", EvidenceOrigin.ISSUE, "task:demo")  # 保存任务来源事实。
    hypothesis = graph.add_node(EvidenceKind.HYPOTHESIS, "问题可能在 greet 输入规范化", EvidenceOrigin.MODEL, "event:model-plan")  # 保存模型根因推断。
    graph.add_edge(hypothesis.node_id, issue.node_id, EvidenceRelation.DERIVED_FROM, "event:model-plan")  # 连接假设和 Issue 来源。
    with pytest.raises(ValueError, match="验证结果"):  # 模型推断不能自行变成反证。
        graph.update_hypothesis(hypothesis.node_id, HypothesisStatus.REFUTED, evidence_id=issue.node_id)  # 用 Issue 假冒执行验证。
    verification = graph.add_node(EvidenceKind.VERIFICATION, "公开测试失败", EvidenceOrigin.VERIFICATION, "event:test-failed", metadata={"passed": False})  # 保存真实候选验证事实。
    with pytest.raises(ValueError, match="方向"):  # 失败结果不能被错误地解释为确认。
        graph.update_hypothesis(hypothesis.node_id, HypothesisStatus.CONFIRMED, evidence_id=verification.node_id)  # 尝试用失败测试确认根因。
    graph.update_hypothesis(hypothesis.node_id, HypothesisStatus.REFUTED, evidence_id=verification.node_id)  # 用真实测试失败反驳假设。
    assert hypothesis.status is HypothesisStatus.REFUTED  # 确认状态已被验证事实更新。
    assert any(edge.relation is EvidenceRelation.REFUTES and edge.target_id == hypothesis.node_id for edge in graph.edges)  # 验证反证边指向原假设。
    with pytest.raises(ValueError, match="不能恢复"):  # 已反驳方案不能直接回到主假设。
        graph.update_hypothesis(hypothesis.node_id, HypothesisStatus.PENDING)  # 尝试偷偷恢复失败方案。
    with pytest.raises(ValueError, match="两个不同"):  # 图不允许悬空或自环关系。
        graph.add_edge(issue.node_id, issue.node_id, EvidenceRelation.SUPPORTS, "event:bad")  # 请求无意义自环。
    target = tmp_path / "graph.json"  # 指定临时图快照位置。
    graph.save(target)  # 原子写出图节点与关系。
    raw = json.loads(target.read_text(encoding="utf-8"))  # 用标准 JSON 解析磁盘结果。
    assert raw["version"] == graph.version and len(raw["nodes"]) == 3  # 版本与节点数应一致。
    assert raw["nodes"][1]["origin"] == "model"  # 模型假设不能被落盘过程误标成事实。


def test_context_keeps_failure_refutation_and_modified_files_under_budget() -> None:  # 验证压缩后关键状态不丢失。
    graph = EvidenceGraph()  # 创建测试专属证据图。
    graph.add_node(EvidenceKind.ISSUE_FACT, "修复 greet 空白输入", EvidenceOrigin.ISSUE, "task:demo")  # 提供任务固定事实。
    old = graph.add_node(EvidenceKind.HYPOTHESIS, "只改测试断言就能修复行为", EvidenceOrigin.MODEL, "event:plan-old")  # 创建应被阻止重复采用的旧方案。
    proof = graph.add_node(EvidenceKind.VERIFICATION, "测试证明行为仍然错误", EvidenceOrigin.VERIFICATION, "event:verify-old", metadata={"passed": False})  # 提供真实反证。
    graph.update_hypothesis(old.node_id, HypothesisStatus.REFUTED, evidence_id=proof.node_id)  # 把旧假设标记为反驳。
    graph.add_node(EvidenceKind.FAILURE, "AssertionError: greet 输出仍含空白", EvidenceOrigin.RUNTIME, "event:test-failure")  # 保存当前失败。
    current = graph.add_node(EvidenceKind.HYPOTHESIS, "greet 需要规范化参数", EvidenceOrigin.MODEL, "event:plan-new")  # 创建新的主假设。
    for number in range(35):  # 构造大量低优先级代码证据以触发压缩。
        graph.add_node(EvidenceKind.SYMBOL, f"无关符号 {number} " + "x" * 150, EvidenceOrigin.DERIVED, "index:demo")  # 每个节点都带可追溯索引来源。
    projection = EvidenceContextBuilder(max_bytes=3_000).build(issue="修复 greet 空白输入", phase=AgentPhase.PLAN, instruction="返回结构化方案", graph=graph, modified_files=("app.py",))  # 构造有界模型上下文。
    payload = json.loads(projection.messages[-1].content)["state"]  # 读取真正进入请求的分区数据。
    assert projection.estimated_bytes <= 3_000  # 请求整体不得越过配置上限。
    assert payload["current_failure"]["kind"] == "failure"  # 最近失败必须保留。
    assert payload["main_hypothesis"]["id"] == current.node_id  # 新假设是唯一当前主假设。
    assert payload["blocked_hypotheses"][0]["id"] == old.node_id  # 已反驳方案必须留在阻止重复的分区。
    assert payload["modified_files"] == ["app.py"]  # 当前修改文件不能因压缩丢失。
    assert projection.dropped_node_ids  # 大量可选证据应记录被丢弃节点。
    assert old.node_id in projection.selected_node_ids  # 核心反证方案被标记为已选中。


def test_graph_rejects_model_claiming_runtime_provenance() -> None:  # 验证模型推断不能伪装为执行事实。
    graph = EvidenceGraph()  # 创建独立证据图。
    with pytest.raises(ValueError, match="轨迹事件"):  # 运行时事实必须有真实事件引用。
        graph.add_node(EvidenceKind.FAILURE, "模型说测试失败", EvidenceOrigin.RUNTIME, "model:guess")  # 尝试伪造 Runtime 来源。
    with pytest.raises(ValueError, match="模型推断"):  # 假设节点必须保留模型推断身份。
        graph.add_node(EvidenceKind.HYPOTHESIS, "未经验证的根因", EvidenceOrigin.RUNTIME, "event:fake")  # 尝试直接把根因设为事实。


def test_graph_rejects_cross_candidate_verification() -> None:  # 验证不同候选的测试事实不能互相背书。
    graph = EvidenceGraph()  # 创建独立任务图。
    attempt = graph.add_node(EvidenceKind.PATCH_ATTEMPT, "候选 A", EvidenceOrigin.MODEL, "event:patch-a", metadata={"candidate_id": "candidate-a"})  # 保存第一个候选身份。
    other_result = graph.add_node(EvidenceKind.VERIFICATION, "候选 B 测试通过", EvidenceOrigin.VERIFICATION, "event:test-b", metadata={"candidate_id": "candidate-b", "passed": True})  # 保存另一个候选的验证结果。
    with pytest.raises(ValueError, match="同一候选"):  # 不允许跨候选关联验证事实。
        graph.add_edge(attempt.node_id, other_result.node_id, EvidenceRelation.VERIFIES, "event:test-b")  # 尝试错误连线。
