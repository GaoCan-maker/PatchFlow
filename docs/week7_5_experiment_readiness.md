# 阶段 7.5：真实模型与 SWE-bench 实验接线

## 1. 阶段目标

阶段 7 已经完成数据隔离、prediction 格式和官方 Evaluation Harness。阶段 7.5 只解决正式实验前的最后四个问题：

1. 从已下载的数据中选择固定实例，并为每个实例准备位于 `base_commit` 的干净本地仓库。
2. 在对应的官方 SWE-bench 实例镜像中运行 PatchFlow，而不是使用通用 Python 镜像。
3. 在推理阶段看不到 `gold patch`、`test_patch`、`FAIL_TO_PASS` 和 `PASS_TO_PASS` 时，仍能输出待评分 prediction，但绝不把它记为 `succeeded`。
4. 从标准运行工件自动收集 prediction，再交给独立 `patchflow-swebench` 环境中的官方 Harness。

本阶段还把实习项目实验缩减为主方法、单轮基线和案例分析，不做论文式全量消融。

## 2. 新增入口

### 2.1 模型兼容性探针

探针只发送一句要求返回 `{"probe":"ok"}` 的短请求，不读取仓库，不启动 Docker。它用于检查 API key、Base URL、模型 ID、`usage` 字段和严格 JSON 输出是否兼容。

```bash
conda activate patchflow  # 进入运行 Agent 的环境。
read -r -s -p 'AGICTO API Key: ' PATCHFLOW_MODEL_API_KEY  # 在当前终端静默读取密钥。
printf '\n'  # 让后续终端输出换行。
export PATCHFLOW_MODEL_API_KEY  # 只导出到当前 WSL 进程树，不写入仓库。
python -m patchflow.model_probe_cli --provider openai_compatible --model-id deepseek-v4-flash --base-url https://api.agicto.cn/v1 --allow-api-spend  # 发起一次极小付费请求。
```

你已经用 `deepseek-v4-flash` 跑通探针；后续固定在每次命令的 `--model-id deepseek-v4-flash`，不写进源码、不写进 API key 环境变量。运行后，模型 ID 会自动进入 `config.snapshot.json` 和 `manifest.json`，所以实验可追溯。

若服务商以后调整模型 ID，应以控制台精确 ID 为准，并重新跑探针；不同模型的结果不能混到同一组实验。

### 2.2 导出固定子集记录

下面的脚本必须在安装了 `swebench==5.0.2` 的独立环境中执行。先把最终选择的实例 ID 写入 `INSTANCE_IDS`，再生成本地 JSON。这个文件含官方原始字段，只用于转换和评测准备，不应作为 Agent prompt。

```bash
conda activate patchflow-swebench  # 进入官方 Harness 环境。
python - <<'PY'  # 从已经下载或缓存的数据集中导出固定子集。
import json  # 使用标准 JSON 保存官方记录。
from pathlib import Path  # 管理明确的输出路径。
from swebench.harness.run_evaluation import load_instances  # 使用当前 5.0.2 的官方加载函数。

INSTANCE_IDS = ["sympy__sympy-20590"]  # 先用已通过 gold 的实例做单题联调，正式实验时替换为固定十题。
records = load_instances("princeton-nlp/SWE-bench_Lite", "test", INSTANCE_IDS, None)  # 只加载固定实例集合。
output = Path("experiments/week75/subset.json")  # 将原始记录放在明确实验目录。
output.parent.mkdir(parents=True, exist_ok=True)  # 创建输出父目录。
output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 写出可由 PatchFlow 严格解析的 JSON。
print(output)  # 打印真实输出位置供下一步使用。
PY
```

### 2.3 准备基础仓库

`prepare` 会显式联网克隆公开 GitHub 仓库，为每个实例创建独立目录，检出精确 `base_commit`，检查工作区干净，然后只写出安全 `TaskSpec`。它不会覆盖已有目录。

```bash
conda activate patchflow  # 切回 PatchFlow 环境。
python -m patchflow.swebench_cli prepare experiments/week75/subset.json --instance-id sympy__sympy-20590 --repositories-root experiments/week75/repos --tasks-output experiments/week75/tasks.json --dataset-name princeton-nlp/SWE-bench_Lite --split test  # 准备单题仓库与安全任务。
python -m patchflow.cli validate experiments/week75/tasks.json  # 在模型调用前离线复核任务格式。
```

正式十题时，为每个实例重复添加一个 `--instance-id ID`。不要把阶段 7 生成的 private evaluation 文件传给 Agent CLI。

### 2.4 准备官方实例镜像

PatchFlow DockerRuntime 使用 `--pull never`，因此模型运行前必须显式准备镜像。镜像名按 SWE-bench 5 规则从实例 ID 自动推导；例如：

```text
swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest
```

你已经通过 gold 运行准备了这个 SymPy 镜像。其他实例可先运行单题 gold，或者显式 `docker pull` 对应镜像。每个实例必须使用自己的镜像，不能用 SymPy 镜像运行其他项目。

### 2.5 运行一个 PatchFlow prediction

```bash
conda activate patchflow  # 确保 API key 仍在当前终端环境中。
python -m patchflow.agent_cli experiments/week75/tasks.json --task-id sympy__sympy-20590 --strategy patchflow --provider openai_compatible --model-id deepseek-v4-flash --base-url https://api.agicto.cn/v1 --runs-root experiments/week75/runs-main-v4-flash-c1 --max-candidates 1 --candidate-concurrency 1 --index-max-files 2000 --docker-output-chars 100000 --benchmark-prediction-mode --allow-api-spend  # 先做最低成本单题联调。
```

预期成功语义是：

- `status` 为 `patch_generated`，不是 `succeeded`。
- `stop_reason` 为 `benchmark_prediction_ready`。
- `final.patch` 存在且非空。
- 轨迹只声称补丁可应用、修改范围合法、修改后的 Python 文件语法可解析。
- 是否真正修复由后续官方 Harness 决定。

单题跑通后，正式主方法可把 `--max-candidates` 调为 `2`，但先保持 `--candidate-concurrency 1`，避免 WSL 同时启动多个大型实例容器。

### 2.6 运行必要的 one-shot 基线

对完全相同的实例、模型和官方镜像，仅把策略与 runs root 改为：

```bash
python -m patchflow.agent_cli experiments/week75/tasks.json --task-id sympy__sympy-20590 --strategy one_shot --provider openai_compatible --model-id deepseek-v4-flash --base-url https://api.agicto.cn/v1 --runs-root experiments/week75/runs-one-shot-v4-flash --max-candidates 1 --candidate-concurrency 1 --benchmark-prediction-mode --allow-api-spend  # 生成一次性补丁对照。
```

one-shot 只读取固定大小的静态仓库快照并请求一次补丁，不使用 RepoMap、Hybrid Localization、Evidence Graph、显式计划或候选搜索。它足以回答“复杂 Agent 编排是否比一次直接生成更有价值”，无需再做多组消融。

### 2.7 收集 prediction

一个实验配置必须使用独占 runs root，并且每个实例恰好有一个运行。若某题失败，应在新的 runs root 重跑，不能让收集器自行挑选“最好的一次”。

```bash
python -m patchflow.swebench_cli collect-runs experiments/week75/runs-main-v4-flash-c1 experiments/week75/predictions-main-v4-flash-c1.jsonl --instance-id sympy__sympy-20590 --model-name-or-path patchflow_deepseek-v4-flash_c1  # 收集主方法 prediction。
python -m patchflow.swebench_cli collect-runs experiments/week75/runs-one-shot-v4-flash experiments/week75/predictions-one-shot-v4-flash.jsonl --instance-id sympy__sympy-20590 --model-name-or-path one-shot_deepseek-v4-flash  # 收集单轮基线 prediction。
```

正式十题同样重复添加十个 `--instance-id`，并可再运行 `export --minimum-predictions 10` 做二次格式校验。

### 2.8 官方评分

官方评分必须在不继承模型 API key 的独立环境中执行：

```bash
unset PATCHFLOW_MODEL_API_KEY  # 防止评测阶段继承模型密钥。
conda activate patchflow-swebench  # 进入已经跑通 gold 的官方环境。
swebench eval lite -p experiments/week75/predictions-main-v4-flash-c1.jsonl -i sympy__sympy-20590 --run-id patchflow-v4-flash-c1-001 -j 1  # 串行评测单题主方法。
swebench eval lite -p experiments/week75/predictions-one-shot-v4-flash.jsonl -i sympy__sympy-20590 --run-id one-shot-v4-flash-001 -j 1  # 串行评测单题基线。
```

阶段 7.5 的 Harness 适配器现在按 SWE-bench 5.0.2 的真实路径 `logs/run_evaluation/<run_id>/...` 查找逐实例报告。

## 3. 精简实验设计

### E0：接线验收，不作为能力结果

- 已完成：`sympy__sympy-20590` gold patch 官方评分为 1/1 resolved。
- 已完成：`deepseek-v4-flash` 模型探针，接口返回严格 JSON 和 token usage。
- 待完成：同一题生成一条 PatchFlow prediction，并用官方 Harness 得到 resolved 或 unresolved。
- 目的：排除 API、镜像、数据、补丁格式和 Harness 基础设施问题。

### E1：主结果，固定十题 SWE-bench Lite pilot

- 方法：PatchFlow + `deepseek-v4-flash`。
- 固定参数：`max_candidates=2`、`candidate_concurrency=1`、相同 token/时间预算。
- 只跑十个预先固定的实例，不根据结果更换题目。
- 报告：resolved 数、resolved rate、patch generation rate、基础设施错误数、平均模型调用、输入/输出 token、平均墙钟时间。
- 命名必须写成“固定 10 题 SWE-bench Lite pilot”，不能写成完整 SWE-bench Lite 成绩。

### E2：唯一必要对照，同十题 one-shot

- 模型、实例、基础提交、官方镜像和 Harness 完全相同。
- one-shot 每题只调用模型一次；PatchFlow 使用定位、计划和最多两个候选。
- 对比 resolved rate，同时并列 token、调用次数和时间，避免只谈成功率不谈成本。

### E3：三个案例，不新增模型实验

- 一个 resolved：展示 Issue、定位证据、计划、最终 diff 和官方通过结果。
- 一个 unresolved：分析定位错误、补丁错误或缺少动态反馈，不能把官方失败归因于“模型不够强”一句带过。
- 一个工程边界案例：若有 API、镜像或 Harness 错误，展示系统如何把 infrastructure error 与 unresolved 分开；若没有，就选择一个语法候选被本地拒绝的轨迹。

## 4. 不再做的实验

- 不做去掉 reflection、planning、test feedback、memory、RepoMap 等全套消融。
- 不跑完整 SWE-bench Lite 或 Verified。
- 不做多个模型横向大矩阵。
- 不为了提高数字而反复更换实例或只报告成功题。

对求职项目而言，十题主方法、十题 one-shot、官方 Harness、成本统计和三个可深挖案例已经能证明完整工程闭环。后续只有在时间和预算允许时，才把固定子集扩到 20 题；扩容不改变代码和实验协议。

## 5. 简历可用表述边界

可以写：实现了 repo-level Code Agent、官方实例镜像推理、严格 inference/evaluation 隔离、候选分支、证据图、预算与轨迹审计，并在固定 10 题 SWE-bench Lite pilot 上用官方 Harness 对比 one-shot。

不能写：达到某个 SWE-bench Lite/Verified 官方榜单分数，除非确实跑完对应完整分片并保留完整可复现报告。
