"""任务、预算和路径策略模型。"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from patchflow.domain.enums import RepositoryKind, TaskSource


class FrozenModel(BaseModel):
    """不可变且拒绝未知字段的领域输入基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Budget(FrozenModel):
    """一次 Agent 任务可消耗的硬预算上限。"""

    max_agent_steps: int = Field(default=40, ge=1)
    max_model_calls: int = Field(default=30, ge=1)
    max_input_tokens: int = Field(default=200_000, ge=1)
    max_output_tokens: int = Field(default=40_000, ge=1)
    max_tool_calls: int = Field(default=80, ge=1)
    max_command_seconds: float = Field(default=1_800.0, gt=0)
    max_wall_clock_seconds: float = Field(default=3_600.0, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)


class PathPolicy(FrozenModel):
    """声明 Agent 对任务工作区内路径的访问规则。

    这里只校验策略表达形式；真实路径解析、符号链接检查和目录逃逸防护由
    下一阶段的 Runtime 实现。
    """

    allowed_paths: tuple[str, ...] = (".",)
    denied_paths: tuple[str, ...] = (".git",)
    read_only_paths: tuple[str, ...] = ()

    @field_validator("allowed_paths", "denied_paths", "read_only_paths")
    @classmethod
    def validate_relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        """拒绝绝对路径、空路径和显式父目录跳转。"""

        for raw_path in paths:
            path = raw_path.strip()
            if not path:
                raise ValueError("路径策略中不能包含空路径")
            if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
                raise ValueError(f"路径策略只允许工作区相对路径：{raw_path}")
            normalized_parts = path.replace("\\", "/").split("/")
            if ".." in normalized_parts:
                raise ValueError(f"路径策略不允许父目录跳转：{raw_path}")
        return paths


class RepositorySpec(FrozenModel):
    """描述任务仓库或预构建运行镜像的位置。"""

    kind: RepositoryKind
    location: str = Field(min_length=1)
    subdirectory: str | None = None

    @field_validator("location")
    @classmethod
    def location_must_not_be_blank(cls, value: str) -> str:
        """剔除首尾空格并拒绝只有空白的仓库位置。"""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("仓库位置不能为空")
        return cleaned

    @field_validator("subdirectory")
    @classmethod
    def validate_subdirectory(cls, value: str | None) -> str | None:
        """仓库子目录必须是安全的相对路径表达。"""

        if value is None:
            return None
        cleaned = value.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("仓库子目录不能为空字符串")
        if PurePosixPath(cleaned).is_absolute() or ".." in cleaned.split("/"):
            raise ValueError("仓库子目录必须位于仓库内部")
        return cleaned


class TaskSpec(FrozenModel):
    """统一描述本地、MicroSWE 和 SWE-bench 任务。"""

    task_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")
    source: TaskSource
    repo_spec: RepositorySpec
    base_commit: str = Field(min_length=1)
    problem_statement: str = Field(min_length=10)
    language: str = Field(default="python", min_length=1)
    public_commands: tuple[str, ...] = ()
    path_policy: PathPolicy = Field(default_factory=PathPolicy)
    budget: Budget = Field(default_factory=Budget)
    tags: frozenset[str] = frozenset()
    evaluation_ref: str | None = None

    @field_validator("base_commit", "problem_statement", "language")
    @classmethod
    def text_fields_must_not_be_blank(cls, value: str) -> str:
        """避免只包含空白字符的文本绕过最小长度检查。"""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("文本字段不能为空")
        return cleaned

    @field_validator("public_commands")
    @classmethod
    def commands_must_not_be_blank(cls, commands: tuple[str, ...]) -> tuple[str, ...]:
        """公开命令提示允许为空集合，但集合中的命令不能是空字符串。"""

        if any(not command.strip() for command in commands):
            raise ValueError("公开命令不能包含空字符串")
        return tuple(command.strip() for command in commands)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: frozenset[str]) -> frozenset[str]:
        """统一标签格式，便于后续分层采样和聚合统计。"""

        normalized = frozenset(tag.strip().lower() for tag in tags if tag.strip())
        if len(normalized) != len(tags):
            raise ValueError("标签不能为空或在规范化后重复")
        return normalized

    @model_validator(mode="after")
    def validate_source_and_repository(self) -> Self:
        """阻止明显矛盾的任务来源与仓库定位组合。"""

        if self.source is TaskSource.LOCAL and self.repo_spec.kind is not RepositoryKind.LOCAL:
            raise ValueError("本地任务必须使用本地仓库")
        return self

