
import os
from pathlib import Path

with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "r") as f:
    content = f.read()

session_manager_code = """
class SessionManager:
    def __init__(self, sessions_dir: str = "~/.nano/sessions"):
        self.path = Path(sessions_dir).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.current_session_file = self.path / "current.json"

    def save_session(self, messages, session_id=None):
        import json
        with open(self.current_session_file, "w") as f:
            json.dump(messages, f, indent=2)
        if session_id:
            target = self.path / f"{session_id}.json"
            with open(target, "w") as f:
                json.dump(messages, f, indent=2)

    def load_session(self, session_id=None):
        import json
        target = self.path / f"{session_id}.json" if session_id else self.current_session_file
        if not target.exists(): return []
        try:
            with open(target, "r") as f: return json.load(f)
        except: return []

    def list_sessions(self):
        sessions = []
        for f in self.path.glob("*.json"):
            if f.name == "current.json": continue
            try:
                mtime = f.stat().st_mtime
                sessions.append({"id": f.stem, "date": mtime, "path": str(f)})
            except: continue
        return sorted(sessions, key=lambda x: x["date"], reverse=True)

    def clear_session(self):
        if self.current_session_file.exists():
            self.current_session_file.unlink()
"""

idx = content.find("class Nano:")
if idx != -1:
    content = content[:idx] + session_manager_code + "\n\n" + content[idx:]
    content = content.replace("        self.session = None", "        self.session = SessionManager()")
    with open("/Users/jonathanwallace/Projects/nano-agent/nano_agent/core.py", "w") as f:
        f.write(content)
    print("Success")
else:
    print("Fail")
