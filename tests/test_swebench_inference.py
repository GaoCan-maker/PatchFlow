"""阶段 7.5 仓库准备与官方实例镜像命名测试。"""  # 测试不联网、不调用模型也不启动 Docker。

from __future__ import annotations  # 延迟解析测试类型标注。

from pathlib import Path  # 管理 pytest 临时仓库准备目录。

import pytest  # 断言已有目录保护和错误类型。

from patchflow.application.run_initializer import initialize_run  # 创建标准运行目录用于收集测试。
from patchflow.config.models import (  # 生成不含密钥的实验快照。
    AppConfig,  # 组合 Agent、模型与 Runtime 配置。
    ModelConfig,  # 保存 provider、模型 ID 和 Base URL。
    RuntimeConfig,  # 标记 SWE-bench 官方环境。
)  # 完成配置模型导入。
from patchflow.domain.enums import RepositoryKind, TaskSource  # 检查输出任务的公开定位信息。
from patchflow.storage.manifest_store import ManifestStore  # 按正式原子方式保存模拟终态清单。
from patchflow.swebench import inference  # 通过模块替换私有 Git 执行边界。
from patchflow.swebench.inference import (  # 测试阶段 7.5 公开接口。
    RepositoryPreparationError,  # 断言已有目录保护错误。
    collect_run_predictions,  # 测试标准运行工件收集。
    official_instance_image,  # 测试官方远程镜像命名。
    prepare_repositories,  # 测试固定子集仓库准备。
)  # 完成阶段 7.5 接口导入。
from patchflow.swebench.models import (  # 构造严格记录和安全任务。
    SweBenchRecord,  # 模拟一条含私有字段的官方记录。
    adapt_record_to_task,  # 构造收集测试使用的安全任务。
)  # 完成 SWE-bench 模型导入。


def _record() -> SweBenchRecord:  # 创建同时含私有评测字段的模拟官方记录。
    return SweBenchRecord(instance_id="sympy__sympy-20590", repo="sympy/sympy", base_commit="abc123", problem_statement="Fix a public symbolic evaluation behavior regression.", patch="gold-secret", test_patch="hidden-secret", FAIL_TO_PASS=["hidden::target"], PASS_TO_PASS=["hidden::regression"])  # 私有值绝不能进入返回 TaskSpec。


def test_official_instance_image_matches_swebench_5_remote_naming() -> None:  # 验证双下划线和 x86_64 标签转换规则。
    image = official_instance_image("sympy__sympy-20590", machine="amd64")  # 使用用户已经成功拉取过的真实实例 ID。
    assert image == "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest"  # 必须与官方 gold 运行保留镜像完全一致。


def test_prepare_repositories_writes_only_public_task_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # 验证准备流程不需要真实网络且保持答案隔离。
    def fake_git(arguments: tuple[str, ...], *, timeout_seconds: float) -> str:  # 模拟 Git clone、checkout 和状态查询。
        assert timeout_seconds == 30.0  # 调用方设置的超时必须传到每一步。
        if arguments[0] == "clone":  # clone 的最后一个参数是新的实例目录。
            Path(arguments[-1]).mkdir(parents=True)  # 模拟 Git 创建目标仓库目录。
            return ""  # clone 成功不需要控制面输出。
        if arguments[-2:] == ("rev-parse", "HEAD"):  # 模拟当前 HEAD 查询。
            return "abc123\n"  # 返回与 base_commit 一致的提交。
        if "rev-parse" in arguments:  # 模拟 base_commit 完整解析。
            return "abc123\n"  # 返回同一个稳定提交。
        if "status" in arguments:  # 模拟工作区清洁性检查。
            return ""  # 空输出表示没有修改或未跟踪文件。
        return ""  # checkout 成功无需输出。

    monkeypatch.setattr(inference, "_run_git", fake_git)  # 将所有外部 Git 调用替换为内存行为。
    tasks = prepare_repositories((_record(),), instance_ids=("sympy__sympy-20590",), output_root=tmp_path / "repos", dataset_name="princeton-nlp/SWE-bench_Lite", split="test", timeout_seconds=30.0)  # 准备一个固定实例。
    assert len(tasks) == 1  # 固定分母必须完整保留。
    task = tasks[0]  # 读取唯一安全推理任务。
    assert task.source is TaskSource.SWE_BENCH and task.repo_spec.kind is RepositoryKind.LOCAL  # Agent 应使用准备好的本地基础仓库。
    assert Path(task.repo_spec.location).name == "sympy__sympy-20590"  # 每个实例必须拥有独立目录。
    serialized = task.model_dump_json().lower()  # 将最终 Agent 输入转为可搜索文本。
    assert "gold-secret" not in serialized and "hidden-secret" not in serialized  # 金补丁和隐藏测试内容不能泄漏。
    assert "fail_to_pass" not in serialized and "pass_to_pass" not in serialized  # 私有答案字段也不能进入 TaskSpec。


def test_prepare_repositories_never_overwrites_existing_directory(tmp_path: Path) -> None:  # 验证重复实验不会覆盖已有仓库。
    existing = tmp_path / "repos" / "sympy__sympy-20590"  # 构造预先存在的实例目录。
    existing.mkdir(parents=True)  # 模拟用户已经准备或正在使用的仓库。
    with pytest.raises(RepositoryPreparationError, match="已存在"):  # 准备器必须在任何 Git 调用前拒绝。
        prepare_repositories((_record(),), instance_ids=("sympy__sympy-20590",), output_root=tmp_path / "repos", dataset_name="dataset", split="test")  # 尝试重复使用同一输出目录。


def test_collect_run_predictions_uses_manifest_task_and_final_patch(tmp_path: Path) -> None:  # 验证运行工件可无手工复制地导出官方预测。
    repository = tmp_path / "repo"  # 创建 TaskSpec 所需的公开本地仓库路径。
    repository.mkdir()  # 此测试只验证收集，不需要真实 Git 内容。
    task = adapt_record_to_task(_record(), dataset_name="princeton-nlp/SWE-bench_Lite", split="test", repository_location=str(repository), repository_kind=RepositoryKind.LOCAL)  # 构造不含隐藏答案的 SWE-bench 任务。
    config = AppConfig(model=ModelConfig(provider="openai_compatible", model="deepseek-v3.2", base_url="https://api.agicto.cn/v1"), runtime=RuntimeConfig(kind="swe_bench"))  # 模拟正式实验配置快照。
    context = initialize_run(task, config, runs_root=tmp_path / "runs")  # 创建标准 manifest、task 和运行目录。
    context.layout.final_patch_path.write_text("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n", encoding="utf-8")  # 写入 Runtime 风格的非空最终补丁。
    context.manifest.status = "patch_generated"  # 模拟 benchmark prediction 正常终态。
    context.manifest.final_patch_path = str(context.layout.final_patch_path)  # 保存正式清单中的工件引用。
    ManifestStore(context.layout.manifest_path).save(context.manifest)  # 原子持久化模拟终态。
    predictions = collect_run_predictions(tmp_path / "runs", instance_ids=(task.task_id,), model_name_or_path="patchflow-main/deepseek-v3.2")  # 按固定分母收集 prediction。
    assert len(predictions) == 1 and predictions[0].instance_id == task.task_id  # 保留唯一实例身份。
    assert predictions[0].model_name_or_path == "patchflow-main/deepseek-v3.2"  # 保存简历实验需要的方法标识。
    assert predictions[0].model_patch.endswith("+new\n")  # 补丁必须来自标准 final.patch 工件。
