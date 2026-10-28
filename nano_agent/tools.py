import inspect
import importlib.util
from pathlib import Path
from typing import get_type_hints, Any, Callable, Dict

def generate_tool_schema(func: Callable, priority: int = 1) -> Dict[str, Any]:
    """
    Converts a Python function into an OpenAI-compatible tool schema.
    Priority: 1 = Basic (Preferred), 2 = Advanced, 3 = Meta (Last Resort).
    """
    name = func.__name__
    doc = inspect.getdoc(func) or "No description provided."
    
    # Add priority weighting to the description to guide the LLM
    priority_map = {
        1: "[PRIORITY: HIGH - Use this for simple, direct actions]",
        2: "[PRIORITY: MEDIUM - Use this for complex operations]",
        3: "[PRIORITY: LOW - LAST RESORT - Use only if no other tool suffices]"
    }
    enhanced_doc = f"{priority_map.get(priority, '')} {doc}"
    
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
            "description": enhanced_doc,
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
        self.tools_dir = Path(tools_dir)
        self.tools: Dict[str, Callable] = {}
        self.schemas: list = []

    def load_tools(self):
        """Dynamically loads Python functions from the tools directory."""
        for file in self.tools_dir.glob("*.py"):
            if file.name == "__init__.py":
                continue
            
            spec = importlib.util.spec_from_file_location(file.stem, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if obj.__module__ == module.__name__:
                    self.tools[name] = obj
                    # Default tools are priority 1 (Basic)
                    self.schemas.append(generate_tool_schema(obj, priority=1))

    def execute(self, name: str, args: dict) -> str:
        if name not in self.tools:
            return f"Error: Tool {name} not found."
        try:
            result = self.tools[name](**args)
            return str(result)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
