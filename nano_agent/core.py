import os
import json
import copy
import re
import tempfile
from contextlib import contextmanager
import requests
import asyncio
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Callable, Union, Tuple
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.prompt import Prompt, Confirm
from nano_agent.tools import ToolRegistry, generate_tool_schema
from nano_agent.providers import ProviderSettings, infer_legacy_provider
from dotenv import load_dotenv

load_dotenv()
console = Console()


@contextmanager
def _provider_config_lock():
    """Lock a stable sidecar inode across Nano processes and atomic replaces."""
    fd = os.open(".nano.json.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            # Windows can lock a byte beyond EOF; keep the sidecar in place.
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _provider_config_snapshot(config: Dict[str, Any]) -> Dict[str, Any]:
    fields = ("active_provider", "providers", "model", "base_url", "api_key", "manual_models")
    return {key: copy.deepcopy(config[key]) for key in fields if key in config}


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
                 is_subagent: bool = False, provider_config: Optional[Dict[str, Any]] = None):
        
        config = self._load_config()
        self._provider_snapshot = _provider_config_snapshot(config)
        self.provider_settings = ProviderSettings(provider_config if provider_config is not None else config, default_model=model)
        self._apply_provider()
        
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
        self.session = None if is_subagent else SessionManager()

        # A delegated agent gets only its explicit goal, never the user's saved
        # or automatically discovered conversations.
        importer = ContextImporter()
        foreign_context_tuples = importer.discover_and_import() if not is_subagent else []
        foreign_context = [msg for agent, msg in foreign_context_tuples]

        self.imported_context = foreign_context_tuples
        self.messages = self.session.load_session() if self.session is not None else []
        self.confirm_callback = confirm_callback
        
        if not self.messages and foreign_context:
            console.print(Panel(Text(f"Imported context from other agents ({len(foreign_context)} messages).", style="dim"), title="Context Import"))
            self.messages = foreign_context

        if self.messages:
            self.messages[0] = {"role": "system", "content": self.system_prompt}
        else:
            self.messages = [{"role": "system", "content": self.system_prompt}]

    def _load_config(self) -> Dict[str, Any]:
        config_path = Path(".nano.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Cannot read Nano configuration; fix .nano.json before continuing. No settings were changed.") from error
        if not isinstance(config, dict):
            raise ValueError("Nano configuration must be a JSON object. No settings were changed.")
        return config

    def _apply_provider(self):
        profile = self.provider_settings.current
        self.provider = self.provider_settings.active
        self.model = profile.get("model") or ""
        self.base_url = profile.get("base_url") or None
        self.api_key = self.provider_settings.credential()
        # A placeholder lets users configure a provider before its key is set.
        # run() refuses to send requests while the key is missing.
        self.client = OpenAI(api_key=self.api_key or "not-configured", base_url=self.base_url)

    def _save_provider_settings(self):
        def reject_symlink():
            if Path(".nano.json").is_symlink():
                raise ValueError("The .nano.json config is a symlink; use a regular file before changing provider settings. No settings were changed.")

        # Lock a sidecar rather than the config inode, which is replaced below.
        # Compare while holding the lock so two processes cannot both commit
        # from the same stale snapshot.
        with _provider_config_lock():
            reject_symlink()
            config = self._load_config()
            if _provider_config_snapshot(config) != self._provider_snapshot:
                raise ValueError("Provider settings changed in another Nano instance; restart Nano before changing them. No settings were changed.")
            # Migrate legacy fields only when a provider setting is changed.
            for field in ("model", "base_url", "api_key", "manual_models"):
                config.pop(field, None)
            config.update(self.provider_settings.serialized())
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=".", prefix=".nano-config-", delete=False) as f:
                    temporary_path = Path(f.name)
                    json.dump(config, f, indent=2)
                # The sidecar serializes Nano writers only; other filesystem
                # writers can still race this check and the atomic replace.
                reject_symlink()
                os.replace(temporary_path, ".nano.json")
                self._provider_snapshot = _provider_config_snapshot(config)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()

    def _commit_provider_settings(self, candidate: ProviderSettings):
        previous = self.provider_settings
        self.provider_settings = candidate
        try:
            self._apply_provider()
            self._save_provider_settings()
        except Exception:
            self.provider_settings = previous
            self._apply_provider()
            raise

    def _select_provider(self, name: str):
        if name not in self.provider_settings.profiles:
            console.print(f"[red]Unknown provider: {name}. Use /provider to see options.[/red]")
            return
        target_url = self.provider_settings.profiles[name].get("base_url")
        if target_url and target_url != self.base_url:
            host = urlparse(target_url).hostname or "unknown host"
            console.print("[yellow]Future requests may send the current conversation, system prompt and tool results to the new endpoint.[/yellow]")
            if not Confirm.ask(f"Switch to {name} on {host}?", default=False):
                console.print("[dim]Provider switch cancelled.[/dim]")
                return
        candidate = copy.deepcopy(self.provider_settings)
        candidate.active = name
        self._commit_provider_settings(candidate)
        console.print(f"[green]Provider: {name}[/green] — endpoint: {self.base_url or '(not configured)'}")
        if not self.model:
            console.print("[yellow]Choose a model with /model or /model <model-id>.[/yellow]")
        if not self.api_key:
            env_name = self.provider_settings.current.get("api_key_env")
            console.print(f"[yellow]API key not set. Set {env_name or 'an API key environment variable'} before starting Nano.[/yellow]")

    def _handle_provider_selection(self):
        table = Table(title="Providers", expand=True, border_style="magenta")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Provider")
        table.add_column("Endpoint")
        names = list(self.provider_settings.profiles)
        for index, name in enumerate(names, 1):
            profile = self.provider_settings.profiles[name]
            marker = " (active)" if name == self.provider else ""
            table.add_row(str(index), f"{name}{marker}", str(profile.get("base_url") or "(not configured)"))
        console.print(Panel(table, border_style="magenta"))
        choice = Prompt.ask("Provider number or name (Enter to cancel)", default="")
        if not choice:
            return
        if choice.isdigit():
            index = int(choice) - 1
            if not 0 <= index < len(names):
                console.print("[red]Invalid provider selection.[/red]")
                return
            choice = names[index]
        self._select_provider(choice)

    def _get_ollama_models(self) -> List[str]:
        if self.provider != "ollama" or not self.base_url:
            return []
        try:
            base = self.base_url.rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            response = requests.get(base + "/api/tags", timeout=2)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(models, list):
                return []
            return [m["name"] for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return []

    def _select_model(self, name: str):
        if not name:
            console.print("[red]Provide a model ID: /model <model-id>[/red]")
            return
        candidate = copy.deepcopy(self.provider_settings)
        profile = candidate.current
        profile["model"] = name
        manual = profile.setdefault("manual_models", [])
        if name not in manual:
            manual.append(name)
        self._commit_provider_settings(candidate)
        console.print(f"[green]Model: {name} ({self.provider})[/green]")

    def _handle_model_selection(self):
        profile = self.provider_settings.current
        models = list(dict.fromkeys(
            self._get_ollama_models() + profile.get("manual_models", []) + ([self.model] if self.model else [])
        ))
        table = Table(title=f"Models — {self.provider}", expand=True, border_style="magenta")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Model")
        for index, name in enumerate(models, 1):
            marker = " (active)" if name == self.model else ""
            table.add_row(str(index), name + marker)
        console.print(Panel(table, border_style="magenta"))
        console.print("Enter a listed number or an exact model ID; Enter cancels.")
        choice = Prompt.ask("Model", default="")
        if not choice:
            return
        if choice.isdigit():
            index = int(choice) - 1
            if not 0 <= index < len(models):
                console.print("[red]Invalid model selection.[/red]")
                return
            choice = models[index]
        self._select_model(choice)

    def _handle_api_config(self):
        console.print(Panel(f"API Configuration — {self.provider}", style="magenta"))
        action = Prompt.ask("What would you like to update?", choices=["url", "key", "model", "back"], default="url")
        candidate = copy.deepcopy(self.provider_settings)
        profile = candidate.current
        if action == "url":
            new_url = Prompt.ask("Base API URL", default=str(self.base_url or ""))
            try:
                parsed = urlparse(new_url)
                _ = parsed.port  # Access validates numeric range and syntax.
            except ValueError:
                console.print("[red]Enter a valid http(s) API URL and port.[/red]")
                return
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                console.print("[red]Enter an http(s) API URL without credentials, query or fragment.[/red]")
                return
            target = infer_legacy_provider(new_url)
            if target != "custom" and target != self.provider:
                console.print(f"[yellow]That URL belongs to {target}. Use /provider {target} instead.[/yellow]")
                return
            if new_url != self.base_url:
                console.print("[yellow]Future requests may send your conversation and this provider's API key to the new endpoint.[/yellow]")
                if not Confirm.ask(f"Use endpoint on {parsed.hostname}?", default=False):
                    console.print("[dim]Endpoint change cancelled.[/dim]")
                    return
            profile["base_url"] = new_url
        elif action == "key":
            env_name = Prompt.ask("Environment variable containing the API key", default=profile.get("api_key_env") or "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                console.print("[red]Enter an environment variable name, not the secret itself.[/red]")
                return
            profile["api_key_env"] = env_name
            profile.pop("api_key", None)
            profile.pop("legacy_credentials", None)
            profile.pop("legacy_api_key_fallback", None)
        elif action == "model":
            self._select_model(Prompt.ask("Exact model ID", default=""))
            return
        else:
            return
        self._commit_provider_settings(candidate)
        if action == "key" and not self.api_key:
            console.print(f"[yellow]{profile.get('api_key_env')} is not set in Nano's process; set it and restart Nano.[/yellow]")
        else:
            console.print("[green]Provider configuration updated.[/green]")

    def _register_core_tools(self):
        async def spawn_sub_agent(goal: str):
            if not hasattr(self, 'session') or self.session is None:
                return "Error: Sub-agents cannot spawn further sub-agents to prevent recursive loops."
            sub = Nano(tools_dir=str(self.registry.tools_dir), model=self.model,
                       confirm_callback=self.confirm_callback, is_subagent=True,
                       provider_config=self.provider_settings.serialized())
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
        # Delegated goals are untrusted task text, never local UI commands.
        if self.session is None:
            return await self._run_goal(user_input)
        user_input_strip = user_input.strip()
        if user_input_strip == "/help":
            help_table = Table(title="Nano Slash Commands", expand=True, border_style="magenta")
            help_table.add_column("Command", style="cyan", no_wrap=True)
            help_table.add_column("Description", style="white")
            help_table.add_row("/help", "Show this list of available slash commands")
            help_table.add_row("/save <name>", "Archive current session with a name")
            help_table.add_row("/sessions", "List and switch between past sessions")
            help_table.add_row("/context", "View context imported from other agents")
            help_table.add_row("/provider [name]", "Select a provider and its saved settings")
            help_table.add_row("/model [model-id]", "Select a model for the active provider")
            help_table.add_row("/config", "Configure the active provider's URL and key environment variable")
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
        if user_input_strip == "/provider":
            self._handle_provider_selection()
            return "Provider selection complete."
        if user_input_strip.startswith("/provider "):
            self._select_provider(user_input_strip[len("/provider "):].strip())
            return "Provider selection complete."
        if user_input_strip == "/model":
            self._handle_model_selection()
            return "Model selection complete."
        if user_input_strip.startswith("/model "):
            self._select_model(user_input_strip[len("/model "):].strip())
            return "Model selection complete."
        if user_input_strip == "/config":
            self._handle_api_config()
            return "Configuration updated."
        if user_input_strip == "/debug":
            self.debug_enabled = not self.debug_enabled
            status = "enabled" if self.debug_enabled else "disabled"
            return f"Debug telemetry {status}."
        return await self._run_goal(user_input)

    async def _run_goal(self, user_input: str):
        if not self.base_url:
            console.print("[red]Set the active provider's endpoint with /config first.[/red]")
            return "Provider endpoint not configured."
        if not self.model:
            console.print("[red]Select a model with /model first.[/red]")
            return "Provider model not configured."
        if not self.api_key:
            env_name = self.provider_settings.current.get("api_key_env")
            console.print(f"[red]API key unavailable. Set {env_name or 'the configured key environment variable'} and restart Nano.[/red]")
            return "Provider API key not configured."
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
                tools=[{"type": s["type"], "function": s["function"]} for s in self.registry.schemas],
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
        
        if is_unsafe:
            if self.confirm_callback is None:
                return "Error: Unsafe tool requires a confirmation callback; execution denied."
            if not self.confirm_callback(name, args):
                console.print(Panel(Text("User denied execution.", style="red"), title="Action Cancelled"))
                return "Error: User denied execution of this tool."
        
        return await self._execute_tool(name, args)

    async def _execute_tool(self, name: str, args: dict) -> str:
        console.print(Panel(Text(f"[bold cyan]Tool Call:[/bold cyan] {name}({args})", style="cyan"), title="Executing Tool"))
        result = await self.registry.execute(name, args)
        console.print(Panel(Text(result, style="green"), title="Tool Result"))
        return result
