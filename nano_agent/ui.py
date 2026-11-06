import re
from rich.console import Console
from rich.markup import escape
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.syntax import Syntax

console = Console()


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
    spinner = TimedSpinner("dots12", text="Nano is thinking...", style="bold magenta")
    return Live(spinner, console=console, refresh_per_second=10, transient=True)


def render_tool_call(tool_name: str, args: any):
    """Renders a stylized tool call notification."""
    # Tool names/args come from the model and may contain '[', which rich
    # would parse as markup — always escape data-driven text.
    console.print(f"\n[bold cyan]🛠️  Tool Call:[/bold cyan] [bold white]{escape(str(tool_name))}[/bold white] [dim]({escape(str(args))})[/dim]")

def render_tool_result(result: str):
    """Renders the result of a tool call in a dim, contained block.

    Long results are truncated for display only — the model always receives
    the full text, so this is purely to keep the terminal readable.
    """
    MAX_DISPLAY_CHARS = 2000
    if len(result) > MAX_DISPLAY_CHARS:
        extra = len(result) - MAX_DISPLAY_CHARS
        display = result[:MAX_DISPLAY_CHARS] + f"\n... [{extra} more characters truncated for display; full result sent to the model]"
    else:
        display = result

    # Escape data-driven text so file paths like '[/foo/bar.py]' aren't
    # parsed as rich markup tags.
    stripped = display.lstrip()
    if stripped.startswith(("diff --git", "--- ")) and "+++" in display:
        # Patch/diff output: render with the diff lexer so +/- lines are colored.
        try:
            syntax = Syntax(display, "diff", theme="monokai", line_numbers=False)
            console.print(f"[dim]↳ Result (diff):[/dim]")
            console.print(syntax)
        except Exception:
            console.print(f"[dim]↳ Result (diff):[/dim] {escape(display)}")
    elif "```" in display or (display.startswith("def ") or display.startswith("import ")):
        try:
            # Simple heuristic to guess language, defaulting to python
            lang = "python"
            if "javascript" in display.lower(): lang = "javascript"
            elif "bash" in display.lower(): lang = "bash"

            syntax = Syntax(display, lang, theme="monokai", line_numbers=True)
            console.print(f"[dim]↳ Result:[/dim]")
            console.print(syntax)
        except Exception:
            console.print(f"[dim]↳ Result:[/dim]")
            console.print(escape(display))
    else:
        console.print(f"[dim]↳ Result:[/dim] {escape(display)}")