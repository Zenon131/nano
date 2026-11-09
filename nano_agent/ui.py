import re
from rich.console import Console
from rich.markup import escape
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.panel import Panel
from rich.box import ROUNDED

console = Console()

# Lazy singleton for one-line interactive prompts (confirmations, ask_user).
_interactive_session = None


async def async_input(message: str) -> str:
    """Read one line interactively via a dedicated prompt_toolkit session.

    This must NOT reuse the main chat session: the chat's Enter binding
    turns an empty Enter into a newline (for multiline messages), which
    would hang any confirmation prompt that relies on Enter meaning
    "accept the default". This session is single-line, so Enter always
    submits — even when the buffer is empty.
    """
    global _interactive_session
    from prompt_toolkit import PromptSession  # imported lazily: ui stays usable headless
    if _interactive_session is None:
        _interactive_session = PromptSession()
    return await _interactive_session.prompt_async(message)

# Color Palette
C_AGENT = "bold sky_blue1"
C_TOOL_NAME = "bold yellow"
C_TOOL_ARGS = "italic dim"
C_RESULT_LABEL = "dim grey50"
C_SYSTEM = "dim"

class TimedSpinner(Spinner):
    """A spinner that appends elapsed seconds, e.g. 'Nano is thinking... 3s'.
    
    The elapsed time is computed from the console render clock, so it ticks
    on every refresh even when no stream chunks are arriving.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_time = None

    def render(self, time: float) -> Text:
        text = super().render(time)
        if self._start_time is None:
            self._start_time = time
        text.append(f" {int(time - self._start_time)}s", style="dim")
        return text


def create_thinking_indicator(console: Console) -> Live:
    """Returns a Live display showing 'Nano is thinking... Ns' with a live
    elapsed-seconds counter. Callers .start()/.stop() it like a rich Status."""
    spinner = TimedSpinner("dots12", text="Nano is thinking...", style=C_AGENT)
    return Live(spinner, console=console, refresh_per_second=10, transient=True)


def render_tool_call(tool_name: str, args: any):
    """Renders a stylized tool call notification using a compact Panel."""
    # We use a small panel to separate the "action" from the "chat"
    content = Text()
    content.append("🛠️  Tool Call: ", style=C_SYSTEM)
    content.append(escape(str(tool_name)), style=C_TOOL_NAME)
    content.append(f" {escape(str(args))}", style=C_TOOL_ARGS)
    
    console.print(Panel(content, box=ROUNDED, border_style="grey37", padding=(0, 1)))

def render_tool_result(result: str):
    """Renders the result of a tool call in a contained, visually distinct block."""
    MAX_DISPLAY_CHARS = 2000
    if len(result) > MAX_DISPLAY_CHARS:
        extra = len(result) - MAX_DISPLAY_CHARS
        display = result[:MAX_DISPLAY_CHARS] + f"\n... [{extra} more characters truncated for display; full result sent to the model]"
    else:
        display = result

    stripped = display.lstrip()
    
    # Prepare the renderable content
    renderable = None
    label = "Result"

    if stripped.startswith(("diff --git", "--- ")) and "+++" in display:
        label = "Diff"
        try:
            renderable = Syntax(display, "diff", theme="monokai", line_numbers=False)
        except Exception:
            renderable = escape(display)
    elif "```" in display or (display.startswith("def ") or display.startswith("import ")):
        try:
            lang = "python"
            if "javascript" in display.lower(): lang = "javascript"
            elif "bash" in display.lower(): lang = "bash"
            renderable = Syntax(display, lang, theme="monokai", line_numbers=True)
        except Exception:
            renderable = escape(display)
    else:
        renderable = escape(display)

    # Wrap everything in a Panel to give it a "block" feel
    # We use a dim border and a small label to keep it from being distracting
    console.print(
        Panel(
            renderable, 
            title=f"[dim]{label}[/dim]", 
            box=ROUNDED, 
            border_style="grey23", 
            padding=(0, 1)
        )
    )
