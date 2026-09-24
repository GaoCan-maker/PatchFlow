"""为 SWE-bench 推理准备公开仓库，并生成官方实例镜像名称。"""  # 本模块不读取或写出 gold patch 与隐藏测试答案。

from __future__ import annotations  # 延迟解析现代容器类型标注。

import platform  # 根据宿主架构选择官方镜像标签中的架构名称。
import shutil  # 只清理由本次失败准备创建且已确认位于输出根内的目录。
import subprocess  # 通过参数数组调用 Git，不把仓库字段交给 Shell。
from collections.abc import Iterable  # 标注一批已经严格解析的数据集记录。
from pathlib import Path  # 管理每个实例独立的只读源仓库目录。

from patchflow.domain.enums import (  # 校验仓库定位、运行终态和任务来源。
    RepositoryKind,  # 标明准备后的仓库位于本地目录。
    RunStatus,  # 只收集正常生成补丁的运行。
    TaskSource,  # 只允许 SWE-bench 任务导出官方 prediction。
)  # 完成领域枚举导入。
from patchflow.domain.run import RunManifest  # 严格解析每次 Agent 运行清单。
from patchflow.domain.task import TaskSpec  # 返回 Agent CLI 可直接消费的安全任务。
from patchflow.swebench.models import (  # 复用第七周公开字段与预测边界。
    SweBenchPrediction,  # 构造官方严格三字段 prediction。
    SweBenchRecord,  # 接收已隔离私有字段的原始记录模型。
    adapt_record_to_task,  # 将公开字段转换为安全 TaskSpec。
)  # 完成 SWE-bench 数据模型导入。


class RepositoryPreparationError(RuntimeError):  # 表示公开仓库准备失败而不是 Agent 修复失败。
    """公开仓库克隆、检出或清洁性检查失败。"""  # 供 CLI 输出稳定错误分类。


def official_instance_image(instance_id: str, *, namespace: str = "swebench", tag: str = "latest", machine: str | None = None) -> str:  # 按 SWE-bench 5 的公开规则生成实例镜像名。
    architecture = (machine or platform.machine()).lower()  # 允许测试显式覆盖宿主架构。
    if architecture in {"x86_64", "amd64"}:  # Docker Hub 的 amd64 镜像历史标签使用 x86_64。
        image_architecture = "x86_64"  # 对齐官方 ImageSpec.name 行为。
    elif architecture in {"aarch64", "arm64"}:  # 识别常见 ARM64 宿主名称。
        image_architecture = "arm64"  # 使用官方 ARM64 标签。
    else:  # 未知架构不能猜测一个可能不存在的镜像。
        raise ValueError(f"不支持的 SWE-bench 镜像架构：{architecture}")  # 要求用户显式处理平台差异。
    local_key = f"sweb.eval.{image_architecture}.{instance_id}:{tag}".lower()  # 先生成官方本地镜像键。
    remote_key = local_key.replace("__", "_1776_")  # Docker Hub 不接受双下划线，官方用固定占位符替换。
    return f"{namespace}/{remote_key}"  # 返回可由 Docker 直接查找或拉取的完整名称。


def prepare_repositories(records: Iterable[SweBenchRecord], *, instance_ids: tuple[str, ...], output_root: Path, dataset_name: str, split: str, timeout_seconds: float = 900.0) -> tuple[TaskSpec, ...]:  # 为固定实验子集创建干净仓库。
    if not instance_ids:  # 空实例集合无法形成实验分母。
        raise ValueError("instance_ids 不能为空")  # 在网络操作前拒绝无意义调用。
    if len(set(instance_ids)) != len(instance_ids):  # 同一实例只能准备一次。
        raise ValueError("instance_ids 不能重复")  # 防止目录冲突和统计重复。
    if timeout_seconds <= 0:  # Git 命令必须具有有限正超时。
        raise ValueError("timeout_seconds 必须大于零")  # 拒绝无限挂起配置。
    record_map = {record.instance_id: record for record in records}  # 只按公开实例 ID 建立查询表。
    missing = tuple(instance_id for instance_id in instance_ids if instance_id not in record_map)  # 找出输入数据集中缺失的请求实例。
    if missing:  # 缺失实例不能被静默跳过。
        raise ValueError(f"数据集中找不到实例：{', '.join(missing)}")  # 保持固定实验分母。
    root = output_root.resolve()  # 规范化用户选择的仓库准备根目录。
    root.mkdir(parents=True, exist_ok=True)  # 只创建显式输出目录及其父目录。
    tasks: list[TaskSpec] = []  # 按调用方指定顺序保存安全推理任务。
    for instance_id in instance_ids:  # 逐个准备固定子集中的实例。
        record = record_map[instance_id]  # 获取已经由 Pydantic 校验的公开记录视图。
        destination = (root / instance_id).resolve()  # 为每个实例分配独立仓库，避免相同项目不同提交串线。
        if not destination.is_relative_to(root) or destination == root:  # 对未来实例 ID 规则变化保持路径防御。
            raise RepositoryPreparationError(f"实例目录越出输出根：{instance_id}")  # 禁止路径穿越。
        if destination.exists():  # 不覆盖用户已有仓库、缓存或实验结果。
            raise RepositoryPreparationError(f"实例仓库目录已存在：{destination}")  # 要求选择新目录或显式复用映射。
        repository_url = f"https://github.com/{record.repo}.git"  # 仅依据数据集公开 repo 字段构造来源。
        try:  # 任一步失败都只清理本轮新创建的目标目录。
            _run_git(("clone", "--quiet", "--no-checkout", repository_url, str(destination)), timeout_seconds=timeout_seconds)  # 克隆完整提交历史以确保 base_commit 可检出。
            _run_git(("-C", str(destination), "checkout", "--quiet", "--detach", record.base_commit), timeout_seconds=timeout_seconds)  # 精确停在官方基础提交。
            resolved = _run_git(("-C", str(destination), "rev-parse", "--verify", f"{record.base_commit}^{{commit}}"), timeout_seconds=timeout_seconds).strip()  # 解析官方提交为完整 SHA。
            head = _run_git(("-C", str(destination), "rev-parse", "HEAD"), timeout_seconds=timeout_seconds).strip()  # 查询当前实际 HEAD。
            status = _run_git(("-C", str(destination), "status", "--porcelain=v1", "--untracked-files=all"), timeout_seconds=timeout_seconds).strip()  # 检查工作区没有额外修改。
            if head != resolved or status:  # 错误提交或脏仓库都不能进入可复现实验。
                raise RepositoryPreparationError(f"实例仓库未停在干净 base_commit：{instance_id}")  # 阻止污染输入继续传播。
        except BaseException:  # 包括超时和用户取消时都避免留下半个仓库。
            if destination.exists() and destination.is_relative_to(root):  # 再次确认只操作显式输出根内的新目录。
                shutil.rmtree(destination, ignore_errors=True)  # 清理由本函数本轮创建的不完整仓库。
            raise  # 保留原始错误类型与因果链。
        task = adapt_record_to_task(record, dataset_name=dataset_name, split=split, repository_location=str(destination), repository_kind=RepositoryKind.LOCAL)  # 只把公开 Issue、提交和本地路径写入任务。
        tasks.append(task)  # 保存可直接交给 Agent 的安全 TaskSpec。
    return tuple(tasks)  # 返回与固定实例顺序一致的不可变任务集合。


def collect_run_predictions(runs_root: Path, *, instance_ids: tuple[str, ...], model_name_or_path: str) -> tuple[SweBenchPrediction, ...]:  # 从独立实验目录收集最终补丁为官方三字段对象。
    if not instance_ids or len(set(instance_ids)) != len(instance_ids):  # 正式收集必须声明非空且唯一的固定分母。
        raise ValueError("instance_ids 必须非空且唯一")  # 防止遗漏或重复预测被静默接受。
    root = runs_root.resolve(strict=True)  # 确认调用方给出的实验运行根真实存在。
    manifests = tuple(sorted(root.glob("*/manifest.json")))  # 只扫描根目录下一层的标准运行布局。
    collected: dict[str, SweBenchPrediction] = {}  # 按实例 ID 保存唯一 prediction。
    for manifest_path in manifests:  # 逐个检查可审计运行清单。
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))  # 拒绝损坏或未知字段的清单。
        if manifest.task_id not in instance_ids:  # 同一 runs root 可以含非本次实验任务。
            continue  # 不把额外任务混入固定分母。
        if manifest.task_id in collected:  # 同一实例多个运行无法自动判断应提交哪一个。
            raise ValueError(f"实例存在多个可收集运行：{manifest.task_id}")  # 要求每个实验使用独立 runs root。
        if manifest.status not in {RunStatus.PATCH_GENERATED, RunStatus.SUCCEEDED}:  # 失败或基础设施错误运行没有可提交补丁。
            raise ValueError(f"实例运行没有生成可提交补丁：{manifest.task_id}，状态 {manifest.status.value}")  # 保持失败显式可见。
        run_dir = manifest_path.parent.resolve()  # 获取当前标准运行目录。
        task_path = run_dir / "task.json"  # 使用固定布局读取推理任务快照。
        patch_path = run_dir / "final.patch"  # 使用固定布局而不信任清单中的任意文件路径。
        task = TaskSpec.model_validate_json(task_path.read_text(encoding="utf-8"))  # 重新校验当时真实 Agent 输入。
        if task.source is not TaskSource.SWE_BENCH or task.task_id != manifest.task_id:  # 禁止把普通任务或错配快照导出到官方 Harness。
            raise ValueError(f"运行任务不是匹配的 SWE-bench 输入：{manifest.run_id}")  # 阻止跨任务补丁串线。
        patch = patch_path.read_text(encoding="utf-8")  # 读取 Runtime 导出的真实最终 diff。
        collected[manifest.task_id] = SweBenchPrediction(instance_id=manifest.task_id, model_name_or_path=model_name_or_path, model_patch=patch)  # 通过官方严格模型拒绝空补丁。
    missing = tuple(instance_id for instance_id in instance_ids if instance_id not in collected)  # 检查固定分母中的未完成实例。
    if missing:  # 缺失预测不能由官方工具默默缩小分母。
        raise ValueError(f"runs root 缺少实例 prediction：{', '.join(missing)}")  # 要求先完成或明确重跑失败任务。
    return tuple(collected[instance_id] for instance_id in instance_ids)  # 按用户固定顺序返回标准预测。


def _run_git(arguments: tuple[str, ...], *, timeout_seconds: float) -> str:  # 执行一次无 Shell Git 命令并返回标准输出。
    try:  # 分别处理超时、二进制缺失和非零退出。
        completed = subprocess.run(("git", *arguments), check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds)  # 使用参数数组并限制输出和墙钟。
    except subprocess.TimeoutExpired as error:  # 克隆或检出超过用户设置的上限。
        raise RepositoryPreparationError("Git 仓库准备超时") from error  # 隐藏可能很长的部分输出。
    except OSError as error:  # Git 未安装或无法启动属于准备环境错误。
        raise RepositoryPreparationError("无法启动 Git，请检查 WSL 环境") from error  # 给出可行动诊断。
    if completed.returncode != 0:  # 非零退出说明当前仓库步骤没有完成。
        detail = (completed.stderr or completed.stdout or "未知 Git 错误")[-2_000:]  # 只保留有限尾部诊断。
        raise RepositoryPreparationError(f"Git 仓库准备失败：{detail.strip()}")  # 不把失败误计为 Agent unresolved。
    return completed.stdout  # 返回控制面所需的有限文本结果。
