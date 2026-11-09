with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f: f.write("""import asyncio
import functools
import inspect
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from openai import AsyncOpenAI
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

console = Console()

class MockProvider:
    def __init__(self):
        self.call_count = 0
    async def chat(self, messages):
        self.call_count += 1
        if self.call_count == 1:
            return {
                'content': 'Using tool',
                'tool_calls': [{
                    'id': 'call_1',
                    'function': {'name': 'shell', 'arguments': '{"command": "ls"}'}
                }]
            }
        return {'content': 'Task complete.', 'tool_calls': []}

class ToolRegistry:
    def __init__(self, tools_dir: Optional[str] = None):
        self.tools_dir = Path(tools_dir) if tools_dir else Path("tools")
        self.tools: Dict[str, Callable] = {}
        self.schemas: List[Dict[str, Any]] = []
        self.unsafe_tools = set()

    def load_tools(self):
        self.tools.clear()
        self.schemas.clear()
        if not self.tools_dir.exists():
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            return

        for file in self.tools_dir.glob("*.py"):
            if file.name == "__init__.py": continue
            module_name = f"tools.{file.stem}"
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(module_name, file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module):
                    if (inspect.iscoroutinefunction(obj) or inspect.isfunction(obj)) and not name.startswith("_"):
                        self.tools[name] = obj
                        self.schemas.append(self.generate_tool_schema(obj))
            except Exception as e:
                console.print(f"[red]Failed to load tool {file.name}: {e}[/red]")

    def generate_tool_schema(self, func: Callable) -> Dict[str, Any]:
        sig = inspect.signature(func)
        params = {n: {"type": "string"} for n in sig.parameters} # Simplified
        return {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": func.__doc__ or "No description provided.",
                "parameters": {"type": "object", "properties": params, "required": [n for n, p in sig.parameters.items() if p.default == inspect.Parameter.empty]},
            },
            "unsafe": func.__name__ in self.unsafe_tools
        }

    async def execute(self, name: str, args: Dict[str, Any]) -> str:
        if name not in self.tools: return f"Error: Tool {name} not found."
        func = self.tools[name]
        try:
            return await func(self, **args) if inspect.iscoroutinefunction(func) else func(self, **args)
        except Exception as e: return f"Tool execution error: {str(e)}"

class SnapshotManager:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.history: List[Dict[str, Any]] = []

    def snapshot(self, label: str):
        state = {str(f): f.read_text() for f in self.root_dir.rglob("*.py")}
        self.history.insert(0, {"time": time.time(), "label": label, "state": state})
        self.history = self.history[:10]

    def restore(self, snapshot: Dict[str, Any]):
        for path, content in snapshot["state"].items(): Path(path).write_text(content)


class SessionManager:
    def __init__(self, sessions_dir: str = "~/.nano/sessions"):
        self.path = Path(sessions_dir).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.current_session_file = self.path / "current.json"

    def save_session(self, messages, session_id=None):
        import json
        with open(self.current_session_file, "w") as f:
            json.dump(messages, f, indent=2)
        if session_id:
            target = self.path / f"{session_id}.json"
            with open(target, "w") as f:
                json.dump(messages, f, indent=2)

    def load_session(self, session_id=None):
        import json
        target = self.path / f"{session_id}.json" if session_id else self.current_session_file
        if not target.exists(): return []
        try:
            with open(target, "r") as f: return json.load(f)
        except: return []

    def list_sessions(self):
        sessions = []
        for f in self.path.glob("*.json"):
            if f.name == "current.json": continue
            try:
                mtime = f.stat().st_mtime
                sessions.append({"id": f.stem, "date": mtime, "path": str(f)})
            except: continue
        return sorted(sessions, key=lambda x: x["date"], reverse=True)

    def clear_session(self):
        if self.current_session_file.exists():
            self.current_session_file.unlink()


class Nano:
    def __init__(self, tools_dir: Optional[str] = None, confirm_callback: Optional[Callable] = None, interactive=True, auto_approve=False):
        self.interactive = interactive
        self.auto_approve = auto_approve
        self.confirm_callback = confirm_callback
        self.registry = ToolRegistry(tools_dir)
        self.registry.load_tools()
        
        # Config Defaults
        self.base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1")
        self.model = "llama3"
        self.api_key = os.environ.get("OPENAI_API_KEY", "ollama")
        
        self.provider = MockProvider()
        self.messages = []
        self.snapshots = SnapshotManager(".")
        self.session = SessionManager()
        self.prompt_session = None
        self._register_core_tools()

    def _register_core_tools(self):
        """Built-in meta-tools for the agent."""
        async def write_tool(filename: str, code: str):
            if not filename.endswith(".py"): return "Error: filename must end in .py"
            path = self.registry.tools_dir / filename
            path.write_text(code)
            return f"Tool written to {path}. Call reload_tools() to activate."
        self.registry.unsafe_tools.add('write_tool')
        write_tool.__doc__ = "Writes a new tool implementation to the tools directory."
        
        async def reload_tools():
            self.registry.load_tools()
            return f"Reloaded {len(self.registry.tools)} tools."
        reload_tools.__doc__ = "Reloads the tool registry."

        async def test_tool(script: str):
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
                tf.write(script); tmp_path = tf.name
            try:
                res = subprocess.run(["python3", tmp_path], capture_output=True, text=True, timeout=10)
                return f"Passed: {res.stdout}" if res.returncode == 0 else f"Failed: {res.stderr}"
            finally: os.unlink(tmp_path)
        self.registry.unsafe_tools.add('test_tool')
        test_tool.__doc__ = "Executes a script to verify tool behavior."

        for f in [write_tool, reload_tools, test_tool]:
            self.registry.tools[f.__name__] = f
            self.registry.schemas.append(self.registry.generate_tool_schema(f))

    async def run(self, user_input: str):
        if user_input.startswith("/"): return await self._handle_command(user_input)
        self.messages.append({"role": "user", "content": user_input})
        while True:
            response = await self.provider.chat(self.messages)
            self.messages.append(response)
            
            if response.get('tool_calls'):
                for tc in response['tool_calls']:
                    name = tc['function']['name']
                    args = json.loads(tc['function']['arguments'])
                    schema = next((s for s in self.registry.schemas if s['function']['name'] == name), {})
                    if schema.get('unsafe', False) and not self.auto_approve:
                        if self.confirm_callback and not await self.confirm_callback(name, args):
                            res = 'Error: Denied.'
                        else:
                            self.snapshots.snapshot(label=name)
                            res = await self.registry.execute(name, args)
                    else:
                        res = await self.registry.execute(name, args)
                    self.messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'name': name, 'content': res})
                continue
            return response['content']

    
    async def _handle_command(self, cmd: str):
        parts = cmd.split()
        action = parts[0].lower()
        
        if action == "/help":
            return "Nano Commands:\n/undo - Restore last snapshot\n/clear - Clear session\n/auto - Toggle auto-approve\n/save - Manual snapshot"
        
        if action == "/undo":
            if not self.snapshots.history:
                return "No snapshots available."
            self.snapshots.restore(self.snapshots.history[0])
            return "Workspace restored to last snapshot."
            
        if action == "/clear":
            self.messages = []
            return "Session cleared."
            
        if action == "/auto":
            self.auto_approve = not self.auto_approve
            status = "enabled" if self.auto_approve else "disabled"
            return f"Auto-approve is now {status}."

        if action == "/save":
            self.snapshots.snapshot(label="Manual Save")
            return "Manual snapshot created."
            
        return f"Unknown command: {action}"
""")