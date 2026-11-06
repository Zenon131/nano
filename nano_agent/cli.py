import sys
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
    # NOTE: macOS terminals intercept Cmd+K before it ever reaches this
    # process (usually clearing the screen, which desyncs the renderer and
    # looks like a crash). Ctrl+K is the reliable, bindable equivalent.
    buffer = event.current_buffer
    buffer.text = "/help"
    buffer.cursor_position = len(buffer.text)
    buffer.validate_and_handle()

@cli_bindings.add('escape', 'enter')
def _(event):
    # Alt+Enter inserts a real newline; Enter alone sends the message.
    # (Terminals deliver Alt+Enter as an Escape followed by Enter.)
    event.current_buffer.insert_text('\n')

def print_splash():
    """Prints a centered, branded landing page."""
    console.clear()
    logo = Text("\n\nN A N O", style="bold magenta")
    sub = Text("\nTHE TRANSPARENT SHELL AGENT", style="dim italic")
    divider = Text("\n" + "─" * 40, style="dim")
    
    console.print(Align.center(logo))
    console.print(Align.center(sub))
    console.print(Align.center(divider))
    console.print("\n")

def confirm_tool_call(name, args):
    """Callback used by Nano to request permission for unsafe tools."""
    console.print(f"\n[bold yellow]⚠️  Safety Warning:[/bold yellow] Nano wants to run [bold red]{escape(str(name))}[/bold red] with args: {escape(str(args))}")
    return Confirm.ask("Allow?", default=False)

def build_arg_parser():
    parser = argparse.ArgumentParser(description="Nano - The Transparent Shell Agent")
    parser.add_argument("--new", action="store_true", help="Start a fresh session")
    parser.add_argument("-p", "--prompt", metavar="TEXT",
                        help="Run one prompt non-interactively (pipes/scripts) and exit")
    parser.add_argument("-y", "--auto", "--yes", action="store_true",
                        help="Auto-approve unsafe tools: file edits and shell commands run "
                             "without asking (/undo snapshots are still kept; /auto toggles)")
    return parser

async def run_headless(args) -> int:
    """Single-prompt execution for scripts and CI.

    Safe by default: unsafe tools are denied unless --auto (or --yes) is
    passed. Returns the process exit code; the agent's streaming output is
    printed exactly as in the interactive session (minus the splash/status bar).
    """
    allow_unsafe = args.auto
    headless_confirm = (lambda name, tool_args: allow_unsafe)
    try:
        agent = Nano(tools_dir=None, confirm_callback=headless_confirm, interactive=False)
    except Exception as init_error:
        try:
            console.print(f"[bold red]Error initializing agent:[/bold red] {escape(str(init_error))}")
        except Exception:
            print(f"Error initializing agent: {init_error}", file=sys.stderr)
        return 1
    if not allow_unsafe:
        console.print("[dim]Headless: unsafe tools are auto-denied; pass --auto to approve them.[/dim]")
    try:
        await agent.run(args.prompt)
        return 0
    except Exception as run_error:
        try:
            console.print(f"\n[bold red]Runtime Error:[/bold red] {escape(str(run_error))}\n")
        except Exception:
            print(f"Runtime Error: {run_error}", file=sys.stderr)
        return 1

async def run_interactive(args):
    print_splash()
    console.print("Type 'exit' or 'quit' to stop.")
    console.print("[dim](Enter to send, Alt+Enter for a new line)\n")

    # Let the Nano core handle the default tools directory resolution
    tools_dir = None
    
    try:
        agent = Nano(
            tools_dir=tools_dir,
            confirm_callback=confirm_tool_call,
            auto_approve=args.auto
        )

        if args.new:
            agent.session.clear_session()
            agent.messages = agent.messages[:1] # Keep only system prompt
            console.print("[dim]Started fresh session.[/dim]")

    except Exception as e:
        console.print(f"[bold red]Error initializing agent:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # Splash summary: one dim line of orientation info after init.
    session_state = "resumed" if len(agent.messages) > 1 else "fresh"
    console.print(f"[dim]connected · {escape(agent.provider)} · {escape(agent.model or 'no model set')} · "
                  f"{len(agent.registry.schemas)} tools · {session_state} session ({len(agent.messages)} msgs)[/dim]\n")

    # Auto mode must never be invisible: loud banner at startup, persistent
    # AUTO segment in the status bar while it is on.
    if agent.auto_approve:
        console.print("[bold yellow]⚡ Auto-approve enabled: file edits and shell commands will run WITHOUT asking. "
                      "/undo snapshots are still kept. Type /auto to disable.[/bold yellow]\n")

    # A dynamic status bar that lives at the bottom of the screen.
    # It updates every render cycle: provider, model, live context size.
    def get_status_bar():
        try:
            auto_segment = " | ⚡ AUTO" if agent.auto_approve else ""
            return f" Nano | {agent.provider} | {agent.model}{auto_segment} | {len(agent.messages)} msgs | Ctrl+K for help "
        except AttributeError:
            return " Nano | Initializing... "

    # Define slash commands for auto-completion
    slash_commands = [
        "/help", 
        "/save", 
        "/sessions", 
        "/context", 
        "/provider", 
        "/model", 
        "/config", 
        "/debug",
        "/clear",
        "/tokens",
        "/tools",
        "/undo",
        "/auto"
    ]
    # Use a custom completer that only activates when the buffer starts with '/'
    # and contains ONLY the command (no other text).
    class SlashCompleter(Completer):
        """Prefix-matching completer for slash commands.

        WordCompleter is unsuitable here: its word-splitting pattern treats
        '/' as a separator, so typing '/he' matched the word 'he' against
        candidates like '/help' and returned nothing. This completer matches
        the whole buffer as a command prefix instead.
        """
        def __init__(self, commands):
            self.commands = commands

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # Only suggest if the line starts with '/' and has no spaces
            # (a space means arguments follow — no suggestions mid-sentence).
            if text.startswith('/') and ' ' not in text:
                lowered = text.lower()
                for cmd in self.commands:
                    if cmd.lower().startswith(lowered):
                        yield Completion(cmd, start_position=-len(text))

    completer = SlashCompleter(slash_commands)

    # Persistent input history: up-arrow recalls previous prompts across sessions.
    history_dir = Path.home() / ".nano"
    history_dir.mkdir(parents=True, exist_ok=True)

    session = PromptSession(
        key_bindings=cli_bindings, 
        multiline=True, 
        completer=completer,
        history=FileHistory(str(history_dir / "history")),
        bottom_toolbar=get_status_bar
    )

    while True:
        try:
            # prompt_async avoids blocking the event loop
            user_input = await session.prompt_async("❯ ")
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C / Ctrl+D at the prompt exits, like a shell.
            break

        try:
            if user_input.lower() in ["exit", "quit"]:
                break
            
            if not user_input.strip():
                continue

            # streaming response is handled inside agent.run
            await agent.run(user_input)
            
        except KeyboardInterrupt:
            # Ctrl+C during a response cancels the turn; the transcript is
            # repaired (see _repair_interrupted_transcript) and saved in core.
            console.print("\n[yellow]✗ Interrupted — response cancelled, session preserved.[/yellow]")
        except Exception as e:
            # The error printer itself must never kill the app: exception
            # messages can contain arbitrary text (file paths in brackets,
            # markup-looking fragments), so escape it. If even that fails,
            # fall back to a plain print.
            try:
                console.print(f"\n[bold red]Runtime Error:[/bold red] {escape(str(e))}\n")
            except Exception:
                print(f"\nRuntime Error: {e}\n", file=sys.stderr)

def main():
    """
    Synchronous entry point for the CLI.
    This is what the pyproject.toml entry point calls.
    """
    args = build_arg_parser().parse_args()
    exit_code = 0
    try:
        if args.prompt is not None:
            exit_code = asyncio.run(run_headless(args))
        else:
            asyncio.run(run_interactive(args))
    except KeyboardInterrupt:
        exit_code = 130
    sys.exit(exit_code)

if __name__ == "__main__":
    main()