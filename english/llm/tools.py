import json
from typing import Dict, Any, Callable


class ToolRegistry:
    """Registry for available tools."""

    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.definitions: list = []

    def register(self, name: str, description: str, parameters: dict):
        """Decorator to register a tool."""
        def decorator(func: Callable):
            self.tools[name] = func
            self.definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            })
            return func
        return decorator

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool by name with given arguments."""
        if name not in self.tools:
            return f"Error: Tool '{name}' not found"

        try:
            result = await self.tools[name](**arguments)
            return result
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"

    def get_definitions(self) -> list:
        """Get OpenAI-compatible tool definitions."""
        return self.definitions


# Global tool registry
registry = ToolRegistry()

# Add your tools here using @registry.register() decorator
