# 第四周：仓库索引与 Hybrid Localization

## 交付范围

- `src/patchflow/localization.py`：从已经启动的 Runtime 读取 Git 已跟踪的 Python 文件，生成仓库地图、AST 类/函数/方法索引、静态导入和调用名称，并保留受限源码行用于搜索。文件数、行数、字符数和命令时间都有上限；被跳过文件会出现在 `skipped`，枚举截断则直接失败。
- `build_repo_index`：只允许在干净的基础提交上调用。可选 JSON 缓存键包含仓库位置、基础提交、语言适配器版本和索引配置。默认不把源码缓存到磁盘。
- `localize`：融合 Issue 命名词、源码命中、traceback 栈帧、失败测试导入关系、符号与调用名称。每个文件和函数候选都包含原始 `features`、实际 `contributions` 和可回溯的 `evidence`。测试文件的弱先验惩罚也显示在贡献表中；Issue 明确点名测试路径时不惩罚。
- `src/patchflow/evaluation/localization.py`：在独立 Docker 容器中，按文件和符号分别计算 Top-1/3/5/10 与 MRR，报告完整配置和五个留一通道消融。金标不写入 `TaskSpec`，也不发送给 Agent。
- `src/patchflow/localization_cli.py`：单独的离线评测命令，不构造模型客户端、不读取 API key、不调用付费模型。
- `src/patchflow/tools/localization.py`：`repo_map` 和 `find_symbol` 两个 Pydantic 校验的只读工具，分别分页查看基础提交仓库地图、按 AST 名称查找符号；实际 JSON 观察大小也受工具预算约束。

## 离线运行

在 WSL 的 `patchflow` 环境、项目根目录执行：

```bash
python -m patchflow.cli prepare-smoke /tmp/patchflow-week4-smoke
python -m patchflow.localization_cli /tmp/patchflow-week4-smoke/tasks.json docs/week4_smoke_gold.json --report /tmp/patchflow-week4-report.json
```

`prepare-smoke` 目标目录必须为空或不存在；请为重复运行换一个新的 `/tmp` 目录。第二条命令需要现有 `patchflow-runtime:py311` Docker 镜像及 Docker Desktop 的 WSL 集成，不需要注册新 API，也不会产生模型调用费用。可选 `--cache-dir /tmp/patchflow-index` 会将基础提交的源码文本保存为 JSON，请不要把该目录提交到 Git，也不要对含秘密的仓库使用不可信缓存目录。

金标文件是 `task_id -> {file, symbol, traceback?, failing_tests?}` 的 JSON 映射。`traceback` 和 `failing_tests` 只能填写修复前公开测试确实观察到的信息，不得使用参考补丁或私有测试答案。`docs/week4_smoke_gold.json` 只适配现有两题烟测集。

## 如何读报告

- `summary.full` 是所有通道开启时的指标；`summary.without_traceback` 等项是相同任务、相同候选集和相同权重下只关闭一个通道后的指标。
- `cases[*].candidates.files` 与 `symbols` 保留 Top-K、分数、原始特征、加权贡献和证据；`skipped` 列出索引未覆盖的文件。
- 没有 traceback 或失败测试输入时，相应消融和完整配置相同是正常现象，不代表这些通道无效。
- 两题烟测只能验证流程，不能说明真实代码仓库的定位泛化能力；正式报告需要更多仓库、多文件干扰项、修复前公开失败证据和人工审核的金标。

## 当前边界和第五周接口

本周结果由独立入口输出，不改变第三周三条 baseline 的工具集合和问题上下文。`RepoMapTool(index)` 和 `FindSymbolTool(index)` 已可被后续策略注入，但目前只反映建索引时的基础提交，补丁后不可把它们误认为实时视图。第五周显式状态机可以在 `LOCATE` 阶段消费 `RepoIndex` 与 `LocalizationResult`，再把候选和证据写入 Evidence Graph。这样不会把定位增强混入现有 baseline 的结果。

当前只解析 Python AST；静态 `calls` 是名称提示，不是可靠的跨模块调用图。源码搜索是受限文件内词项命中，不是完整的正则或语义检索。缓存只适用于干净基础提交，补丁后的增量索引留到候选工作区阶段。大仓库超过配置上限时明确失败或报告跳过文件，而不是将不完整结果伪装成完整仓库地图。

## 代码阅读顺序

1. `src/patchflow/localization.py`：`IndexSettings`、`IndexedFile`、`RepoIndex`、`build_repo_index`。
2. 同文件：`LocalizationSettings`、`RankedCandidate`、`localize`。
3. `src/patchflow/evaluation/localization.py`：金标、排名计算、留一消融。
4. `src/patchflow/tools/localization.py`：两个只读工具及分页/输出预算。
5. `src/patchflow/localization_cli.py`：命令行装配及 JSON 输入。
6. `tests/test_localization.py`、`tests/test_localization_tools.py` 和 `tests/test_localization_integration.py`：边界、工具契约和 Docker 端到端验收。
