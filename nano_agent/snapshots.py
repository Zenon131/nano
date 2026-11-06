import hashlib
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Optional

# Where all Nano state lives (matches SessionManager's ~/.nano convention).
SNAPSHOT_HOME = Path("~/.nano/snapshots").expanduser()

# Directories that are never worth archiving: VCS metadata, caches and
# dependency trees dominate workspace size, but are not files an agent edit
# is meant to touch (and their toolchains can always recreate them).
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env",
    "node_modules", ".next", ".nuxt",
    ".nano", "dist", "build",
}

# Stray junk that doesn't belong in a restore point.
EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}

# Refuse to build an archive bigger than this; a workspace over the cap
# still gets a snapshot, just of the first files up to the limit.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
# Safety valve for pathological file counts, even under the byte cap.
MAX_FILES = 20000
# Keep this many newest snapshots per workspace; older ones are pruned.
MAX_KEEP = 10
# Automatic snapshots closer together than this share one archive: several
# unsafe calls inside a single tool round-trip are one logical edit.
MIN_INTERVAL_SECONDS = 5.0


class SnapshotManager:
    """Creates and restores workspace snapshots for the /undo command.

    Snapshots are keyed by working directory and stored under
    ~/.nano/snapshots/<workspace>/, so an undo always restores the files
    Nano was actually operating on.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = (root or Path.cwd()).resolve()
        key = f"{self.root.name}_{hashlib.sha1(str(self.root).encode()).hexdigest()[:8]}"
        self.dir = SNAPSHOT_HOME / key
        self._last_auto_time = 0.0

    def snapshot(self, label: str = "manual", force: bool = False) -> Optional[Path]:
        """Archive the workspace now.

        Returns the archive path, or None when nothing was archived:
        a skipped rate limit, an unreadable workspace, or an I/O error.
        `force=True` bypasses the rate limit (used for tests and /undo prep).
        """
        if not force and time.time() - self._last_auto_time < MIN_INTERVAL_SECONDS:
            return None
        candidates = self._candidate_files()
        if not candidates:
            return None
        archive_path = self.dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{label}.tar.gz"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, "w:gz") as tar:
                budget = MAX_ARCHIVE_BYTES
                for path, rel, size in candidates[:MAX_FILES]:
                    if size > budget:
                        continue  # Skip oversized files; keep archiving the rest.
                    budget -= size
                    member = tar.gettarinfo(str(path), arcname=str(rel))
                    # Store plain regular files only: symlinks/specials are
                    # either recreated by the toolchain or unsafe to restore.
                    if not member.isreg():
                        continue
                    with open(path, "rb") as f:
                        tar.addfile(member, f)
        except (OSError, tarfile.TarError):
            archive_path.unlink(missing_ok=True)
            return None
        self._prune()
        self._last_auto_time = time.time()
        return archive_path

    def items(self) -> List[Dict]:
        """Available snapshots for this workspace, newest first."""
        result = []
        if not self.dir.is_dir():
            return result
        for archive in self.dir.glob("*.tar.gz"):
            # Filenames are "<YYYYmmdd-HHMMSS>_<label>.tar.gz".
            stamp, _, label = archive.name.partition("_")
            try:
                taken = time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))
            except ValueError:
                continue
            result.append({"path": archive, "time": taken, "label": label.removesuffix(".tar.gz")})
        return sorted(result, key=lambda item: item["time"], reverse=True)

    def restore(self, item: Dict) -> Dict:
        """Extract a snapshot over the workspace, safely.

        Only regular files whose names stay inside the workspace are
        restored — members with absolute paths or '..' are skipped, so a
        malicious or moved archive cannot write outside the workspace.

        Files created after the snapshot are left in place; a restore
        overwrites and un-deletes known files rather than deleting extras.
        """
        summary = {"files": 0, "time": item["time"], "label": item["label"], "path": item["path"]}
        with tarfile.open(item["path"], "r:gz") as tar:
            for member in tar.getmembers():
                rel = self._safe_relpath(member.name)
                if rel is None or not member.isreg():
                    continue
                member.name = str(rel)
                tar.extract(member, self.root)
                summary["files"] += 1
        return summary

    def restore_latest(self) -> Optional[Dict]:
        """Restore the most recent snapshot, or None if there is none."""
        available = self.items()
        return self.restore(available[0]) if available else None

    def _candidate_files(self) -> List:
        """Regular, readable workspace files (path, relative name, size),
        excluding caches/dependencies and sorted for a deterministic archive."""
        candidates = []
        for path in self.root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(self.root)
            except OSError:
                continue
            if EXCLUDED_DIRS & set(rel.parts[:-1]) or rel.name in EXCLUDED_FILES:
                continue
            try:
                candidates.append((path, rel, path.stat().st_size))
            except OSError:
                continue
        return sorted(candidates, key=lambda entry: str(entry[1]))

    def _prune(self):
        archives = sorted(self.dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in archives[MAX_KEEP:]:
            old.unlink(missing_ok=True)

    @staticmethod
    def _safe_relpath(member_name: str) -> Optional[Path]:
        """The workspace-relative path of a tar member, or None if unsafe."""
        parts = Path(member_name).parts
        if not parts or Path(member_name).is_absolute() or ".." in parts:
            return None
        return Path(*parts)
