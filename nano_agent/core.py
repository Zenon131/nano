import asyncio
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

from nano_agent.providers import ProviderSettings, OpenAICompatibleProvider

console = Console()

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
                if spec is None: continue
                module = importlib.util.module_from_spec(spec)
                if spec.loader: spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module):
                    if (inspect.iscoroutinefunction(obj) or inspect.isfunction(obj)) and not name.startswith("_"):
                        self.tools[name] = obj
                        self.schemas.append(self.generate_tool_schema(obj))
            except Exception as e:
                console.print(f"[red]Failed to load tool {file.name}: {e}[/red]")

    def generate_tool_schema(self, func: Callable) -> Dict[str, Any]:
        sig = inspect.signature(func)
        params = {n: {"type": "string"} for n in sig.parameters}
        return {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": func.__doc__ or "No description provided.",
                "parameters": {
                    "type": "object", 
                    "properties": params, 
                    "required": [n for n, p in sig.parameters.items() if p.default == inspect.Parameter.empty]
                },
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
        state = {str(f): f.read_text() for f in self.root_dir.rglob("*.py") if f.is_file()}
        self.history.insert(0, {"time": time.time(), "label": label, "state": state})
        self.history = self.history[:10]

    def restore(self, snapshot: Dict[str, Any]):
        for path, content in snapshot["state"].items():
            Path(path).write_text(content)

class SessionManager:
    def __init__(self, sessions_dir: str = "~/.nano/sessions"):
        self.path = Path(sessions_dir).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.current_session_file = self.path / "current.json"

    def save_session(self, messages: List[Dict[str, Any]], session_id: Optional[str] = None):
        with open(self.current_session_file, "w") as f:
            json.dump(messages, f, indent=2)
        if session_id:
            target = self.path / f"{session_id}.json"
            with open(target, "w") as f:
                json.dump(messages, f, indent=2)

    def load_session(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
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
        
        config = self._load_config()
        self.settings = ProviderSettings(config)
        self.provider = OpenAICompatibleProvider(self.settings)
        self.provider_name = self.settings.active
        self.model = self.settings.current["model"]
        
        self.messages = []
        self.snapshots = SnapshotManager(".")
        self.session = SessionManager()
        self.prompt_session: Any = None
        self.debug = False
        self.cwd = os.getcwd()
        
        self._register_core_tools()

    def _load_config(self):
        config_path = Path.home() / ".nano.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except:
                pass
        return {}

    def _register_core_tools(self):
        async def write_tool(registry, filename: str, code: str):
            if not filename.endswith(".py"): return "Error: filename must end in .py"
            path = registry.tools_dir / filename
            path.write_text(code)
            return f"Tool written to {path}. Call reload_tools() to activate."
        
        async def reload_tools(registry):
            registry.load_tools()
            return f"Reloaded {len(registry.tools)} tools."

        async def test_tool(registry, script: str):
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
                tf.write(script)
                tmp_path = tf.name
            try:
                res = subprocess.run(["python3", tmp_path], capture_output=True, text=True, timeout=10)
                return f"Passed: {res.stdout}" if res.returncode == 0 else f"Failed: {res.stderr}"
            finally:
                if os.path.exists(tmp_path): os.unlink(tmp_path)

        core_tools = {
            "write_tool": (write_tool, "Writes a new tool implementation to the tools directory.", True),
            "reload_tools": (reload_tools, "Reloads the tool registry.", False),
            "test_tool": (test_tool, "Executes a script to verify tool behavior.", True),
        }

        for name, (func, doc, unsafe) in core_tools.items():
            func.__doc__ = doc
            if unsafe:
                self.registry.unsafe_tools.add(name)
            self.registry.tools[name] = func
            self.registry.schemas.append(self.registry.generate_tool_schema(func))

    async def run(self, user_input: str):
        if user_input.startswith("/"): return await self._handle_command(user_input)
        
        if not self.messages or self.messages[0]["role"] != "system":
            sys_msg = f"You are Nano, a transparent, recursive, provider-agnostic AI agent shell. You run in a local terminal environment at {self.cwd}. Your goal is to be a helpful, high-agency assistant that uses tools to interact with the filesystem and execute code. You should always be transparent about your process and avoid generic 'AI assistant' canned responses. When asked about your location or environment, refer to your CWD: {self.cwd}."
            self.messages.insert(0, {"role": "system", "content": sys_msg})

        self.messages.append({"role": "user", "content": user_input})
        while True:
            if self.debug: print(f'DEBUG: Sending {len(self.messages)} messages to provider...')
            
            response = await self.provider.chat(self.messages, tools=self.registry.schemas)
            
            if self.debug: print(f'DEBUG: Provider responded: {response}')
            self.messages.append(response)
            
            if response.get('tool_calls'):
                for tc in response['tool_calls']:
                    name = tc['function']['name']
                    args = json.loads(tc['function']['arguments'])
                    schema = next((s for s in self.registry.schemas if s['function']['name'] == name), {})
                    
                    if schema.get('unsafe', False) and not self.auto_approve:
                        if self.confirm_callback and not await self.confirm_callback(name, args):
                            res = 'Error: Denied by user.'
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
            return "Nano Commands:\\n/undo - Restore last snapshot\\n/clear - Clear session\\n/auto - Toggle auto-approve\\n/save - Manual snapshot\\n/models - Model & Provider Picker\\n/status - System status\\n/debug - Toggle telemetry"
        
        if action == "/undo":
            if not self.snapshots.history: return "No snapshots available."
            self.snapshots.restore(self.snapshots.history[0])
            return "Workspace restored to last snapshot."
            
        if action == "/clear":
            self.messages = []
            self.session.clear_session()
            return "Session cleared."
            
        if action == "/auto":
            self.auto_approve = not self.auto_approve
            return f"Auto-approve is now {'enabled' if self.auto_approve else 'disabled'}."

        if action == "/save":
            self.snapshots.snapshot(label="Manual Save")
            return "Manual snapshot created."
            
        if action == "/status":
            return f"Status: Provider={self.provider_name}, Model={self.model}, Messages={len(self.messages)}"

        if action == "/debug":
            self.debug = not self.debug
            return f"Debug mode is now {'enabled' if self.debug else 'disabled'}."

        if action == "/models":
            if len(parts) > 1:
                target = parts[1]
                if "/" in target:
                    try:
                        prov_name, model_name = target.split("/", 1)
                        if prov_name in self.settings.profiles:
                            # 1. Update settings first
                            self.settings.active = prov_name
                            profile = self.settings.profiles[prov_name]
                            profile["model"] = model_name
                            if model_name not in profile["manual_models"]:
                                profile["manual_models"].append(model_name)
                            self.settings.save()
                            
                            # 2. Attempt to initialize provider
                            try:
                                self.provider = OpenAICompatibleProvider(self.settings)
                                self.provider_name = prov_name
                                self.model = model_name
                                return f"Switched to {prov_name} | {model_name}"
                            except Exception as e:
                                return f"Settings updated, but could not connect to provider: {e}"
                        else:
                            return f"Provider {prov_name} not found."
                    except Exception as e:
                        return f"Error switching: {e}"
                else:
                    model_name = target
                    profile = self.settings.profiles[self.settings.active]
                    profile["model"] = model_name
                    if model_name not in profile["manual_models"]:
                        profile["manual_models"].append(model_name)
                    self.settings.save()
                    try:
                        self.provider.model = model_name
                        self.model = model_name
                        return f"Switched to model: {model_name}"
                    except Exception as e:
                        return f"Model updated in config, but provider error: {e}"

            # Interactive picker logic
            models = await self.provider.list_models()
            console.print("\n[bold cyan]Model Picker[/bold cyan]")
            console.print(f"Current: [yellow]{self.model}[/yellow]")
            for i, m in enumerate(models, 1):
                console.print(f"{i}: {m}")
            console.print("\n[dim]Enter number to switch, '/p' to change provider, or 'q' to cancel[/dim]")
            
            choice = await self.prompt_session.prompt_async("❯ ")
            if choice.lower() == 'q': return "Cancelled."
            if choice.lower() == '/p': return await self._handle_command("/provider")
            
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(models):
                    new_model = models[idx]
                    profile = self.settings.profiles[self.settings.active]
                    profile["model"] = new_model
                    if new_model not in profile["manual_models"]:
                        profile["manual_models"].append(new_model)
                    self.provider.model = new_model
                    self.model = new_model
                    self.settings.save()
                    return f"Switched to model: {new_model}"
            except ValueError:
                if choice in models:
                    profile = self.settings.profiles[self.settings.active]
                    profile["model"] = choice
                    if choice not in profile["manual_models"]:
                        profile["manual_models"].append(choice)
                    self.provider.model = choice
                    self.model = choice
                    self.settings.save()
                    return f"Switched to model: {choice}"
            return "Invalid selection."



        if action == "/provider":
            providers = list(self.settings.profiles.keys())
            console.print("\\n[bold cyan]Provider Manager[/bold cyan]")
            console.print(f"Current: [yellow]{self.provider_name}[/yellow]")
            for i, p in enumerate(providers, 1):
                console.print(f"{i}: {p}")
            console.print("\\n[dim]Enter number to switch, '/a' to add provider, or 'q' to cancel[/dim]")
            
            choice = await self.prompt_session.prompt_async("❯ ")
            if choice.lower() == 'q': return "Cancelled."
            if choice.lower() == '/a':
                name = await self.prompt_session.prompt_async("Provider name: ")
                if not name: return "Name required."
                url = await self.prompt_session.prompt_async("Base URL: ")
                if not url: return "URL required."
                key = await self.prompt_session.prompt_async("API Key (optional): ")
                self.settings.profiles[name] = {
                    "base_url": url,
                    "api_key": key if key else None,
                    "api_key_env": None,
                    "model": "",
                    "manual_models": []
                }
                self.settings.active = name
                from nano_agent.providers import get_provider
                self.provider = OpenAICompatibleProvider(self.settings)
                self.provider_name = name
                self.model = self.settings.current["model"]
                self.settings.save()
                return f"Provider {name} added and activated. Please run /models to pick a model."

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(providers):
                    new_prov = providers[idx]
                    self.settings.active = new_prov
                    from nano_agent.providers import get_provider
                    self.provider = OpenAICompatibleProvider(self.settings)
                    self.provider_name = self.settings.active
                    self.model = self.settings.current["model"]
                    self.settings.save()
                    return f"Switched to provider: {new_prov}. Please run /models to pick a model."
            except ValueError:
                if choice in providers:
                    new_prov = choice
                    self.settings.active = new_prov
                    from nano_agent.providers import get_provider
                    self.provider = OpenAICompatibleProvider(self.settings)
                    self.provider_name = self.settings.active
                    self.model = self.settings.current["model"]
                    self.settings.save()
                    return f"Switched to provider: {new_prov}."
            return "Invalid selection."

        return f"Unknown command: {action}"
