import sys
import os
import argparse
import asyncio
from pathlib import Path
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm
from rich.align import Align
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from nano_agent.core import Nano
from nano_agent.ui import async_input

console = Console()

# Custom bindings to avoid the "Invalid Key" crash
cli_bindings = KeyBindings()

@cli_bindings.add('enter')
def _(event):
    # Submit if buffer is not empty
    if event.current_buffer.text.strip():
        event.current_buffer.validate_and_handle()
    else:
        # If empty, just allow it as a newline
        event.current_buffer.insert_text('\n')

@cli_bindings.add('c-k')
def _(event):
    # Ctrl+K opens the help menu, identical to typing /help.
    buffer = event.current_buffer
    buffer.text = "/help"
    buffer.cursor_position = len(buffer.text)
    buffer.validate_and_handle()

@cli_bindings.add('escape', 'enter')
def _(event):
    # Alt+Enter inserts a real newline; Enter alone sends the message.
    event.current_buffer.insert_text('\n')

def print_splash():
    """Prints a centered, branded landing page."""
    console.clear()
    try:
        with open(os.path.join(os.path.dirname(__file__), 'logo.txt'), 'r', encoding='utf-8') as f:
            logo_text = f.read()
    except Exception:
        logo_text = "N A N O"
    
    logo = Text(logo_text, style="bold cyan")
    sub = Text("\nTHE TRANSPARENT SHELL AGENT", style="dim italic")
    divider = Text("\n" + "─" * 40, style="dim")
    
    console.print(Align.center(logo))
    console.print(Align.center(sub))
    console.print(Align.center(divider))
    console.print("\n")

async def confirm_tool_call(name, args, session=None):
    """Callback used by Nano to request permission for unsafe tools."""
    console.print(f"\n[bold yellow]⚠️  Safety Warning:[/bold yellow] Nano wants to run [bold red]{escape(str(name))}[/bold red] with args: {escape(str(args))}")

    if session:
        ans_text = await async_input("Allow? [y/n] (n): ")
        return ans_text.strip().lower() in ("y", "yes")

    def get_input():
        return input("Allow? [y/n] (n): ").strip().lower()
    ans_text = await asyncio.to_thread(get_input)
    return ans_text in ("y", "yes")

def build_arg_parser():
    parser = argparse.ArgumentParser(description="Nano - The Transparent Shell Agent")
    parser.add_argument("--new", action="store_true", help="Start a fresh session")
    parser.add_argument("-p", "--prompt", metavar="TEXT",
                        help="Run one prompt non-interactively (pipes/scripts) and exit")
    parser.add_argument("-y", "--auto", "--yes", action="store_true",
                        help="Auto-approve unsafe tools")
    return parser

async def run_headless(args) -> int:
    allow_unsafe = args.auto

    async def headless_confirm(name, tool_args, session=None):
        return allow_unsafe

    try:
        agent = Nano(tools_dir=None, confirm_callback=headless_confirm, interactive=False)
    except Exception as init_error:
        console.print(f"[bold red]Error initializing agent:[/bold red] {escape(str(init_error))}")
        return 1
    
    try:
        await agent.run(args.prompt)
        return 0
    except Exception as run_error:
        console.print(f"\n[bold red]Runtime Error:[/bold red] {escape(str(run_error))}\n")
        return 1

async def run_interactive(args):
    print_splash()
    console.print("Type 'exit' or 'quit' to stop.")
    console.print("[dim](Enter to send, Alt+Enter for a new line)\n")
    
    try:
        agent = Nano(
            tools_dir=None,
            confirm_callback=confirm_tool_call,
            auto_approve=args.auto
        )
        
        if args.new:
            agent.session.clear_session()
            agent.messages = agent.messages[:1]
            console.print("[dim]Started fresh session.[/dim]")
            
    except Exception as e:
        console.print(f"[bold red]Error initializing agent:[/bold red] {escape(str(e))}")
        sys.exit(1)

    def get_status_bar():
        try:
            auto_segment = " | ⚡ AUTO" if agent.auto_approve else ""
            return f" Nano | {agent.provider_name} | {agent.model}{auto_segment} | {len(agent.messages)} msgs | Ctrl+K for help "
        except (AttributeError, NameError):
            return " Nano | Initializing... "

    slash_commands = ["/help", "/undo", "/clear", "/auto", "/save", "/models", "/provider", "/status", "/debug"]
    
    class SlashCompleter(Completer):
        def __init__(self, commands):
            self.commands = commands
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith('/') and ' ' not in text:
                lowered = text.lower()
                for cmd in self.commands:
                    if cmd.lower().startswith(lowered):
                        yield Completion(cmd, start_position=-len(text))

    completer = SlashCompleter(slash_commands)
    history_dir = Path.home() / ".nano"
    history_dir.mkdir(parents=True, exist_ok=True)

    session = PromptSession(
        key_bindings=cli_bindings, 
        multiline=True, 
        completer=completer,
        history=FileHistory(str(history_dir / "history")),
        bottom_toolbar=get_status_bar
    )

    agent.prompt_session = session

    while True:
        try:
            user_input = await session.prompt_async("❯ ")
        except (EOFError, KeyboardInterrupt):
            break

        try:
            if user_input.lower() in ["exit", "quit"]:
                break
            if not user_input.strip():
                continue
            response = await agent.run(user_input)
            if response: console.print(response)
        except KeyboardInterrupt:
            console.print("\n[yellow]✗ Interrupted — response cancelled, session preserved.[/yellow]")
        except Exception as e:
            console.print(f"\n[bold red]Runtime Error:[/bold red] {escape(str(e))}\n")

def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    
    async def _execute():
        if args.prompt:
            return await run_headless(args)
        await run_interactive(args)
        return 0

    try:
        return asyncio.run(_execute())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    sys.exit(main())
