"""第六周候选隔离、并发验证和选择规则测试。"""  # 说明本文件覆盖第六周主交付。

from __future__ import annotations  # 允许延迟解析类型标注。

import asyncio  # 在同步 pytest 测试中运行异步 Agent 组件。
import sys  # 读取当前测试解释器的绝对路径。
from pathlib import Path  # 标注测试目录路径。

from patchflow.candidates.branching import (  # 导入候选编排器。
    BranchingConfig,  # 导入候选数量与并发配置。
    CandidateBranchingEngine,  # 导入候选验证和选择引擎。
)  # 完成候选编排器导入。
from patchflow.runtime.local import LocalRuntime  # 使用真实本地隔离 Runtime。
from patchflow.verification.pyramid import (  # 检查验证层级并指定解释器。
    VerificationLevel,  # 检查失败发生的验证层级。
    VerificationPlan,  # 指定 V1 语法检查解释器。
)  # 完成验证依赖导入。
from tests.runtime_helpers import (  # 复用可信 Git 仓库和有效补丁。
    create_temporary_git_repository,  # 创建干净的临时 Git 仓库。
    valid_patch,  # 生成可以通过公开测试的候选补丁。
)  # 完成测试夹具导入。


def _invalid_syntax_patch() -> str:  # 构造可以应用但不能通过语法检查的候选。
    return valid_patch().replace(  # 只改变新增代码使补丁路径仍然有效。
        "+    cleaned_name = name.strip()  # 去除姓名两端空白。\n",  # 定位有效候选中的新增语句。
        "+    cleaned_name =  # 故意制造 Python 语法错误。\n",  # 保留补丁结构但让 V1 必须淘汰。
    )  # 返回语法错误候选。


def _runtime_factory(workspace: Path, isolation_root: Path) -> LocalRuntime:  # 为每个候选创建独立本地 Runtime。
    return LocalRuntime(workspace, isolation_root)  # Runtime 只指向当前候选目录。


def test_candidate_branches_are_isolated_and_valid_candidate_is_selected(tmp_path: Path) -> None:  # 验证失败候选不会污染通过候选。
    fixture = create_temporary_git_repository(tmp_path)  # 创建统一基础提交的源仓库。
    task = fixture.task.model_copy(update={"public_commands": (f"{sys.executable} -m pytest -q",)})  # 使用当前解释器避免依赖外部 PATH。
    branch_root = tmp_path / "candidate-branches"  # 创建候选工作区父目录。
    report_path = tmp_path / "reports" / "candidates.json"  # 指定可审计候选报告路径。
    engine = CandidateBranchingEngine(  # 使用两个候选和两个验证并发槽。
        config=BranchingConfig(max_candidates=2, max_concurrency=2),  # 允许两个候选并行执行。
    )  # 完成候选引擎装配。
    plan = VerificationPlan.from_task(task, python_executable=sys.executable)  # 使用当前解释器执行 V1 语法检查。
    results, selection = asyncio.run(  # 执行候选创建、验证和选择。
        engine.run(  # 调用第六周完整候选流程。
            task,  # 传递使用当前解释器的基础任务。
            (_invalid_syntax_patch(), valid_patch()),  # 传递一个语法错误候选和一个有效候选。
            isolation_root=branch_root,  # 限制所有候选目录在测试隔离根中。
            runtime_factory=_runtime_factory,  # 为每个候选创建独立 Runtime。
            plan=plan,  # 传递使用当前解释器的验证计划。
            report_path=report_path,  # 保存候选选择报告。
        )  # 完成候选引擎调用。
    )  # 完成异步测试执行。
    assert len(results) == 2  # 两个不同补丁都应获得独立结果。
    assert sum(item.passed for item in results) == 1, results[1].verification.steps[-1].result.stderr if results[1].verification and results[1].verification.steps[-1].result else results  # 只有有效候选通过确定性验证，并在失败时输出诊断。
    assert selection.selected_candidate_id is not None  # 必须选出通过验证的候选。
    selected = next(item for item in results if item.candidate_id == selection.selected_candidate_id)  # 找到最终选择结果。
    assert selected.passed  # 最终选择必须满足全部硬验证条件。
    rejected = next(item for item in results if not item.passed)  # 找到语法错误候选。
    assert rejected.verification is not None  # 语法失败应留下结构化验证报告。
    assert rejected.verification.failed_level is VerificationLevel.V1_SYNTAX  # 失败应在低成本语法层被淘汰。
    assert report_path.exists()  # 候选结果应写入可审计报告。
    assert not branch_root.exists() or not any(branch_root.iterdir())  # 默认配置应清理所有候选源码副本。


def test_duplicate_patches_are_deduplicated_before_workspace_creation(tmp_path: Path) -> None:  # 验证候选去重发生在创建工作区之前。
    fixture = create_temporary_git_repository(tmp_path)  # 创建统一基础提交的源仓库。
    task = fixture.task.model_copy(update={"public_commands": (f"{sys.executable} -m pytest -q",)})  # 使用当前解释器避免依赖外部 PATH。
    engine = CandidateBranchingEngine(config=BranchingConfig(max_candidates=4))  # 允许最多四个候选但输入只有一个唯一补丁。
    plan = VerificationPlan.from_task(task, python_executable=sys.executable)  # 使用当前解释器执行 V1 语法检查。
    results, selection = asyncio.run(  # 运行重复候选输入。
        engine.run(  # 调用候选引擎。
            task,  # 传递使用当前解释器的基础任务。
            (valid_patch(), valid_patch()),  # 传递完全相同的两个补丁文本。
            isolation_root=tmp_path / "duplicate-branches",  # 指定候选隔离根。
            runtime_factory=_runtime_factory,  # 使用真实本地 Runtime。
            plan=plan,  # 传递使用当前解释器的验证计划。
        )  # 完成候选引擎调用。
    )  # 完成异步执行。
    assert len(results) == 1  # 相同规范化补丁只能创建一个候选。
    assert selection.selected_candidate_id == results[0].candidate_id, results  # 唯一候选通过后应被选中。


def test_no_candidate_is_selected_when_every_candidate_fails(tmp_path: Path) -> None:  # 验证不能把最不差候选伪装成成功。
    fixture = create_temporary_git_repository(tmp_path)  # 创建统一基础提交的源仓库。
    task = fixture.task.model_copy(update={"public_commands": (f"{sys.executable} -m pytest -q",)})  # 使用当前解释器避免依赖外部 PATH。
    engine = CandidateBranchingEngine(config=BranchingConfig(max_candidates=2))  # 配置有限候选宽度。
    plan = VerificationPlan.from_task(task, python_executable=sys.executable)  # 使用当前解释器执行 V1 语法检查。
    results, selection = asyncio.run(  # 运行全部失败候选。
        engine.run(  # 调用候选引擎。
            task,  # 传递使用当前解释器的基础任务。
            (_invalid_syntax_patch(),),  # 只传递一个语法错误候选。
            isolation_root=tmp_path / "failed-branches",  # 指定候选隔离根。
            runtime_factory=_runtime_factory,  # 使用真实本地 Runtime。
            plan=plan,  # 传递使用当前解释器的验证计划。
        )  # 完成候选引擎调用。
    )  # 完成异步执行。
    assert len(results) == 1  # 保留失败候选报告用于分析。
    assert selection.selected_candidate_id is None  # 没有通过验证的候选不能被选择。
    assert selection.reason == "no_candidate_passed_required_verification"  # 记录明确的无候选原因。
