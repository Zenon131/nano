import os
import json
import requests
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Union, Tuple
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.prompt import Prompt
from nano_agent.tools import ToolRegistry, generate_tool_schema
from dotenv import load_dotenv

load_dotenv()
console = Console()

class ContextImporter:
    """Discovers and imports history from other AI agent frameworks."""
    
    # Known high-confidence signatures: {agent_name: (path_pattern, schema_type)}
    SIGNATURES = {
        "Hermes": ("~/.hermes/**/*.json", "openai"), # Use glob for profiles
        "OpenCode": ("~/.opencode/history.json", "generic"),
        "OpenClaw": ("~/.openclaw/sessions", "generic"),
    }

    # Directories to scan for generalized history search
    SEARCH_DIRS = [
        Path("~/.config").expanduser(),
        Path("~/.local/share").expanduser(),
        Path("~/Library/Application Support").expanduser(),
        Path("~").expanduser(),
    ]

    # File patterns that typically indicate agent history
    FILE_PATTERNS = ["*history*.json", "*session*.json", ".nano_history", "history.json", "chat_*.json"]

    @staticmethod
    def normalize(messages: List[Dict], schema: str) -> List[Dict]:
        normalized = []
        for m in messages:
            if not isinstance(m, dict): continue
            if schema == "openai":
                normalized.append(m)
            elif schema == "generic":
                role = m.get("role") or ("user" if "user" in m else "assistant")
                content = m.get("content") or m.get("text") or str(m)
                normalized.append({"role": role, "content": content})
        return normalized

    def _is_chat_history(self, data: Any) -> bool:
        """Heuristic to determine if a JSON object is likely a chat history."""
        if not isinstance(data, list) or not data:
            return False
        
        # Chat-like keys to look for
        chat_keys = {"role", "content", "text", "message", "prompt", "response", "user", "assistant"}
        
        # Sample first few entries
        found_keys = set()
        for entry in data[:5]:
            if isinstance(entry, dict):
                found_keys.update(entry.keys())
        
        # If it contains at least a few of the common chat keys, it's a candidate
        return len(found_keys.intersection(chat_keys)) >= 1

    def _generalized_search(self) -> List[Tuple[str, Dict]]:
        discovered = []
        for directory in self.SEARCH_DIRS:
            if not directory.exists():
                continue
            
            try:
                # Use rglob to find patterns recursively but limit depth to avoid hanging
                for pattern in self.FILE_PATTERNS:
                    for path in directory.glob(pattern):
                        # Skip very large files to prevent memory issues
                        if path.stat().st_size > 10 * 1024 * 1024: continue
                        
                        try:
                            with open(path, "r") as f:
                                data = json.load(f)
                                if self._is_chat_history(data):
                                    # Label by the filename or parent folder
                                    source_name = f"Auto({path.name})"
                                    normalized = self.normalize(data, "generic")
                                    for msg in normalized:
                                        discovered.append((source_name, msg))
                        except Exception:
                            continue
            except Exception:
                continue
        return discovered

    def discover_and_import(self) -> List[Tuple[str, Dict]]:
        imported_messages = []
        
        # 1. Check known signatures first (high confidence)
        for agent, (pattern, schema) in self.SIGNATURES.items():
            # Handle glob patterns in signatures
            if "*" in pattern:
                paths = list(Path(pattern).expanduser().glob("**/*.json"))
                for path in paths:
                    try:
                        with open(path, "r") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                normalized = self.normalize(data, schema)
                                for msg in normalized:
                                    imported_messages.append((agent, msg))
                    except Exception:
                        continue
            else:
                path = Path(pattern).expanduser()
                if path.exists():
                    try:
                        with open(path, "r") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                normalized = self.normalize(data, schema)
                                for msg in normalized:
                                    imported_messages.append((agent, msg))
                    except Exception:
                        continue
        
        # 2. Run generalized search for unknown agents
        imported_messages.extend(self._generalized_search())
        
        return imported_messages

class SessionManager:
    def __init__(self, sessions_dir: str = "~/.nano/sessions"):
        self.path = Path(sessions_dir).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.current_session_file = self.path / "current.json"

    def save_session(self, messages: List[Dict[str, Any]], session_id: Optional[str] = None):
        # Always save to the current session pointer
        with open(self.current_session_file, "w") as f:
            json.dump(messages, f, indent=2)
        
        # If a specific ID is provided (or we want to archive), save to a timestamped file
        if session_id:
            target = self.path / f"{session_id}.json"
            with open(target, "w") as f:
                json.dump(messages, f, indent=2)

    def load_session(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        target = self.path / f"{session_id}.json" if session_id else self.current_session_file
        if not target.exists():
            return []
        try:
            with open(target, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        for f in self.path.glob("*.json"):
            if f.name == "current.json": continue
            try:
                # Use file mtime as a proxy for session date
                mtime = f.stat().st_mtime
                sessions.append({
                    "id": f.stem, 
                    "date": mtime, 
                    "path": str(f)
                })
            except Exception:
                continue
        # Sort by newest first
        return sorted(sessions, key=lambda x: x["date"], reverse=True)

    def clear_session(self):
        if self.current_session_file.exists():
            self.current_session_file.unlink()

class Nano:
    def __init__(self, tools_dir: Optional[str] = None, model: str = "gpt-4o", 
                 confirm_callback: Optional[Callable[[str, Dict], bool]] = None,
                 is_subagent: bool = False):
        
        config = self._load_config()
        self.model = config.get("model", model)
        
        project_prompt = config.get("system_prompt", "You are Nano, a transparent terminal AI agent.")
        
        self.system_prompt = (
            f"{project_prompt}\n\n"
            "IDENTITY: You are a Terminal AI Agent. You do NOT chat about your limitations. "
            "You execute tasks using tools. If a tool exists for a task, you MUST use it. "
            "NEVER ask the user to run a command and paste the output if you have a tool for it.\n\n"
            "TOOL HIERARCHY:\n"
            "1. Use basic tools (filesystem, shell) for all direct requests.\n"
            "2. Use spawn_sub_agent ONLY for multi-step complex reasoning.\n"
            "3. You are strictly forbidden from telling the user you cannot see the filesystem."
        )
        
        self.base_url = config.get("base_url") 
        self.api_key = config.get("api_key", os.getenv("OPENAI_API_KEY", "ollama"))
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        package_root = Path(__file__).parent.parent
        if tools_dir is None:
            tools_dir = str(package_root / "tools")
        
        config_tools_dir = config.get("tools_dir")
        if config_tools_dir:
            config_path = Path(config_tools_dir)
            if not config_path.is_absolute():
                final_tools_dir = str(package_root / config_path)
            else:
                final_tools_dir = config_tools_dir
        else:
            final_tools_dir = tools_dir
        
        self.registry = ToolRegistry(final_tools_dir)
        self.registry.load_tools()
        self._register_core_tools()
        
        self.registry.schemas.sort(key=lambda x: x.get("priority", 1))
        
        self.debug_enabled = False
        self.session = SessionManager()
        
        importer = ContextImporter()
        foreign_context_tuples = importer.discover_and_import()
        foreign_context = [msg for agent, msg in foreign_context_tuples]
        
        self.imported_context = foreign_context_tuples
        self.messages = self.session.load_session()
        self.confirm_callback = confirm_callback
        
        if not self.messages and foreign_context:
            console.print(Panel(Text(f"Imported context from other agents ({len(foreign_context)} messages).", style="dim"), title="Context Import"))
            self.messages = foreign_context

        if self.messages:
            self.messages[0] = {"role": "system", "content": self.system_prompt}
        else:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        
        if is_subagent:
            self.session = None 

    def _load_config(self) -> Dict[str, Any]:
        config_path = Path(".nano.json")
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_config(self, updates: Dict[str, Any]):
        config = self._load_config()
        config.update(updates)
        with open(".nano.json", "w") as f:
            json.dump(config, f, indent=2)

    def _get_ollama_models(self) -> List[str]:
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            if response.status_code == 200:
                data = response.json()
                return [m['name'] for m in data.get('models', [])]
        except Exception:
            pass
        return []

    def _handle_model_selection(self):
        ollama_models = self._get_ollama_models()
        config = self._load_config()
        manual_models = config.get("manual_models", [])
        
        all_options = []
        for m in ollama_models: all_options.append({"name": m, "provider": "Ollama (Local)"})
        for m in manual_models: all_options.append({"name": m, "provider": "Manual/Custom"})
        
        if not any(o["name"] == self.model for o in all_options):
            all_options.append({"name": self.model, "provider": "Current"})

        if not all_options:
            console.print("[bold red]No models found.[/bold red] Use /config to add manual models.")
            return

        table = Table(title="Select AI Model", expand=True, border_style="magenta")
        table.add_column("ID", justify="right", style="cyan", no_wrap=True)
        table.add_column("Model Name", style="white")
        table.add_column("Provider", style="dim")

        for idx, opt in enumerate(all_options, 1):
            marker = "⭐" if opt["name"] == self.model else ""
            table.add_row(str(idx), f"{marker} {opt['name']}", opt["provider"])

        console.print(Panel(table, title="Model Selector", border_style="magenta"))
        choice = Prompt.ask("[bold magenta]Select a model number[/bold magenta]")
        
        try:
            idx = int(choice) - 1
            selected = all_options[idx]
            self.model = selected["name"]
            updates = {"model": self.model}
            if selected["provider"] == "Ollama (Local)":
                updates["base_url"] = "http://localhost:11434/v1"
                updates["api_key"] = "ollama"
            self._save_config(updates)
            new_config = self._load_config()
            self.api_key = new_config.get("api_key", "ollama")
            self.base_url = new_config.get("base_url")
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            console.print(f"\\n[bold green]✓ Model switched to {self.model}[/bold green]\\n")
        except (ValueError, IndexError):
            console.print("[bold red]Invalid selection.[/bold red]")

    def _handle_api_config(self):
        console.print(Panel("[bold magenta]API Configuration[/bold magenta]", style="magenta"))
        action = Prompt.ask("What would you like to update?", choices=["url", "key", "model", "back"], default="url")
        if action == "url":
            new_url = Prompt.ask("[bold cyan]Enter Base API URL (e.g. http://localhost:11434/v1)[/bold cyan]")
            self._save_config({"base_url": new_url})
            self.base_url = new_url
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            console.print("[bold green]Base URL updated![/bold green]")
        elif action == "key":
            new_key = Prompt.ask("[bold cyan]Enter your API Key[/bold cyan]")
            self._save_config({"api_key": new_key})
            self.api_key = new_key
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            console.print("[bold green]API Key updated![/bold green]")
        elif action == "model":
            new_model = Prompt.ask("[bold cyan]Enter model name (e.g. gpt-4-turbo)[/bold cyan]")
            config = self._load_config()
            manual = config.get("manual_models", [])
            if new_model not in manual:
                manual.append(new_model)
                self._save_config({"manual_models": manual})
            console.print(f"[bold green]Model {new_model} added to list![/bold green]")

    def _register_core_tools(self):
        async def spawn_sub_agent(goal: str):
            if not hasattr(self, 'session') or self.session is None:
                return "Error: Sub-agents cannot spawn further sub-agents to prevent recursive loops."
            sub = Nano(tools_dir=self.registry.tools_dir, model=self.model, is_subagent=True)
            sub.messages[0]["content"] = f"You are a Nano sub-agent. Your specific goal is: {goal}. Provide a final answer when complete."
            return await sub.run(goal)
        
        spawn_sub_agent.__doc__ = "Spawns a specialized sub-agent for complex multi-step tasks. DO NOT use for simple direct actions."
        self.registry.tools["spawn_sub_agent"] = spawn_sub_agent
        self.registry.schemas.append(generate_tool_schema(spawn_sub_agent, priority=3))

        async def ask_user(question: str):
            console.print(Panel(Text(f"Agent is asking: {question}", style="bold yellow"), title="User Query"))
            return Prompt.ask("[bold blue]Your Answer[/bold blue]")
        
        ask_user.__doc__ = "Use this tool to ask the user for clarification, missing information, or a decision."
        self.registry.tools["ask_user"] = ask_user
        self.registry.schemas.append(generate_tool_schema(ask_user, priority=1))

    async def run(self, user_input: str):
        user_input_strip = user_input.strip()
        if user_input_strip == "/help":
            help_table = Table(title="Nano Slash Commands", expand=True, border_style="magenta")
            help_table.add_column("Command", style="cyan", no_wrap=True)
            help_table.add_column("Description", style="white")
            help_table.add_row("/help", "Show this list of available slash commands")
            help_table.add_row("/save <name>", "Archive current session with a name")
            help_table.add_row("/sessions", "List and switch between past sessions")
            help_table.add_row("/context", "View context imported from other agents")
            help_table.add_row("/model", "Select or switch the active AI model")
            help_table.add_row("/config", "Configure API base URL and API key")
            help_table.add_row("/debug", "Toggle the 'Tool Calls & Telemetry' window")
            console.print(Panel(help_table, border_style="magenta"))
            return "Help menu displayed."
        if user_input_strip.startswith("/save "):
            name = user_input_strip[6:].strip()
            if not name:
                console.print("[bold red]Error: Please provide a session name. (e.g. /save project-x)[/bold red]")
                return "Save failed: missing name."
            
            if self.session:
                self.session.save_session(self.messages, session_id=name)
                console.print(f"[bold green]✓ Session archived as {name}[/bold green]")
                return f"Session saved as {name}."
            else:
                console.print("[bold red]Error: Session manager not available.[/bold red]")
                return "Save failed."
        if user_input_strip == "/sessions":
            if self.session is None:
                console.print("[bold red]Session manager not available (sub-agent mode).[/bold red]")
                return "Session management is disabled for sub-agents."
            
            sessions = self.session.list_sessions()
            if not sessions:
                console.print("[dim]No archived sessions found.[/dim]")
                return "No archived sessions."
            
            sess_table = Table(title="Past Sessions", expand=True, border_style="magenta")
            sess_table.add_column("ID", style="cyan", no_wrap=True)
            sess_table.add_column("Date", style="dim")
            
            for s in sessions:
                from datetime import datetime
                date_str = datetime.fromtimestamp(s["date"]).strftime("%Y-%m-%d %H:%M")
                sess_table.add_row(s["id"], date_str)
            
            console.print(Panel(sess_table, border_style="magenta"))
            choice = Prompt.ask("\n[bold magenta]Enter Session ID to load (or press Enter to cancel)[/bold magenta]")
            
            if choice:
                # Save current state before switching
                self.session.save_session(self.messages, session_id=f"backup_{int(asyncio.get_event_loop().time())}")
                
                new_messages = self.session.load_session(session_id=choice)
                if new_messages:
                    self.messages = new_messages
                    # Ensure system prompt is preserved
                    self.messages[0] = {"role": "system", "content": self.system_prompt}
                    console.print(f"[bold green]✓ Loaded session {choice}[/bold green]")
                    return f"Switched to session {choice}."
                else:
                    console.print("[bold red]Failed to load session.[/bold red]")
                    return "Session load failed."
            return "Session switch cancelled."
        if user_input_strip == "/context":
            if not self.imported_context:
                console.print("[dim]No imported context found.[/dim]")
                return "No imported context."
            
            ctx_table = Table(title="Imported Context", expand=True, border_style="magenta")
            ctx_table.add_column("Source", style="cyan", width=12)
            ctx_table.add_column("Role", style="magenta", width=10)
            ctx_table.add_column("Content", style="white")
            
            for source, msg in self.imported_context:
                content = msg.get("content", "")
                # Truncate long messages for the table
                display_content = (content[:100] + "...") if len(content) > 100 else content
                ctx_table.add_row(source, msg.get("role", "unknown"), display_content)
            
            console.print(Panel(ctx_table, border_style="magenta"))
            return f"Displayed {len(self.imported_context)} imported messages."
        if user_input_strip == "/model":
            self._handle_model_selection()
            return "Model selection complete."
        if user_input_strip == "/config":
            self._handle_api_config()
            return "Configuration updated."
        if user_input_strip == "/debug":
            self.debug_enabled = not self.debug_enabled
            status = "enabled" if self.debug_enabled else "disabled"
            return f"Debug telemetry {status}."
        self.messages.append({"role": "user", "content": user_input})
        
        while True:
            if self.debug_enabled:
                console.print(Panel(Text(json.dumps({
                    "model": self.model,
                    "messages": self.messages,
                    "tools": self.registry.schemas
                }, indent=2), style="dim"), title="Tool Calls & Telemetry"))

            # Use a type-cast for messages to satisfy the OpenAI SDK's strict type checking
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages, # type: ignore
                tools=self.registry.schemas,
                tool_choice="auto",
                stream=True
            )
            full_content = ""
            tool_calls = []
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    console.print(delta.content, end="")
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        if tc_delta.index is None: continue
                        while len(tool_calls) <= tc_delta.index:
                            tool_calls.append({"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                        tc = tool_calls[tc_delta.index]
                        if tc_delta.id: tc["id"] = tc_delta.id
                        if tc_delta.function.name: tc["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments: tc["function"]["arguments"] += tc_delta.function.arguments
            if full_content:
                console.print()
            msg_obj = {"role": "assistant", "content": full_content or None}
            if tool_calls:
                msg_obj["tool_calls"] = tool_calls
            self.messages.append(msg_obj)
            if tool_calls:
                tasks = []
                for tc in tool_calls:
                    tasks.append(self._execute_tool_wrapper(tc))
                
                tool_results = await asyncio.gather(*tasks)
                
                for tc, result in zip(tool_calls, tool_results):
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"], "content": result})
                
                if self.session:
                    self.session.save_session(self.messages)
                continue 
            else:
                if self.session:
                    self.session.save_session(self.messages)
                return full_content

    async def _execute_tool_wrapper(self, tc: Dict[str, Any]) -> str:
        name = tc["function"]["name"]
        args_str = tc["function"]["arguments"]
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}
            
        schema = next((s for s in self.registry.schemas if s["function"]["name"] == name), {})
        is_unsafe = schema.get("unsafe", False)
        
        if is_unsafe and self.confirm_callback:
            if not self.confirm_callback(name, args):
                console.print(Panel(Text("User denied execution.", style="red"), title="Action Cancelled"))
                return "Error: User denied execution of this tool."
        
        return await self._execute_tool(name, args)

    async def _execute_tool(self, name: str, args: dict) -> str:
        console.print(Panel(Text(f"[bold cyan]Tool Call:[/bold cyan] {name}({args})", style="cyan"), title="Executing Tool"))
        result = await self.registry.execute(name, args)
        console.print(Panel(Text(result, style="green"), title="Tool Result"))
        return result
