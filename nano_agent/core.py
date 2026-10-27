import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
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
        base_url = config.get("base_url") # e.g., "http://localhost:11434/v1" for Ollama
        api_key = config.get("api_key", os.getenv("OPENAI_API_KEY", "ollama"))
        
        # The OpenAI client is compatible with any OpenAI-like API (including Ollama)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
        self.registry = ToolRegistry(config.get("tools_dir", tools_dir))
        self.registry.load_tools()
        
        # Register the sub-agent capability as a tool
        self._register_core_tools()
        
        self.session = SessionManager()
        self.messages = self.session.load_session()
        self.confirm_callback = confirm_callback
        
        if not self.messages:
            self.messages = [{"role": "system", "content": system_prompt}]
        
        if is_subagent:
            # Sub-agents don't use the shared .nano_history to avoid pollution
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

    def _register_core_tools(self):
        """Injects internal Nano capabilities as tools."""
        def spawn_sub_agent(goal: str):
            """
            Spawns a specialized sub-agent to handle a complex sub-task.
            Returns the final result of the sub-agent's work.
            """
            sub = Nano(tools_dir=self.registry.tools_dir, model=self.model, is_subagent=True)
            sub.messages[0]["content"] = f"You are a Nano sub-agent. Your specific goal is: {goal}. Provide a final answer when complete."
            return sub.run(goal)

        self.registry.tools["spawn_sub_agent"] = spawn_sub_agent
        self.registry.schemas.append(generate_tool_schema(spawn_sub_agent))

    def run(self, user_input: str):
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
                    console.print(delta.content, end="", flush=True)
                
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
