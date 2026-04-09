"""
Nova / VoiceCommander
dev_plugin.py — Jarvis-grade developer automation plugin.

Drop this file into the same folder as your other *_plugin.py files.
It auto-loads because load_all_plugins() picks up every *_plugin.py.

New voice actions
─────────────────
GIT
  git_status          → "what's the git status of my project?"
  git_commit          → "commit everything" / "commit with message fix the login bug"
  git_push            → "push to remote"
  git_pull            → "pull latest"
  git_branch          → "create branch feature/dark-mode" / "switch to main"
  git_log             → "show last 5 commits"
  git_diff            → "show what changed"
  git_stash           → "stash my changes"
  git_stash_pop       → "pop the stash"

PROJECT
  open_project        → "open my last project" / "open project nova-assistant"
  find_file           → "find AuthController.swift"
  run_script          → "run dev server" / "run tests" / "run build"

PROCESSES / PORTS
  port_info           → "what's on port 3000"
  kill_port           → "kill port 8080"
  list_processes      → "show running processes" / "what's eating my CPU"
  kill_process        → "kill process 1234" / "kill process node"

TERMINAL
  shell_run           → "run: brew update" (arbitrary shell command — confirm required)
  open_terminal       → "open terminal here" / "open terminal in my project"
  cd_project          → "go to project nova-assistant in terminal"

DOCKER
  docker_ps           → "show docker containers"
  docker_start        → "start container my-app"
  docker_stop         → "stop container my-app"
  docker_logs         → "show logs for container my-app"

ENVIRONMENT
  env_info            → "show my environment" (node, python, git versions)
  disk_usage          → "how much disk space do I have"
  memory_usage        → "show memory usage"
  wifi_info           → "what wifi am I on" / "show my IP"

Dependencies: all stdlib + anthropic (already in requirements.txt)
"""

from __future__ import annotations

import os
import re
import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from plugins import PluginBase
from logger import get_logger

log = get_logger("dev")

# ── helpers ───────────────────────────────────────────────────────────────────

def _sh(cmd: str | list, cwd: str = None, timeout: int = 30) -> tuple[str, str, int]:
    """Run a shell command, return (stdout, stderr, returncode)."""
    if isinstance(cmd, str):
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=cwd, timeout=timeout
        )
    else:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, timeout=timeout
        )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _truncate(s: str, max_len: int = 600) -> str:
    if len(s) <= max_len:
        return s
    half = max_len // 2
    return s[:half] + f"\n…[{len(s) - max_len} chars omitted]…\n" + s[-half:]


def _active_project(memory_db) -> Optional[str]:
    """Return the path stored as 'active_project' in user facts, if any."""
    facts = memory_db.get_facts() if memory_db else {}
    return facts.get("active_project") or facts.get("project_path")


def _find_projects(roots: list[str] = None, depth: int = 4) -> list[str]:
    """
    Scan common dev directories for project roots.
    A 'project root' is any directory containing .git, package.json,
    Cargo.toml, pyproject.toml, *.xcodeproj, or CMakeLists.txt.
    """
    MARKERS = {".git", "package.json", "Cargo.toml", "pyproject.toml",
               "CMakeLists.txt", "build.gradle", "pom.xml"}
    if roots is None:
        home = Path.home()
        roots = [
            str(home / "Developer"),
            str(home / "Projects"),
            str(home / "dev"),
            str(home / "code"),
            str(home / "workspace"),
            str(home / "Desktop"),
            str(home / "Documents"),
        ]

    found: list[str] = []

    def _scan(path: Path, current_depth: int):
        if current_depth > depth:
            return
        try:
            entries = list(path.iterdir())
        except PermissionError:
            return
        names = {e.name for e in entries}
        if MARKERS & names:
            found.append(str(path))
            return   # don't recurse inside a project
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith("."):
                _scan(entry, current_depth + 1)

    for root in roots:
        p = Path(root)
        if p.is_dir():
            _scan(p, 0)

    # Sort by modification time — most recently touched first
    found.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return found


def _ai_commit_message(diff: str, memory_db) -> str:
    """Call Claude to generate a conventional commit message from a diff."""
    try:
        import anthropic as ant
        from config import CONFIG
        client = ant.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])
        # Only send the first 3000 chars of the diff to stay inside token budget
        diff_snippet = diff[:3000] if len(diff) > 3000 else diff
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    "Write ONE conventional commit message (type: short description, "
                    "max 72 chars, no quotes, no explanation) for this diff:\n\n"
                    + diff_snippet
                )
            }]
        )
        return resp.content[0].text.strip().strip('"').strip("'")
    except Exception as e:
        log.error("AI commit message failed: %s", e)
        return "chore: update files"


# ── plugin ────────────────────────────────────────────────────────────────────

class Plugin(PluginBase):
    name        = "dev"
    description = "Developer automation: git, projects, ports, docker, shell"

    # ── setup ─────────────────────────────────────────────────────────────────

    def setup(self):
        # GIT
        self.action("git_status")(self._git_status)
        self.action("git_commit")(self._git_commit)
        self.action("git_push")(self._git_push)
        self.action("git_pull")(self._git_pull)
        self.action("git_branch")(self._git_branch)
        self.action("git_log")(self._git_log)
        self.action("git_diff")(self._git_diff)
        self.action("git_stash")(self._git_stash)
        self.action("git_stash_pop")(self._git_stash_pop)
        # PROJECT
        self.action("open_project")(self._open_project)
        self.action("find_file")(self._find_file)
        self.action("run_script")(self._run_script)
        # PROCESSES / PORTS
        self.action("port_info")(self._port_info)
        self.action("kill_port")(self._kill_port)
        self.action("list_processes")(self._list_processes)
        self.action("kill_process")(self._kill_process)
        # TERMINAL
        self.action("shell_run")(self._shell_run)
        self.action("open_terminal")(self._open_terminal)
        self.action("cd_project")(self._cd_project)
        # DOCKER
        self.action("docker_ps")(self._docker_ps)
        self.action("docker_start")(self._docker_start)
        self.action("docker_stop")(self._docker_stop)
        self.action("docker_logs")(self._docker_logs)
        # ENVIRONMENT
        self.action("env_info")(self._env_info)
        self.action("disk_usage")(self._disk_usage)
        self.action("memory_usage")(self._memory_usage)
        self.action("wifi_info")(self._wifi_info)

    # ═════════════════════════════════════════════════════════════════════════
    # GIT
    # ═════════════════════════════════════════════════════════════════════════

    def _git_status(self, data: dict, db) -> str:
        cwd = data.get("path") or _active_project(db)
        out, err, rc = _sh("git status --short", cwd=cwd)
        if rc != 0:
            return err or "Not a git repo (or git not found)."
        branch_out, _, _ = _sh("git branch --show-current", cwd=cwd)
        if not out:
            return f"Branch '{branch_out}' — working tree clean ✓"
        lines = out.splitlines()
        staged   = [l for l in lines if l[0] != ' ' and l[0] != '?']
        unstaged = [l for l in lines if l[0] == ' ' or l[0] == '?']
        parts = []
        if staged:
            parts.append(f"{len(staged)} staged")
        if unstaged:
            parts.append(f"{len(unstaged)} unstaged/untracked")
        summary = " · ".join(parts)
        return f"Branch '{branch_out}' — {summary}.\n{_truncate(out, 400)}"

    def _git_commit(self, data: dict, db) -> str:
        cwd    = data.get("path") or _active_project(db)
        msg    = data.get("message", "").strip()
        add_all = data.get("add_all", True)

        if add_all:
            _sh("git add -A", cwd=cwd)

        # Check if there's anything staged
        staged_out, _, _ = _sh("git diff --cached --stat", cwd=cwd)
        if not staged_out:
            return "Nothing staged to commit. (Working tree clean?)"

        # Generate AI commit message if none provided
        if not msg:
            diff_out, _, _ = _sh("git diff --cached", cwd=cwd)
            msg = _ai_commit_message(diff_out, db)

        out, err, rc = _sh(["git", "commit", "-m", msg], cwd=cwd)
        if rc != 0:
            return f"Commit failed: {err}"
        # Store last commit message
        if db:
            db.save_fact("last_commit_message", msg)
        return f"Committed: \"{msg}\""

    def _git_push(self, data: dict, db) -> str:
        cwd    = data.get("path") or _active_project(db)
        remote = data.get("remote", "origin")
        branch_out, _, _ = _sh("git branch --show-current", cwd=cwd)
        branch = data.get("branch", branch_out or "main")
        out, err, rc = _sh(f"git push {remote} {branch}", cwd=cwd)
        if rc != 0:
            return f"Push failed: {err or out}"
        return f"Pushed '{branch}' to {remote}."

    def _git_pull(self, data: dict, db) -> str:
        cwd = data.get("path") or _active_project(db)
        out, err, rc = _sh("git pull", cwd=cwd)
        if rc != 0:
            return f"Pull failed: {err or out}"
        return out or "Already up to date."

    def _git_branch(self, data: dict, db) -> str:
        cwd    = data.get("path") or _active_project(db)
        action = data.get("action", "list")   # create | switch | delete | list
        name   = data.get("name", "")

        if action == "create":
            if not name:
                return "Please provide a branch name."
            _, err, rc = _sh(f"git checkout -b {name}", cwd=cwd)
            return f"Created and switched to '{name}'." if rc == 0 else f"Error: {err}"

        elif action == "switch":
            if not name:
                return "Which branch should I switch to?"
            _, err, rc = _sh(f"git checkout {name}", cwd=cwd)
            return f"Switched to '{name}'." if rc == 0 else f"Error: {err}"

        elif action == "delete":
            if not name:
                return "Which branch should I delete?"
            _, err, rc = _sh(f"git branch -d {name}", cwd=cwd)
            return f"Deleted branch '{name}'." if rc == 0 else f"Error: {err}"

        else:   # list
            out, _, _ = _sh("git branch -a", cwd=cwd)
            return out or "No branches found."

    def _git_log(self, data: dict, db) -> str:
        cwd   = data.get("path") or _active_project(db)
        count = int(data.get("count", 7))
        fmt   = "%C(yellow)%h%Creset %s %C(dim)(%cr)%Creset"
        out, err, rc = _sh(
            f"git log --oneline -n {count} --pretty=format:'{fmt}'",
            cwd=cwd
        )
        return _truncate(out, 600) if rc == 0 else (err or "No commits yet.")

    def _git_diff(self, data: dict, db) -> str:
        cwd   = data.get("path") or _active_project(db)
        staged = data.get("staged", False)
        flag  = "--cached" if staged else ""
        out, err, rc = _sh(f"git diff {flag} --stat", cwd=cwd)
        if not out and rc == 0:
            return "No differences — working tree matches HEAD."
        return _truncate(out or err, 500)

    def _git_stash(self, data: dict, db) -> str:
        cwd = data.get("path") or _active_project(db)
        msg = data.get("message", "")
        cmd = f'git stash push -m "{msg}"' if msg else "git stash"
        out, err, rc = _sh(cmd, cwd=cwd)
        return "Changes stashed." if rc == 0 else f"Stash failed: {err}"

    def _git_stash_pop(self, data: dict, db) -> str:
        cwd = data.get("path") or _active_project(db)
        out, err, rc = _sh("git stash pop", cwd=cwd)
        return "Stash applied." if rc == 0 else f"Pop failed: {err}"

    # ═════════════════════════════════════════════════════════════════════════
    # PROJECT
    # ═════════════════════════════════════════════════════════════════════════

    def _open_project(self, data: dict, db) -> str:
        name    = data.get("name", "").strip()
        editor  = data.get("editor", "").lower()   # vscode | xcode | idea | ""
        recency = data.get("recency", "last")       # "last" | "all"

        projects = _find_projects()

        if not projects:
            return "No projects found in ~/Developer, ~/Projects, ~/Desktop."

        # Filter by name if given
        if name:
            matches = [p for p in projects if name.lower() in p.lower()]
            if not matches:
                return f"No project matching '{name}' found. Found: {', '.join(Path(p).name for p in projects[:6])}"
            target = matches[0]
        else:
            # "last" = most recently modified
            target = projects[0]

        # Remember as active project
        if db:
            db.save_fact("active_project", target)

        project_name = Path(target).name

        # Detect best editor
        if not editor:
            if (Path(target) / ".xcode").exists() or any(
                    Path(target).glob("*.xcodeproj")):
                editor = "xcode"
            elif shutil.which("code"):
                editor = "vscode"
            elif shutil.which("idea"):
                editor = "idea"
            else:
                editor = "finder"

        if editor in ("vscode", "code"):
            out, err, rc = _sh(f"code '{target}'")
            return f"Opened '{project_name}' in VS Code." if rc == 0 else f"VS Code error: {err}"

        elif editor == "xcode":
            xcproj = list(Path(target).glob("*.xcodeproj"))
            xcwork = list(Path(target).glob("*.xcworkspace"))
            file_to_open = str(xcwork[0] if xcwork else xcproj[0] if xcproj else target)
            _sh(f"open '{file_to_open}'")
            return f"Opened '{project_name}' in Xcode."

        elif editor in ("idea", "intellij"):
            _sh(f"idea '{target}'")
            return f"Opened '{project_name}' in IntelliJ IDEA."

        else:
            _sh(f"open '{target}'")
            return f"Opened '{project_name}' in Finder."

    def _find_file(self, data: dict, db) -> str:
        filename   = data.get("filename") or data.get("name", "")
        search_dir = data.get("path") or _active_project(db) or str(Path.home())
        max_results = int(data.get("max_results", 10))

        if not filename:
            return "Tell me the filename to search for."

        out, err, rc = _sh(
            f"find '{search_dir}' -name '{filename}' "
            f"-not -path '*/node_modules/*' -not -path '*/.git/*' "
            f"-not -path '*/build/*' -not -path '*/.venv/*' "
            f"2>/dev/null | head -{max_results}"
        )
        if not out:
            return f"'{filename}' not found under {search_dir}."

        lines = out.splitlines()
        result = "\n".join(lines)
        # Auto-open if exactly one match
        if len(lines) == 1:
            _sh(f"open '{lines[0]}'")
            return f"Found and opened: {lines[0]}"
        return f"Found {len(lines)} match(es):\n{result}"

    def _run_script(self, data: dict, db) -> str:
        """
        Run a named script from the project's package.json / Makefile / shell,
        or a raw command like 'dev', 'test', 'build', 'lint'.
        """
        script  = data.get("script", data.get("command", "dev")).strip()
        cwd     = data.get("path") or _active_project(db)
        background = data.get("background", True)   # most dev servers should be background

        pkg_json = Path(cwd or ".") / "package.json" if cwd else None

        # Try npm/yarn/pnpm first
        if pkg_json and pkg_json.exists():
            pm = "npm"
            for manager in ("pnpm", "yarn", "bun"):
                if shutil.which(manager):
                    pm = manager
                    break
            cmd = f"{pm} run {script}"
        elif shutil.which("make") and (Path(cwd or ".") / "Makefile").exists():
            cmd = f"make {script}"
        elif script in ("dev", "start", "serve", "run"):
            # Heuristic: try common entry points
            for candidate in ("python main.py", "python app.py",
                              "node index.js", "go run .", "cargo run"):
                prog = candidate.split()[0]
                if shutil.which(prog):
                    cmd = candidate
                    break
            else:
                cmd = script
        else:
            cmd = script

        if background:
            subprocess.Popen(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return f"Running `{cmd}` in background."
        else:
            out, err, rc = _sh(cmd, cwd=cwd, timeout=60)
            if rc != 0:
                return f"`{cmd}` failed:\n{_truncate(err or out, 400)}"
            return f"`{cmd}` complete.\n{_truncate(out, 400)}"

    # ═════════════════════════════════════════════════════════════════════════
    # PROCESSES / PORTS
    # ═════════════════════════════════════════════════════════════════════════

    def _port_info(self, data: dict, db) -> str:
        port = str(data.get("port", ""))
        if not port:
            return "Which port should I check?"

        out, _, rc = _sh(f"lsof -iTCP:{port} -sTCP:LISTEN -n -P")
        if not out:
            return f"Nothing is listening on port {port}."

        lines = out.splitlines()
        results = []
        for line in lines[1:]:   # skip header
            parts = line.split()
            if len(parts) >= 2:
                results.append(f"{parts[0]} (PID {parts[1]})")
        summary = ", ".join(results) if results else "Unknown process"
        return f"Port {port} is held by: {summary}"

    def _kill_port(self, data: dict, db) -> str:
        port = str(data.get("port", ""))
        if not port:
            return "Which port should I kill?"

        out, _, _ = _sh(f"lsof -ti TCP:{port} -sTCP:LISTEN")
        pids = [p.strip() for p in out.splitlines() if p.strip()]
        if not pids:
            return f"Nothing on port {port}."

        killed = []
        for pid in pids:
            _, err, rc = _sh(f"kill -9 {pid}")
            if rc == 0:
                killed.append(pid)

        return (f"Killed {len(killed)} process(es) on port {port} (PID: {', '.join(killed)})."
                if killed else f"Could not kill process on port {port}.")

    def _list_processes(self, data: dict, db) -> str:
        sort_by = data.get("sort_by", "cpu").lower()   # cpu | mem
        count   = int(data.get("count", 10))

        if sort_by == "mem":
            out, _, _ = _sh(f"ps aux --sort=-%mem 2>/dev/null | head -{count + 1} || "
                             f"ps aux | sort -k4 -rn | head -{count + 1}")
        else:
            out, _, _ = _sh(f"ps aux | sort -k3 -rn | head -{count + 1}")

        if not out:
            return "Could not list processes."
        # Re-format to be readable
        lines = out.splitlines()
        header = "NAME               PID    %CPU  %MEM"
        rows   = []
        for line in lines[1:count + 1]:
            parts = line.split()
            if len(parts) >= 11:
                name = Path(parts[10]).name[:18]
                rows.append(f"{name:<19}{parts[1]:<7}{parts[2]:<6}{parts[3]}")
        return header + "\n" + "\n".join(rows)

    def _kill_process(self, data: dict, db) -> str:
        pid  = data.get("pid")
        name = data.get("name", "").strip()

        if pid:
            _, err, rc = _sh(f"kill -9 {pid}")
            return f"Killed PID {pid}." if rc == 0 else f"Error: {err}"

        if name:
            out, _, _ = _sh(f"pgrep -f '{name}'")
            pids = [p.strip() for p in out.splitlines() if p.strip()]
            if not pids:
                return f"No process matching '{name}'."
            killed = []
            for p in pids:
                _, _, rc = _sh(f"kill -9 {p}")
                if rc == 0:
                    killed.append(p)
            return f"Killed '{name}' (PID: {', '.join(killed)})." if killed else f"Could not kill '{name}'."

        return "Tell me the PID or process name to kill."

    # ═════════════════════════════════════════════════════════════════════════
    # TERMINAL
    # ═════════════════════════════════════════════════════════════════════════

    def _shell_run(self, data: dict, db) -> str:
        """
        Run an arbitrary shell command.
        Requires explicit 'confirmed': true in the action payload for safety,
        otherwise Claude should narrate what it *would* do.
        """
        cmd       = data.get("command", "").strip()
        confirmed = data.get("confirmed", False)
        cwd       = data.get("path") or _active_project(db)

        if not cmd:
            return "No command provided."
        if not confirmed:
            return f"I'd run: `{cmd}` — say 'yes, run it' to confirm."

        out, err, rc = _sh(cmd, cwd=cwd, timeout=120)
        result = out or err
        if rc != 0:
            return f"Command failed (exit {rc}):\n{_truncate(result, 500)}"
        return _truncate(result, 600) if result else f"`{cmd}` completed (no output)."

    def _open_terminal(self, data: dict, db) -> str:
        path = data.get("path") or _active_project(db) or str(Path.home())
        safe = path.replace("'", "\\'")
        script = f"""
        tell application "Terminal"
            activate
            do script "cd '{safe}'"
        end tell
        """
        from mac_actions import run_applescript
        _, err = run_applescript(script)
        if err:
            # Fallback: iTerm2
            script2 = f"""
            tell application "iTerm2"
                activate
                tell current window
                    create tab with default profile
                    tell current session to write text "cd '{safe}'"
                end tell
            end tell
            """
            run_applescript(script2)
        name = Path(path).name
        return f"Opened terminal in '{name}'."

    def _cd_project(self, data: dict, db) -> str:
        name = data.get("name", "").strip()
        projects = _find_projects()
        if name:
            matches = [p for p in projects if name.lower() in p.lower()]
            target = matches[0] if matches else None
        else:
            target = projects[0] if projects else None

        if not target:
            return f"Project '{name}' not found."
        return self._open_terminal({"path": target}, db)

    # ═════════════════════════════════════════════════════════════════════════
    # DOCKER
    # ═════════════════════════════════════════════════════════════════════════

    def _docker_ps(self, data: dict, db) -> str:
        all_flag = "--all" if data.get("all", False) else ""
        out, err, rc = _sh(f"docker ps {all_flag} --format "
                           '"table {{.Names}}\t{{.Status}}\t{{.Ports}}"')
        if rc != 0:
            return "Docker is not running (or not installed)."
        return out or "No containers."

    def _docker_start(self, data: dict, db) -> str:
        name = data.get("name", "").strip()
        if not name:
            return "Which container should I start?"
        _, err, rc = _sh(f"docker start {name}")
        return f"Started '{name}'." if rc == 0 else f"Error: {err}"

    def _docker_stop(self, data: dict, db) -> str:
        name = data.get("name", "").strip()
        if not name:
            return "Which container should I stop?"
        _, err, rc = _sh(f"docker stop {name}")
        return f"Stopped '{name}'." if rc == 0 else f"Error: {err}"

    def _docker_logs(self, data: dict, db) -> str:
        name  = data.get("name", "").strip()
        lines = int(data.get("lines", 30))
        if not name:
            return "Which container's logs do you want?"
        out, err, rc = _sh(f"docker logs --tail {lines} {name}")
        return _truncate(out or err, 600) if (out or err) else "No logs found."

    # ═════════════════════════════════════════════════════════════════════════
    # ENVIRONMENT
    # ═════════════════════════════════════════════════════════════════════════

    def _env_info(self, data: dict, db) -> str:
        checks = [
            ("Python",  "python3 --version"),
            ("Node",    "node --version"),
            ("npm",     "npm --version"),
            ("Go",      "go version"),
            ("Rust",    "rustc --version"),
            ("Git",     "git --version"),
            ("Docker",  "docker --version"),
            ("Xcode",   "xcodebuild -version | head -1"),
        ]
        lines = []
        for label, cmd in checks:
            out, _, rc = _sh(cmd)
            if rc == 0 and out:
                ver = out.split("\n")[0]
                lines.append(f"{label}: {ver}")
        return "\n".join(lines) if lines else "Could not detect dev tools."

    def _disk_usage(self, data: dict, db) -> str:
        out, _, _ = _sh("df -h / | tail -1")
        if not out:
            return "Could not read disk usage."
        parts = out.split()
        if len(parts) >= 5:
            return (f"Disk /: {parts[1]} total · {parts[2]} used · "
                    f"{parts[3]} free ({parts[4]} used)")
        return out

    def _memory_usage(self, data: dict, db) -> str:
        # vm_stat is macOS-specific
        out, _, rc = _sh("vm_stat")
        if rc != 0:
            return "Could not read memory stats."
        pages_free    = 0
        pages_active  = 0
        pages_inactive = 0
        page_size     = 4096   # bytes, standard on macOS
        for line in out.splitlines():
            if "page size" in line.lower():
                m = re.search(r"(\d+) bytes", line)
                if m:
                    page_size = int(m.group(1))
            elif "Pages free" in line:
                pages_free = int(re.search(r"(\d+)", line).group(1))
            elif "Pages active" in line:
                pages_active = int(re.search(r"(\d+)", line).group(1))
            elif "Pages inactive" in line:
                pages_inactive = int(re.search(r"(\d+)", line).group(1))

        free_gb = pages_free * page_size / 1e9
        used_gb = (pages_active + pages_inactive) * page_size / 1e9
        return f"Memory — {used_gb:.1f} GB in use · {free_gb:.1f} GB free"

    def _wifi_info(self, data: dict, db) -> str:
        # SSID
        ssid_out, _, _ = _sh(
            "/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport -I | grep ' SSID' | awk '{print $2}'"
        )
        # Local IP
        ip_out, _, _ = _sh("ipconfig getifaddr en0 || ipconfig getifaddr en1")
        # Public IP (quick)
        pub_out, _, _ = _sh("curl -s --max-time 3 https://api.ipify.org")

        parts = []
        if ssid_out:
            parts.append(f"Wi-Fi: {ssid_out}")
        if ip_out:
            parts.append(f"Local IP: {ip_out}")
        if pub_out:
            parts.append(f"Public IP: {pub_out}")
        return " · ".join(parts) if parts else "Could not determine network info."

    # ═════════════════════════════════════════════════════════════════════════
    # SYSTEM PROMPT EXTENSION — teaches Claude the new actions
    # ═════════════════════════════════════════════════════════════════════════

    def system_prompt_extension(self) -> str:
        return """
━━━ DEV / VOICECOMMANDER PLUGIN ━━━

GIT — always include "path" if you know the active project:
<action>{"type":"git_status"}</action>
<action>{"type":"git_commit","add_all":true}</action>
<action>{"type":"git_commit","message":"fix: resolve login race condition"}</action>
<action>{"type":"git_push"}</action>
<action>{"type":"git_pull"}</action>
<action>{"type":"git_branch","action":"create","name":"feature/dark-mode"}</action>
<action>{"type":"git_branch","action":"switch","name":"main"}</action>
<action>{"type":"git_branch","action":"list"}</action>
<action>{"type":"git_log","count":5}</action>
<action>{"type":"git_diff"}</action>
<action>{"type":"git_stash"}</action>
<action>{"type":"git_stash_pop"}</action>

PROJECTS:
<action>{"type":"open_project"}</action>
<action>{"type":"open_project","name":"nova-assistant","editor":"vscode"}</action>
<action>{"type":"find_file","filename":"AuthController.swift"}</action>
<action>{"type":"run_script","script":"dev"}</action>
<action>{"type":"run_script","script":"test","background":false}</action>
<action>{"type":"run_script","script":"build","background":false}</action>

PORTS & PROCESSES:
<action>{"type":"port_info","port":3000}</action>
<action>{"type":"kill_port","port":8080}</action>
<action>{"type":"list_processes","sort_by":"cpu","count":10}</action>
<action>{"type":"kill_process","name":"node"}</action>
<action>{"type":"kill_process","pid":12345}</action>

TERMINAL:
<action>{"type":"open_terminal"}</action>
<action>{"type":"open_terminal","path":"~/Developer/my-app"}</action>
<action>{"type":"cd_project","name":"nova-assistant"}</action>
<action>{"type":"shell_run","command":"brew update","confirmed":false}</action>

DOCKER:
<action>{"type":"docker_ps"}</action>
<action>{"type":"docker_ps","all":true}</action>
<action>{"type":"docker_start","name":"my-app"}</action>
<action>{"type":"docker_stop","name":"my-app"}</action>
<action>{"type":"docker_logs","name":"my-app","lines":30}</action>

ENVIRONMENT:
<action>{"type":"env_info"}</action>
<action>{"type":"disk_usage"}</action>
<action>{"type":"memory_usage"}</action>
<action>{"type":"wifi_info"}</action>

RULES:
- For "commit everything" → emit git_commit with add_all:true (Claude generates the message via AI).
- For "open my last project" → emit open_project with no name.
- For "what's on port X" → emit port_info.
- For shell_run, ALWAYS set confirmed:false first and let the user confirm before re-emitting with confirmed:true.
- After open_project, save the path as active_project via save_fact so subsequent git commands know where to operate.
"""
