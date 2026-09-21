"""工作区内路径的规范化与权限检查。"""  # 说明本文件负责阻止目录逃逸。

from __future__ import annotations  # 启用延迟解析类型标注。

from dataclasses import dataclass  # 导入轻量不可变数据类装饰器。
from pathlib import Path, PurePosixPath, PureWindowsPath  # 导入真实路径和跨平台纯路径。

from patchflow.domain.task import PathPolicy  # 导入任务声明的路径访问策略。
from patchflow.runtime.errors import PathViolationError  # 导入明确的路径违规异常。


@dataclass(frozen=True, slots=True)  # 使用不可变紧凑对象保存根目录和策略。
class WorkspacePathResolver:  # 定义工作区安全路径解析器。
    """把 Agent 提供的相对路径安全解析到工作区内部。"""  # 说明解析器的安全职责。

    root: Path  # 保存任务工作区根目录。
    policy: PathPolicy  # 保存允许、拒绝和只读路径策略。

    def __post_init__(self) -> None:  # 在数据类初始化后规范化根目录。
        resolved_root = self.root.resolve(strict=True)  # 解析根目录并要求其真实存在。
        if not resolved_root.is_dir():  # 验证根路径确实是目录。
            raise PathViolationError(f"工作区根路径不是目录：{resolved_root}")  # 拒绝文件根路径。
        object.__setattr__(self, "root", resolved_root)  # 在冻结对象中保存规范化绝对路径。

    def resolve(  # 定义对外路径解析入口。
        self,  # 接收当前解析器实例。
        raw_path: str,  # 接收 Agent 提供的原始相对路径。
        *,  # 强制后续安全选项使用关键字传递。
        write: bool = False,  # 标记本次访问是否会修改文件。
        must_exist: bool = True,  # 标记目标在解析时是否必须存在。
    ) -> Path:  # 返回经过全部检查的真实绝对路径。
        normalized = self._normalize_relative_path(raw_path)  # 先做平台无关的词法规范化。
        candidate = (self.root / normalized).resolve(strict=must_exist)  # 解析符号链接和真实父目录。
        self._ensure_inside_root(candidate)  # 阻止符号链接或路径组合逃出工作区。
        self._ensure_allowed(candidate)  # 确认目标位于至少一个允许路径中。
        self._ensure_not_denied(candidate)  # 确认目标未落入拒绝路径。
        if write:  # 仅写操作需要额外检查只读路径。
            self._ensure_writable(candidate)  # 拒绝对只读路径及其子路径写入。
        return candidate  # 返回最终可安全使用的真实路径。

    def relative_name(self, path: Path) -> str:  # 定义绝对路径转工作区相对名称的方法。
        resolved = path.resolve(strict=False)  # 规范化调用者提供的路径。
        self._ensure_inside_root(resolved)  # 确认转换目标仍位于工作区。
        return resolved.relative_to(self.root).as_posix()  # 使用稳定的 POSIX 风格保存相对路径。

    @staticmethod  # 声明该词法检查不依赖实例状态。
    def _normalize_relative_path(raw_path: str) -> str:  # 定义跨平台相对路径规范化逻辑。
        cleaned = raw_path.strip()  # 去除用户输入两端的无意义空白。
        if not cleaned:  # 检查空字符串。
            raise PathViolationError("路径不能为空")  # 拒绝无法表达目标的空路径。
        if "\x00" in cleaned:  # 检查文件 API 不允许的空字节。
            raise PathViolationError("路径不能包含空字节")  # 在进入 pathlib 前拒绝空字节。
        if PurePosixPath(cleaned).is_absolute() or PureWindowsPath(cleaned).is_absolute():  # 检查两类绝对路径。
            raise PathViolationError(f"只允许工作区相对路径：{raw_path}")  # 拒绝 Linux 和 Windows 绝对路径。
        normalized = cleaned.replace("\\", "/")  # 将 Windows 分隔符统一为正斜杠。
        parts = normalized.split("/")  # 拆分路径以检查显式父目录跳转。
        if ".." in parts:  # 查找任何父目录片段。
            raise PathViolationError(f"路径不允许包含父目录跳转：{raw_path}")  # 阻止词法目录逃逸。
        return normalized  # 返回可交给 Path 继续解析的相对路径。

    def _ensure_inside_root(self, candidate: Path) -> None:  # 定义真实路径边界检查。
        if not candidate.is_relative_to(self.root):  # 判断候选真实路径是否属于工作区。
            raise PathViolationError(f"路径逃出工作区：{candidate}")  # 拒绝符号链接等造成的越界。

    def _policy_paths(self, entries: tuple[str, ...]) -> tuple[Path, ...]:  # 解析策略中的相对路径集合。
        return tuple((self.root / entry).resolve(strict=False) for entry in entries)  # 得到每个策略路径的真实位置。

    def _ensure_allowed(self, candidate: Path) -> None:  # 定义允许列表检查。
        allowed_roots = self._policy_paths(self.policy.allowed_paths)  # 解析全部允许路径。
        if not any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots):  # 检查命中允许范围。
            raise PathViolationError(f"路径不在允许范围内：{candidate}")  # 拒绝未被显式允许的路径。

    def _ensure_not_denied(self, candidate: Path) -> None:  # 定义拒绝列表检查。
        denied_roots = self._policy_paths(self.policy.denied_paths)  # 解析全部拒绝路径。
        if any(candidate == root or candidate.is_relative_to(root) for root in denied_roots):  # 检查命中拒绝范围。
            raise PathViolationError(f"路径被访问策略禁止：{candidate}")  # 拒绝访问敏感目录及其后代。

    def _ensure_writable(self, candidate: Path) -> None:  # 定义只读路径写保护检查。
        read_only_roots = self._policy_paths(self.policy.read_only_paths)  # 解析全部只读路径。
        if any(candidate == root or candidate.is_relative_to(root) for root in read_only_roots):  # 检查写目标是否只读。
            raise PathViolationError(f"路径只允许读取：{candidate}")  # 拒绝对只读范围执行写操作。

