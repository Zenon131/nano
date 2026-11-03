import inspect
import importlib.util
import asyncio
from pathlib import Path
from typing import get_type_hints, Any, Callable, Dict, Union

def generate_tool_schema(func: Callable, priority: int = 1) -> Dict[str, Any]:
    """
    Converts a Python function into an OpenAI-compatible tool schema.
    Priority: 1 = Basic (Preferred), 2 = Advanced, 3 = Meta (Last Resort).
    """
    name = func.__name__
    doc = inspect.getdoc(func) or "No description provided."
    
    hints = get_type_hints(func)
    params = {}
    sig = inspect.signature(func)
    
    for param_name, param in sig.parameters.items():
        py_type = hints.get(param_name, Any)
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
            Any: "string"
        }
        json_type = type_map.get(py_type, "string")
        params[param_name] = {
            "type": json_type,
            "description": f"The {param_name} parameter"
        }
        if param.default is not inspect.Parameter.empty:
            params[param_name]["default"] = param.default

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": [n for n, p in sig.parameters.items() if p.default is inspect.Parameter.empty]
            }
        },
        "priority": priority,
        "unsafe": getattr(func, "unsafe", False)
    }

class ToolRegistry:
    def __init__(self, tools_dir: str):
        # Force absolute resolution to prevent path-drift during global execution
        self.tools_dir = Path(tools_dir).resolve()
        self.tools: Dict[str, Callable] = {}
        self.schemas: list = []

    def load_tools(self):
        """Dynamically loads Python functions from the tools directory."""
        if not self.tools_dir.exists() or not self.tools_dir.is_dir():
            raise FileNotFoundError(f"CRITICAL: Tools directory not found at {self.tools_dir}")

        files = list(self.tools_dir.glob("*.py"))
        files = [f for f in files if f.name != "__init__.py"]

        if not files:
            raise FileNotFoundError(f"CRITICAL: No tool files found in {self.tools_dir}")

        for file in files:
            spec = importlib.util.spec_from_file_location(file.stem, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if obj.__module__ == module.__name__:
                    self.tools[name] = obj
                    self.schemas.append(generate_tool_schema(obj, priority=1))

    async def execute(self, name: str, args: dict) -> str:
        if name not in self.tools:
            return f"Error: Tool {name} not found."
        
        func = self.tools[name]
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                # Run sync functions in a thread to avoid blocking the event loop
                result = await asyncio.to_thread(func, **args)
            return str(result)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
