"""Validated executable registry extending NOVA's original metadata contract."""

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from app.computer.models import ComputerError
from app.tools.base import ToolDefinition


@dataclass
class RegisteredTool:
    definition: ToolDefinition
    arguments: type[BaseModel]
    capability: str
    summary: str


class ToolRegistry:
    def __init__(self): self.tools: dict[str, RegisteredTool] = {}
    def register(self, definition: ToolDefinition, arguments: type[BaseModel], *, capability: str, summary: str):
        if definition.name in self.tools: raise ValueError("Tool already registered")
        self.tools[definition.name]=RegisteredTool(definition,arguments,capability,summary)
    def get(self, name: str) -> RegisteredTool:
        if name not in self.tools: raise ComputerError("TOOL_UNAVAILABLE", "The requested computer tool is unavailable.")
        return self.tools[name]
    def validate(self, name: str, arguments: dict) -> dict:
        try: return self.get(name).arguments.model_validate(arguments).model_dump(exclude_none=True)
        except ValidationError as exc: raise ComputerError("ACTION_INVALID", "Tool arguments did not match the allowed schema.") from exc
    def catalog(self) -> list[dict]:
        def compact(value):
            if isinstance(value,dict):return {key:compact(item) for key,item in value.items() if key not in {"title","default"}}
            if isinstance(value,list):return [compact(item) for item in value]
            return value
        return [{"name":tool.definition.name,"description":tool.definition.description,"arguments":compact(tool.arguments.model_json_schema()),"permission":tool.definition.permission_level} for tool in self.tools.values()]
