"""显式开启的第五周主策略真实 Docker 集成测试。"""  # FakeModel 保证不会调用任何付费 API。

from __future__ import annotations  # 延迟解析临时目录类型。

import asyncio  # 驱动异步主策略与 DockerRuntime。
import json  # 读取最终证据图快照。
import os  # 复用现有 Docker 集成测试开关。
from pathlib import Path  # 接收 pytest 隔离目录。

import pytest  # 默认跳过需 Docker daemon 的集成验收。

from patchflow.agent.patchflow import PatchFlowAgent  # 运行第五周状态机。
from patchflow.application.run_initializer import initialize_run  # 创建正式运行工件。
from patchflow.config.models import (  # 配置单候选 Docker 策略。
    AgentConfig,  # 声明第五周主策略。
    AppConfig,  # 构造运行上下文快照。
    RuntimeConfig,  # 选择 Docker 后端。
)  # 完成测试配置导入。
from patchflow.domain.enums import RunStatus  # 验证公开测试通过的终态。
from patchflow.model.fake import FakeModel  # 使用不会联网的脚本模型。
from patchflow.model.protocol import ModelResponse  # 构造确定性阶段回复。
from patchflow.runtime.docker import DockerRuntime  # 使用真实禁网容器后端。
from tests.runtime_helpers import (  # 创建可信测试仓库和补丁。
    create_temporary_git_repository,  # 生成干净 Git 基础提交。
    valid_patch,  # 提供可应用的规范化补丁。
)  # 完成测试夹具导入。


@pytest.mark.skipif(os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1", reason="需要显式开启 Docker 集成测试")  # 普通单测不依赖容器服务。
def test_main_strategy_works_in_real_docker_without_touching_source(tmp_path: Path) -> None:  # 验证模型策略、Runtime 和图快照端到端。
    fixture = create_temporary_git_repository(tmp_path)  # 创建干净的基础提交仓库。
    original = (fixture.repository / "app.py").read_text(encoding="utf-8")  # 保存宿主源文件原文。
    config = AppConfig(agent=AgentConfig(strategy="patchflow"), runtime=RuntimeConfig(kind="docker"))  # 固定正式容器隔离配置。
    context = initialize_run(fixture.task, config, runs_root=tmp_path / "runs")  # 建立运行轨迹与清单。
    replies = (ModelResponse(final_answer=json.dumps({"summary": "修复 greet 处理带空白姓名", "expected": "姓名两端空白应被移除", "actual": "公开测试尚未覆盖带空白姓名", "constraints": ["保留原输出"], "unknowns": ["隐藏边界"], "reproduction_plan": "运行公开 pytest"}, ensure_ascii=False)), ModelResponse(final_answer=json.dumps({"hypothesis": "greet 没有规范化输入姓名", "target_file": "app.py", "target_symbol": "greet", "expected_change": "去掉姓名两端空白后生成问候字符串", "regression_risk": "正常姓名输出可能变化", "verification": "运行公开 pytest 回归", "abandon_if": "测试仍失败"}, ensure_ascii=False)), ModelResponse(final_answer=json.dumps({"patch": valid_patch()}, ensure_ascii=False)))  # 脚本模型依次提供理解、计划和补丁。
    runtime = DockerRuntime(fixture.repository)  # 源仓库只读挂载，候选位于容器私有工作区。
    outcome = asyncio.run(PatchFlowAgent(FakeModel(replies), config=config.agent).run(fixture.task, context, runtime))  # 执行真实容器中的完整阶段链。
    assert outcome.status is RunStatus.SUCCEEDED and outcome.patch is not None  # 公开测试通过才允许成功。
    assert (fixture.repository / "app.py").read_text(encoding="utf-8") == original  # 宿主源仓库必须保持原样。
    graph = json.loads((context.layout.run_dir / "evidence_graph.json").read_text(encoding="utf-8"))  # 读取可追溯图快照。
    assert any(node["kind"] == "verification_result" and node["metadata"]["passed"] is True for node in graph["nodes"])  # 验证 Docker 测试事实进入图。
