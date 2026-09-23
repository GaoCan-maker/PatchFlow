"""第六周候选分支在 Docker Runtime 上的显式集成测试。"""  # 说明本测试默认不启动 Docker。

from __future__ import annotations  # 允许延迟解析类型标注。

import asyncio  # 在同步 pytest 测试中运行候选引擎。
import os  # 读取显式 Docker 测试开关。
from pathlib import Path  # 标注测试目录路径。

import pytest  # 使用 pytest 跳过默认重型集成测试。

from patchflow.candidates.branching import (  # 导入候选并发引擎。
    BranchingConfig,  # 导入候选数量和并发配置。
    CandidateBranchingEngine,  # 导入候选验证和选择引擎。
)  # 完成候选引擎导入。
from patchflow.runtime.docker import DockerRuntime  # 使用真实 Docker 候选 Runtime。
from tests.runtime_helpers import (  # 复用临时 Git 仓库和有效补丁。
    create_temporary_git_repository,  # 创建干净的临时 Git 仓库。
    valid_patch,  # 生成可以通过公开测试的补丁。
)  # 完成测试夹具导入。


def _invalid_syntax_patch() -> str:  # 构造一个能应用但会被 V1 淘汰的候选。
    return valid_patch().replace(  # 保留文件路径和补丁上下文不变。
        "+    cleaned_name = name.strip()  # 去除姓名两端空白。\n",  # 找到有效候选的新增语句。
        "+    cleaned_name =  # 制造语法错误。\n",  # 让 Docker 候选在低成本层失败。
    )  # 返回错误候选补丁。


def _docker_factory(workspace: Path, _isolation_root: Path) -> DockerRuntime:  # 为每个候选创建独立 Docker Runtime。
    return DockerRuntime(workspace)  # 将当前候选副本作为只读输入挂载到新容器。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 默认跳过重型容器测试。
def test_docker_candidate_branches_are_isolated(tmp_path: Path) -> None:  # 验证两个候选可在独立容器中并发验证。
    fixture = create_temporary_git_repository(tmp_path)  # 创建统一基础提交的源仓库。
    engine = CandidateBranchingEngine(  # 配置两个候选和两个容器并发槽。
        config=BranchingConfig(max_candidates=2, max_concurrency=2),  # 允许候选级并发但限制宽度。
    )  # 完成候选引擎装配。
    results, selection = asyncio.run(  # 在独立 Docker 容器中运行候选验证。
        engine.run(  # 调用第六周完整候选流程。
            fixture.task,  # 使用公开 pytest 任务定义。
            (_invalid_syntax_patch(), valid_patch()),  # 提供一个语法失败候选和一个可通过候选。
            isolation_root=tmp_path / "docker-candidate-branches",  # 限制候选副本的宿主目录。
            runtime_factory=_docker_factory,  # 为每个候选建立单独容器。
        )  # 完成候选引擎调用。
    )  # 完成异步 Docker 测试。
    assert len(results) == 2  # 两个不同补丁都应产生候选报告。
    assert sum(item.passed for item in results) == 1  # 只有有效补丁能通过 Docker 验证。
    assert selection.selected_candidate_id is not None  # 必须选择通过验证的候选。
