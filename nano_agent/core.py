import os
import json
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
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

class SessionManager:
    def __init__(self, history_file: str = ".nano_history"):
        self.path = Path(history_file)

    def save_session(self, messages: List[Dict[str, Any]]):
        with open(self.path, "w") as f:
            json.dump(messages, f, indent=2)

    def load_session(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def clear_session(self):
        if self.path.exists():
            self.path.unlink()

class Nano:
    def __init__(self, tools_dir: str, model: str = "gpt-4o", 
                 confirm_callback: Optional[Callable[[str, Dict], bool]] = None,
                 is_subagent: bool = False):
        
        # 1. Project Config Load
        config = self._load_config()
        self.model = config.get("model", model)
        system_prompt = config.get("system_prompt", "You are Nano, a transparent terminal AI agent. Be concise and direct.")
        
        # Provider Configuration
        self.base_url = config.get("base_url") 
        self.api_key = config.get("api_key", os.getenv("OPENAI_API_KEY", "ollama"))
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        self.registry = ToolRegistry(config.get("tools_dir", tools_dir))
        self.registry.load_tools()
        self._register_core_tools()
        
        self.session = SessionManager()
        self.messages = self.session.load_session()
        self.confirm_callback = confirm_callback
        
        if not self.messages:
            self.messages = [{"role": "system", "content": system_prompt}]
        
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
        """Fetches available models from a local Ollama instance."""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            if response.status_code == 200:
                data = response.json()
                return [m['name'] for m in data.get('models', [])]
        except Exception:
            pass
        return []

    def _handle_model_selection(self):
        """Creates a professional selection menu for models."""
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
            
            console.print(f"\n[bold green]✓ Model switched to {self.model}[/bold green]\n")
        except (ValueError, IndexError):
            console.print("[bold red]Invalid selection.[/bold red]")

    def _handle_api_config(self):
        """Allows user to manually configure the API endpoint, key, and models."""
        console.print(Panel("[bold magenta]API Configuration[/bold magenta]", style="magenta"))
        
        action = Prompt.ask(
            "What would you like to update?", 
            choices=["url", "key", "model", "back"], 
            default="url"
        )
        
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
        """Injects internal Nano capabilities as tools."""
        def spawn_sub_agent(goal: str):
            """
            Spawns a specialized sub-agent to handle a complex, multi-step task that requires deep reasoning or a separate focus. 
            DO NOT use this for simple tasks like listing files or reading a single file; use the direct tools for those.
            Returns the final result of the sub-agent's work.
            """
            # Prevent sub-agents from spawning more sub-agents to stop infinite recursion
            if not hasattr(self, 'session') or self.session is None:
                return "Error: Sub-agents cannot spawn further sub-agents to prevent recursive loops."

            sub = Nano(tools_dir=self.registry.tools_dir, model=self.model, is_subagent=True)
            sub.messages[0]["content"] = f"You are a Nano sub-agent. Your specific goal is: {goal}. Provide a final answer when complete."
            return sub.run(goal)

        self.registry.tools["spawn_sub_agent"] = spawn_sub_agent
        self.registry.schemas.append(generate_tool_schema(spawn_sub_agent))

    def run(self, user_input: str):
        # Slash Command Handling
        if user_input.strip() == "/model":
            self._handle_model_selection()
            return "Model selection complete."
        
        if user_input.strip() == "/config":
            self._handle_api_config()
            return "Configuration updated."

        self.messages.append({"role": "user", "content": user_input})
        
        while True:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
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
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    schema = next((s for s in self.registry.schemas if s["function"]["name"] == name), {})
                    is_unsafe = schema.get("unsafe", False)
                    
                    if is_unsafe and self.confirm_callback:
                        if not self.confirm_callback(name, args):
                            console.print(Panel(Text("User denied execution.", style="red"), title="Action Cancelled"))
                            result = "Error: User denied execution of this tool."
                        else:
                            result = self._execute_tool(name, args)
                    else:
                        result = self._execute_tool(name, args)
                    
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result})
                
                if self.session:
                    self.session.save_session(self.messages)
                continue 
            else:
                if self.session:
                    self.session.save_session(self.messages)
                return full_content

    def _execute_tool(self, name: str, args: dict) -> str:
        console.print(Panel(Text(f"[bold cyan]Tool Call:[/bold cyan] {name}({args})", style="cyan"), title="Executing Tool"))
        result = self.registry.execute(name, args)
        console.print(Panel(Text(result, style="green"), title="Tool Result"))
        return result
