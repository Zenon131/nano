import os

def list_files(path='.'):
    """Lists files in the specified directory."""
    return os.listdir(path)

def read_file(path):
    """Reads the content of a file."""
    with open(path, 'r') as f:
        return f.read()
