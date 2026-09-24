# 第七周：SWE-bench Adapter 与官方 Evaluation Harness

## 1. 本周目标

第七周把 PatchFlow 的 Agent 推理结果接到官方 SWE-bench 评分系统，但不重新实现官方的 `resolved` 判定。本周代码完成四项交付：

1. 将 SWE-bench JSON/JSONL 记录转换为 PatchFlow `TaskSpec`。
2. 将最终 Git diff 导出为官方三字段 prediction JSONL。
3. 以参数数组调用官方 `swebench.harness.run_evaluation`，并解析不同版本的 JSON 报告。
4. 用数据模型、递归检查和进程边界隔离 Inference Phase 与 Evaluation Phase。

## 2. 最重要的数据边界

### Inference Phase 可以看到

- `instance_id`。
- `repo` 和 `base_commit`。
- `problem_statement`。
- 基础提交中的源码与仓库已有测试。
- 不包含答案的公开环境信息。

### Inference Phase 不可以看到

- 官方 gold patch。
- `test_patch`。
- `FAIL_TO_PASS`。
- `PASS_TO_PASS`。
- 官方最终测试结果。

`SweBenchRecord` 只用于读取原始数据集。`adapt_record_to_task()` 创建的新 `TaskSpec` 不含上述答案；`assert_inference_payload_safe()` 会在推理文件写盘前递归拒绝这些键。评测私有信息只能进入 `SweBenchEvaluationRecord`，并且不是运行 Agent 或官方 Harness 所必需的输入。

### Evaluation Phase 只接收

```json
{
  "instance_id": "owner__repo-123",
  "model_name_or_path": "patchflow-main/model-name",
  "model_patch": "diff --git ..."
}
```

官方 Harness 自己加载数据集、应用测试补丁、构建或拉取评测镜像并判定 `resolved`。PatchFlow 只转换报告格式，绝不依据内部公开测试自行宣布 SWE-bench 成功。

## 3. 代码阅读顺序

1. `src/patchflow/swebench/models.py`
   先读 `SweBenchRecord`、`adapt_record_to_task()`、`SweBenchPrediction` 和三个写入函数。这一文件定义数据边界。
2. `tests/test_swebench_adapter.py`
   对照测试理解 gold/test patch 为什么不能进入 `TaskSpec`，以及 prediction 为什么只能有三个字段。
3. `src/patchflow/swebench/harness.py`
   依次读配置模型、`build_harness_command()`、`parse_harness_results()` 和 `run_harness()`。
4. `tests/test_swebench_harness.py`
   查看命令契约、报告兼容、缺失结果与矛盾结果的处理。
5. `src/patchflow/swebench_cli.py`
   最后读 CLI 如何把前述组件装配为 `convert`、`export`、`harness-command`、`run-harness` 和 `parse-results`。
6. `tests/test_swebench_harness_integration.py`
   查看真实 gold patch 环境测试为何默认跳过，以及需要哪些环境变量。
7. `docs/adr/0011-swebench-inference-evaluation-isolation.md`
   查看架构取舍、代价和重新评估条件。

## 4. 环境策略

PatchFlow Agent 环境与官方 SWE-bench 环境建议分开：

- `patchflow`：运行 Agent、模型 SDK、仓库工具和本项目测试。
- `patchflow-swebench`：只安装官方 `swebench` 包并运行最终评测。

这样做可以避免官方评测依赖与 Agent 依赖互相锁死，也使“Agent 进程已停止后才启动官方评测”成为真实的进程边界。

创建独立环境的参考命令：

```bash
conda create -n patchflow-swebench python=3.11 -y
conda activate patchflow-swebench
python -m pip install --upgrade pip
python -m pip install swebench
python -c 'import swebench; print(swebench.__file__)'
docker version
```

安装版本应在正式实验报告中记录。官方镜像、数据集和构建缓存可能占用大量磁盘；第一次运行前应检查 Docker 可用空间，先用一个实例和 `--max-workers 1`，不要直接跑完整 Lite/Verified 集。

官方 Harness 不需要 LLM API key。只有先运行 PatchFlow Agent 生成预测时才需要模型服务密钥与费用确认。`run_harness()` 会从子进程环境中移除常见模型 API 密钥。

## 5. 转换任务

先把选定数据记录保存为本地 JSON 数组或 JSONL。转换本身离线，不调用模型、Docker 或官方 Harness：

```bash
conda activate patchflow
cd "/mnt/c/Users/73621/Desktop/code agent"
python -m patchflow.swebench_cli convert /path/to/subset.jsonl \
  --dataset-name princeton-nlp/SWE-bench_Lite \
  --split test \
  --tasks-output /path/to/week7/tasks.json \
  --bundle-output /path/to/week7/inference-bundle.json
```

没有 `--repository-map` 时，TaskSpec 使用官方 GitHub URL 和 `RepositoryKind.GIT`。当前 Agent 的 Docker 入口仍要求仓库事先准备好，因此真实推理应提供实例到本地干净仓库的映射：

```json
{
  "owner__repo-123": {
    "kind": "local",
    "location": "/home/user/swebench-repos/owner__repo-123"
  }
}
```

然后增加：

```bash
--repository-map /path/to/repository-map.json
```

`--private-evaluation-output` 不是运行 Agent 的必需参数。只有研究 gold 定位指标时才应显式生成该文件，并放在 Agent 工作区、run artifact 和模型上下文之外。

## 6. 导出 prediction

Agent 结束后，把每个实例的最终 `final.patch` 汇总为 JSON 数组或 JSONL。输入可以已经含 `model_name_or_path`，也可以统一由参数补充：

```json
[
  {
    "instance_id": "owner__repo-123",
    "model_patch": "diff --git a/pkg/a.py b/pkg/a.py\n..."
  }
]
```

导出五题正式验收文件：

```bash
python -m patchflow.swebench_cli export /path/to/raw-patches.json \
  /path/to/predictions.jsonl \
  --model-name-or-path patchflow-main/ag-model \
  --minimum-predictions 5
```

导出器会拒绝空补丁、额外字段、NUL 字节、重复 `instance_id` 和低于指定数量的预测。

## 7. 先做 gold patch 环境验证

先只预览命令，不启动 Docker：

```bash
python -m patchflow.swebench_cli harness-command \
  --dataset-name princeton-nlp/SWE-bench_Lite \
  --split test \
  --run-id patchflow-week7-gold \
  --workdir "$HOME/swebench-eval" \
  --predictions gold \
  --instance-id owner__repo-123 \
  --python-executable "$HOME/miniconda3/envs/patchflow-swebench/bin/python" \
  --max-workers 1
```

确认实例、磁盘和 Docker 环境后，真实运行必须增加授权开关与报告路径：

```bash
python -m patchflow.swebench_cli run-harness \
  --dataset-name princeton-nlp/SWE-bench_Lite \
  --split test \
  --run-id patchflow-week7-gold \
  --workdir "$HOME/swebench-eval" \
  --predictions gold \
  --instance-id owner__repo-123 \
  --python-executable "$HOME/miniconda3/envs/patchflow-swebench/bin/python" \
  --max-workers 1 \
  --report-output "$HOME/swebench-eval/patchflow-week7-gold.json" \
  --allow-harness-run
```

也可以用默认跳过的 pytest 集成测试完成同一检查：

```bash
export PATCHFLOW_RUN_SWEBENCH_TESTS=1
export PATCHFLOW_SWEBENCH_INSTANCE_ID='owner__repo-123'
export PATCHFLOW_SWEBENCH_WORKDIR="$HOME/swebench-eval"
export PATCHFLOW_SWEBENCH_PYTHON="$HOME/miniconda3/envs/patchflow-swebench/bin/python"
python -m pytest -q tests/test_swebench_harness_integration.py
```

gold patch 若不能 `resolved`，先排查官方包版本、Docker 镜像、磁盘、网络、实例名和数据集分片，不应继续把 Agent 失败归因于策略。

## 8. 运行五个 Agent prediction

五题 prediction 文件必须由已经停止的 Agent 运行生成。最终评测不可把官方结果反馈给同一次 Agent 继续修改：

```bash
python -m patchflow.swebench_cli run-harness \
  --dataset-name princeton-nlp/SWE-bench_Lite \
  --split test \
  --run-id patchflow-week7-agent-five \
  --workdir "$HOME/swebench-eval" \
  --predictions /path/to/predictions.jsonl \
  --python-executable "$HOME/miniconda3/envs/patchflow-swebench/bin/python" \
  --max-workers 1 \
  --report-output "$HOME/swebench-eval/patchflow-week7-agent-five.json" \
  --allow-harness-run
```

自定义 prediction 会先被严格读取，文件内的实例集合就是默认评测分母；若额外提供 `--instance-id`，二者集合必须完全一致。

官方 Harness 会按 `run_id` 与 `instance_id` 缓存结果。补丁、模型、方法或配置发生变化时必须换一个新的 `run_id`，否则官方工具可能复用旧结果，造成看似重新评测、实际没有执行新补丁的问题。

## 9. 结果分类

进程级状态：

- `completed`：官方命令成功，所有预期实例均有可解析结论。
- `process_failed`：官方进程非零退出，属于评测或环境故障。
- `timed_out`：PatchFlow 外层总超时终止进程组。
- `results_missing`：进程成功，但至少一个实例结果缺失或报告无法解析。

实例级状态：

- `resolved`：官方明确给出 `resolved=true`。
- `unresolved`：官方明确给出 `resolved=false`。
- `error`：官方将实例归为评测错误。
- `empty_patch`：官方报告空补丁。
- `missing`：预期实例没有出现在报告中。

`missing` 和 `error` 不能并入 `unresolved`。否则 Docker 或报告故障会被错误统计成 Agent 能力失败。

## 10. 当前限制

- 适配器已经通过离线测试，但当前开发环境尚未安装或运行官方 `swebench`，因此不能声称已完成 gold 与五题真实验收。
- 当前 `TaskSpec` 转换不自动 clone 仓库或准备官方推理镜像；正式推理需先准备本地基础仓库并提供 repository map。
- 报告解析器兼容逐实例 `report.json` 及常见 resolved/unresolved/error/empty 列表；官方格式升级后应以官方输出夹具补充回归测试。
- 外层超时会终止 Harness 进程组，但 Docker daemon 管理的容器仍应通过 `docker ps` 和官方日志复查。
- 正式结果必须记录 PatchFlow commit、官方 swebench 版本、数据集、分片、实例列表、模型与方法、预算、prediction 摘要和 run ID。
