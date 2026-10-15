from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    handler: Callable[..., Any]
    fallback: Callable[..., Any] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self.last_fallback_used = False

    def register(
        self,
        *,
        name: str,
        description: str,
        handler: Callable[..., Any],
        fallback: Callable[..., Any] | None = None,
    ) -> None:
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            handler=handler,
            fallback=fallback,
        )

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        tool = self._tools[name]
        self.last_fallback_used = False
        try:
            return tool.handler(**kwargs)
        except Exception:
            if tool.fallback is None:
                raise
            self.last_fallback_used = True
            return tool.fallback(**kwargs)

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]
