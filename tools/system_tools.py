import os
from pathlib import Path

def list_files(path='.'):
    """Lists files in the specified directory."""
    return os.listdir(path)

def read_file(path):
    """Reads the content of a file."""
    with open(path, 'r') as f:
        return f.read()

def write_file(path: str, content: str):
    """
    Writes content to a file, completely replacing existing content.
    Use this to create new files or overwrite existing ones.
    """
    write_file.unsafe = True
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    return f"Successfully wrote to {path}"

def edit_file(path: str, old_string: str, new_string: str):
    """
    Surgically replaces a specific string in a file with a new string.
    Useful for making small changes without overwriting the whole file.
    """
    edit_file.unsafe = True
    with open(path, 'r') as f:
        content = f.read()
    
    if old_string not in content:
        return f"Error: Could not find the text to replace in {path}"
    
    new_content = content.replace(old_string, new_string)
    with open(path, 'w') as f:
        f.write(new_content)
    return f"Successfully updated {path}"
