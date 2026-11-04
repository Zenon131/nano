import subprocess
import os

def run_command(command: str):
    """
    Executes a shell command and returns the output. 
    WARNING: This is a dangerous tool.
    """
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        return f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"


# The registry inspects this attribute when the module is loaded.
setattr(run_command, "unsafe", True)
