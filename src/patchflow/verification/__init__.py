"""第六周验证金字塔的公共接口。"""  # 说明本包负责候选补丁确定性验证。

from patchflow.verification.pyramid import (  # 导出验证金字塔类型。
    CandidateVerification,  # 导出一个候选的完整验证报告。
    VerificationLevel,  # 导出验证层级枚举。
    VerificationPlan,  # 导出可配置的验证计划。
    VerificationPyramid,  # 导出顺序执行验证器。
)  # 完成验证类型导入。

__all__ = [  # 声明验证模块的稳定公共接口。
    "CandidateVerification",  # 暴露候选验证报告。
    "VerificationLevel",  # 暴露验证层级。
    "VerificationPlan",  # 暴露验证计划。
    "VerificationPyramid",  # 暴露验证器。
]  # 完成公共接口声明。
