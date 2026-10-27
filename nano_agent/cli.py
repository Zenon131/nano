import sys
import os
import argparse
from rich.console import Console
from rich.prompt import Prompt, Confirm
from nano_agent.core import Nano

console = Console()

def confirm_tool_call(name, args):
    """Callback used by Nano to request permission for unsafe tools."""
    console.print(f"\n[bold yellow]⚠️  Safety Warning:[/bold yellow] Nano wants to run [bold red]{name}[/bold red] with args: {args}")
    return Confirm.ask("Allow this execution?")

def main():
    parser = argparse.ArgumentParser(description="Nano - The Transparent Shell Agent")
    parser.add_argument("--new", action="store_true", help="Start a fresh session")
    args = parser.parse_args()

    console.print("[bold magenta]Nano[/bold magenta] - The Transparent Shell Agent")
    console.print("Type 'exit' or 'quit' to stop.\n")
    
    tools_dir = os.path.join(os.getcwd(), "tools")
    
    try:
        agent = Nano(
            tools_dir=tools_dir, 
            confirm_callback=confirm_tool_call
        )
        
        if args.new:
            agent.session.clear_session()
            agent.messages = agent.messages[:1] # Keep only system prompt
            console.print("[dim]Started fresh session.[/dim]")
            
    except Exception as e:
        console.print(f"[bold red]Error initializing agent:[/bold red] {e}")
        sys.exit(1)

    while True:
        try:
            user_input = Prompt.ask("[bold blue]❯[/bold blue]")
            if user_input.lower() in ["exit", "quit"]:
                break
            
            if not user_input.strip():
                continue

            # streaming response is handled inside agent.run
            response = agent.run(user_input)
            
            # The streamed content is already printed by the agent
            console.print(f"\n") 
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"\n[bold red]Runtime Error:[/bold red] {e}\n")

if __name__ == "__main__":
    main()
