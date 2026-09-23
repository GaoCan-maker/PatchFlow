"""第六周候选分支与并发验证的公共入口。"""  # 说明本包负责管理独立候选。

from patchflow.candidates.branching import (  # 导出候选分支编排器。
    BranchingConfig,  # 导出并发和候选数量配置。
    CandidateBranchingEngine,  # 导出候选创建、验证和选择引擎。
    CandidateBranchResult,  # 导出单个候选的最终报告。
    CandidateSelection,  # 导出确定性选择结果。
)  # 完成候选编排器导入。
from patchflow.candidates.workspaces import (  # 导出独立工作区组件。
    CandidateWorkspace,  # 导出单个候选工作区描述。
    CandidateWorkspaceManager,  # 导出候选工作区创建器。
)  # 完成工作区组件导入。

__all__ = [  # 声明第六周候选模块的稳定公共接口。
    "BranchingConfig",  # 暴露候选并发配置。
    "CandidateBranchResult",  # 暴露候选执行结果。
    "CandidateBranchingEngine",  # 暴露候选分支引擎。
    "CandidateSelection",  # 暴露最终选择结果。
    "CandidateWorkspace",  # 暴露工作区描述。
    "CandidateWorkspaceManager",  # 暴露工作区管理器。
]  # 完成公共接口声明。
