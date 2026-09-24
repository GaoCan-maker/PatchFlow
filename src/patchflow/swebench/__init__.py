"""PatchFlow 的 SWE-bench 数据与官方 Harness 适配层。"""  # 包入口保持轻量且不导入官方 swebench 依赖。

from patchflow.swebench.harness import (  # 暴露官方评测配置、调用和结果解析接口。
    HarnessProcessStatus,  # 暴露进程级状态分类。
    SweBenchHarnessConfig,  # 暴露严格 Harness 配置。
    SweBenchHarnessExecution,  # 暴露完整执行结果。
    SweBenchHarnessReport,  # 暴露规范评测报告。
    SweBenchInstanceResult,  # 暴露逐实例结果。
    SweBenchInstanceStatus,  # 暴露逐实例状态枚举。
    build_harness_command,  # 暴露无副作用命令构造器。
    parse_harness_results,  # 暴露离线报告解析器。
    run_harness,  # 暴露显式授权的官方进程入口。
)  # 结束 Harness 公共接口导入。
from patchflow.swebench.models import (  # 暴露纯数据转换和预测文件接口。
    InferenceBundle,  # 暴露推理安全 bundle。
    SweBenchEvaluationRecord,  # 暴露评测私有记录模型。
    SweBenchPrediction,  # 暴露官方 prediction 模型。
    SweBenchRecord,  # 暴露官方数据集记录模型。
    adapt_record_to_task,  # 暴露 TaskSpec 转换函数。
    assert_inference_payload_safe,  # 暴露递归泄漏检查。
    extract_evaluation_record,  # 暴露私有记录提取函数。
    prediction_from_task,  # 暴露 Agent patch 到官方预测转换函数。
    read_predictions,  # 暴露严格 prediction 读取函数。
    write_inference_bundle,  # 暴露带元数据推理 bundle 写入函数。
    write_predictions,  # 暴露标准 JSONL 导出函数。
    write_private_evaluation_records,  # 暴露显式私有文件写入函数。
    write_task_specs,  # 暴露兼容现有 CLI 的安全任务数组写入函数。
)  # 结束数据层公共接口导入。

__all__ = [  # 明确声明稳定公共 API 并阻止误用内部辅助函数。
    "HarnessProcessStatus",  # 导出进程状态。
    "InferenceBundle",  # 导出推理 bundle。
    "SweBenchEvaluationRecord",  # 导出私有记录。
    "SweBenchHarnessConfig",  # 导出 Harness 配置。
    "SweBenchHarnessExecution",  # 导出 Harness 执行结果。
    "SweBenchHarnessReport",  # 导出 Harness 报告。
    "SweBenchInstanceResult",  # 导出逐实例结果。
    "SweBenchInstanceStatus",  # 导出实例状态。
    "SweBenchPrediction",  # 导出 prediction 模型。
    "SweBenchRecord",  # 导出数据集记录。
    "adapt_record_to_task",  # 导出任务转换。
    "assert_inference_payload_safe",  # 导出隔离检查。
    "build_harness_command",  # 导出命令构造。
    "extract_evaluation_record",  # 导出私有信息提取。
    "parse_harness_results",  # 导出报告解析。
    "prediction_from_task",  # 导出预测转换。
    "read_predictions",  # 导出预测读取。
    "run_harness",  # 导出官方调用。
    "write_inference_bundle",  # 导出 bundle 写入。
    "write_predictions",  # 导出预测写入。
    "write_private_evaluation_records",  # 导出私有记录写入。
    "write_task_specs",  # 导出 TaskSpec 数组写入。
]  # 完成公共 API 列表。
