"""
Nova / VoiceCommander
dev_ui.py — Companion developer dashboard window.

Run standalone:  python dev_ui.py
Or launch from the Nova menu bar.

Three live tabs
───────────────
  OUTPUT   — streaming command output (acts like a mini terminal)
  GIT      — current branch · dirty files · recent commits
  PROJECT  — open projects list · active project badge · quick actions

Features
─────────
  • Tail of nova.log scrolled in real-time
  • Branch indicator + dirty file count refreshed every 3 s
  • "Quick actions" toolbar: git status · commit all · port check ·
    open project · run dev server
  • Fully dark, matches Nova's palette, draggable, always-on-top
  • Writes commands to a shared IPC pipe so the running Nova instance
    executes them (if Nova is open), otherwise runs them directly

Usage without Nova running
───────────────────────────
  python dev_ui.py
  All git/shell buttons work stand-alone via subprocess.

Usage alongside Nova
─────────────────────
  Nova's _process() pipeline is unchanged.
  dev_ui reads nova.log for output and writes to .nova_cmd for IPC.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk, simpledialog, messagebox
from typing import Optional

# ── palette (matches main.py) ─────────────────────────────────────────────────
BG      = "#0d0d14"
SURFACE = "#12121f"
BORDER  = "#1f2937"
TEXT    = "#e5e7eb"
MUTED   = "#6b7280"
ACCENT  = "#3b82f6"
GREEN   = "#34d399"
YELLOW  = "#fbbf24"
RED     = "#f87171"
PURPLE  = "#a78bfa"
ORANGE  = "#fb923c"

FONT_MONO = ("Menlo", 11)
FONT_UI   = ("Helvetica Neue", 11)
FONT_BOLD = ("Helvetica Neue", 11, "bold")

HERE      = Path(__file__).parent.resolve()
LOG_PATH  = HERE / "nova.log"
CMD_PIPE  = HERE / ".nova_cmd"
DB_PATH   = HERE / "nova_memory.db"


# ── helpers ───────────────────────────────────────────────────────────────────

def _sh(cmd: str, cwd: str = None) -> tuple[str, str, int]:
    r = subprocess.run(cmd, shell=True, capture_output=True,
                       text=True, cwd=cwd, timeout=20)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def _active_project_from_db() -> Optional[str]:
    """Read active_project fact from SQLite without importing memory_db."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        row  = conn.execute(
            "SELECT value FROM user_facts WHERE key='active_project'"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _find_projects() -> list[str]:
    home  = Path.home()
    roots = [home / "Developer", home / "Projects", home / "dev",
             home / "code", home / "workspace", home / "Desktop"]
    MARKERS = {".git", "package.json", "Cargo.toml", "pyproject.toml",
               "CMakeLists.txt", "build.gradle"}
    found: list[str] = []

    def _scan(path: Path, depth: int):
        if depth > 3:
            return
        try:
            entries = list(path.iterdir())
        except PermissionError:
            return
        names = {e.name for e in entries}
        if MARKERS & names:
            found.append(str(path))
            return
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith("."):
                _scan(entry, depth + 1)

    for root in roots:
        if root.is_dir():
            _scan(root, 0)

    found.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return found


def _send_to_nova(command: str):
    """Write a command to the IPC file; the Nova process will pick it up."""
    try:
        CMD_PIPE.write_text(command + "\n")
    except Exception:
        pass


# ── main window ───────────────────────────────────────────────────────────────

class DevUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Nova DevPanel")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.geometry("640x780")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)

        self._project: Optional[str] = None
        self._branch:  str           = ""
        self._dirty:   int           = 0
        self._log_pos: int           = 0
        self._running  = True

        try:
            self.root.tk.call(
                "::tk::unsupported::MacWindowStyle", "style",
                self.root._w, "plain", "none"
            )
        except Exception:
            pass

        self._setup_drag()
        self._build_ui()
        self._refresh_project()
        self._start_background_threads()

    # ── drag ─────────────────────────────────────────────────────────────────

    def _setup_drag(self):
        self.root.bind("<ButtonPress-1>",   self._drag_start)
        self.root.bind("<B1-Motion>",       self._drag_move)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # ── title bar ─────────────────────────────────────────────────────────
        bar = tk.Frame(root, bg="#0a0a14", height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Traffic lights
        tl = tk.Frame(bar, bg="#0a0a14")
        tl.pack(side="left", padx=12, pady=15)
        for col, cmd in [("#ff5f57", root.destroy),
                          ("#febc2e", root.iconify),
                          ("#28c840", None)]:
            c = tk.Canvas(tl, width=12, height=12, bg="#0a0a14",
                          highlightthickness=0)
            c.create_oval(1, 1, 11, 11, fill=col, outline="")
            c.pack(side="left", padx=3)
            if cmd:
                c.bind("<Button-1>", lambda e, f=cmd: f())

        tk.Label(bar, text="⌨  Nova DevPanel", bg="#0a0a14", fg=TEXT,
                 font=FONT_BOLD).pack(expand=True)

        # ── project badge ─────────────────────────────────────────────────────
        self._badge_frame = tk.Frame(root, bg=SURFACE, height=38)
        self._badge_frame.pack(fill="x")
        self._badge_frame.pack_propagate(False)

        self._badge_project = tk.Label(
            self._badge_frame, text="No project open", bg=SURFACE,
            fg=MUTED, font=FONT_UI
        )
        self._badge_project.pack(side="left", padx=14, pady=8)

        self._badge_branch = tk.Label(
            self._badge_frame, text="", bg=SURFACE, fg=GREEN,
            font=FONT_MONO
        )
        self._badge_branch.pack(side="left", padx=6)

        self._badge_dirty = tk.Label(
            self._badge_frame, text="", bg=SURFACE, fg=YELLOW, font=FONT_MONO
        )
        self._badge_dirty.pack(side="left", padx=2)

        # ── quick actions bar ──────────────────────────────────────────────────
        qa = tk.Frame(root, bg=SURFACE)
        qa.pack(fill="x", padx=0, pady=(1, 0))
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")

        self._qa_buttons: list[tk.Label] = []
        quick_actions = [
            ("⎇ status",    self._qa_git_status),
            ("✓ commit",    self._qa_commit),
            ("↑ push",      self._qa_push),
            ("⚡ port",      self._qa_port),
            ("▶ run dev",   self._qa_run_dev),
            ("📁 project",  self._qa_pick_project),
            ("⟳ refresh",   self._qa_refresh),
        ]
        for label, cmd in quick_actions:
            btn = tk.Label(
                qa, text=label, bg=SURFACE, fg=MUTED, font=FONT_UI,
                padx=10, pady=6, cursor="hand2"
            )
            btn.pack(side="left", padx=2, pady=4)
            btn.bind("<Button-1>", lambda e, c=cmd: c())
            btn.bind("<Enter>",    lambda e, b=btn: b.config(fg=TEXT, bg=BORDER))
            btn.bind("<Leave>",    lambda e, b=btn: b.config(fg=MUTED, bg=SURFACE))
            self._qa_buttons.append(btn)

        # ── notebook tabs ──────────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dev.TNotebook",
            background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0]
        )
        style.configure(
            "Dev.TNotebook.Tab",
            background=SURFACE, foreground=MUTED,
            font=FONT_UI, padding=[16, 6], borderwidth=0
        )
        style.map(
            "Dev.TNotebook.Tab",
            background=[("selected", BG)],
            foreground=[("selected", TEXT)],
        )

        nb = ttk.Notebook(root, style="Dev.TNotebook")
        nb.pack(fill="both", expand=True, pady=(0, 0))

        # Tab 1 — OUTPUT
        out_frame = tk.Frame(nb, bg=BG)
        nb.add(out_frame, text="  OUTPUT  ")
        self._build_output_tab(out_frame)

        # Tab 2 — GIT
        git_frame = tk.Frame(nb, bg=BG)
        nb.add(git_frame, text="   GIT   ")
        self._build_git_tab(git_frame)

        # Tab 3 — PROJECT
        proj_frame = tk.Frame(nb, bg=BG)
        nb.add(proj_frame, text=" PROJECT ")
        self._build_project_tab(proj_frame)

        self._notebook = nb

        # ── input row ─────────────────────────────────────────────────────────
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
        input_bar = tk.Frame(root, bg="#0a0a14", height=48)
        input_bar.pack(fill="x", side="bottom")
        input_bar.pack_propagate(False)

        self._cmd_entry = tk.Entry(
            input_bar, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", bd=0
        )
        self._cmd_entry.pack(side="left", padx=(14, 6),
                              pady=10, fill="x", expand=True)
        self._cmd_entry.insert(0, "$ shell command…")
        self._cmd_entry.bind("<FocusIn>",  self._clear_cmd_placeholder)
        self._cmd_entry.bind("<FocusOut>", self._restore_cmd_placeholder)
        self._cmd_entry.bind("<Return>",   self._run_cmd_entry)

        run_btn = tk.Label(
            input_bar, text="▶", bg=ACCENT, fg="white",
            font=("Helvetica Neue", 14), padx=10, pady=4, cursor="hand2"
        )
        run_btn.pack(side="right", padx=10, pady=10)
        run_btn.bind("<Button-1>", self._run_cmd_entry)

    # ── OUTPUT tab ────────────────────────────────────────────────────────────

    def _build_output_tab(self, parent: tk.Frame):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", padx=10, pady=(8, 0))

        self._output_label = tk.Label(
            toolbar, text="Nova log", bg=BG, fg=MUTED, font=FONT_UI
        )
        self._output_label.pack(side="left")

        for label, cmd in [("clear", self._clear_output),
                             ("copy all", self._copy_output)]:
            b = tk.Label(toolbar, text=label, bg=BG, fg=MUTED,
                         font=FONT_UI, cursor="hand2")
            b.pack(side="right", padx=8)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>",    lambda e, w=b: w.config(fg=TEXT))
            b.bind("<Leave>",    lambda e, w=b: w.config(fg=MUTED))

        self._output_box = scrolledtext.ScrolledText(
            parent, bg="#080810", fg=GREEN, insertbackground=GREEN,
            font=FONT_MONO, relief="flat", bd=0, wrap="word",
            selectbackground=BORDER
        )
        self._output_box.pack(fill="both", expand=True, padx=10, pady=8)
        self._output_box.configure(state="disabled")

        # Colour tags
        self._output_box.tag_configure("info",   foreground=TEXT)
        self._output_box.tag_configure("warn",   foreground=YELLOW)
        self._output_box.tag_configure("error",  foreground=RED)
        self._output_box.tag_configure("action", foreground=PURPLE)
        self._output_box.tag_configure("user",   foreground=ACCENT)
        self._output_box.tag_configure("nova",   foreground=GREEN)
        self._output_box.tag_configure("cmd",    foreground=ORANGE)
        self._output_box.tag_configure("dim",    foreground=MUTED)

    def _append_output(self, text: str, tag: str = "info"):
        """Thread-safe append to the output box."""
        def _do():
            self._output_box.configure(state="normal")
            self._output_box.insert("end", text, tag)
            self._output_box.see("end")
            self._output_box.configure(state="disabled")
        self.root.after(0, _do)

    def _clear_output(self):
        self._output_box.configure(state="normal")
        self._output_box.delete("1.0", "end")
        self._output_box.configure(state="disabled")

    def _copy_output(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._output_box.get("1.0", "end"))

    # ── GIT tab ───────────────────────────────────────────────────────────────

    def _build_git_tab(self, parent: tk.Frame):
        # Status area
        self._git_status_text = scrolledtext.ScrolledText(
            parent, bg="#080810", fg=TEXT, font=FONT_MONO,
            relief="flat", bd=0, height=10, wrap="word"
        )
        self._git_status_text.pack(fill="x", padx=10, pady=(10, 0))
        self._git_status_text.configure(state="disabled")

        # Log area label
        tk.Label(parent, text="RECENT COMMITS", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 9)).pack(anchor="w", padx=14, pady=(10, 2))

        self._git_log_text = scrolledtext.ScrolledText(
            parent, bg="#080810", fg=TEXT, font=FONT_MONO,
            relief="flat", bd=0, wrap="word"
        )
        self._git_log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._git_log_text.configure(state="disabled")

        self._git_log_text.tag_configure("hash",  foreground=YELLOW)
        self._git_log_text.tag_configure("msg",   foreground=TEXT)
        self._git_log_text.tag_configure("time",  foreground=MUTED)

    def _update_git_tab(self):
        cwd = self._project
        if not cwd:
            return

        # Status
        out, _, _ = _sh("git status --short", cwd=cwd)
        self._git_status_text.configure(state="normal")
        self._git_status_text.delete("1.0", "end")
        self._git_status_text.insert("end", out or "Clean working tree ✓")
        self._git_status_text.configure(state="disabled")

        # Log
        log_out, _, _ = _sh(
            "git log --oneline -n 20 --pretty=format:'%h|%s|%cr'", cwd=cwd
        )
        self._git_log_text.configure(state="normal")
        self._git_log_text.delete("1.0", "end")
        for line in log_out.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                h, msg, t = parts
                self._git_log_text.insert("end", h + " ", "hash")
                self._git_log_text.insert("end", msg, "msg")
                self._git_log_text.insert("end", f"  ({t})\n", "time")
        self._git_log_text.configure(state="disabled")

    # ── PROJECT tab ───────────────────────────────────────────────────────────

    def _build_project_tab(self, parent: tk.Frame):
        tk.Label(parent, text="PROJECTS FOUND", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 9)).pack(anchor="w", padx=14, pady=(12, 4))

        list_frame = tk.Frame(parent, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=10)

        sb = tk.Scrollbar(list_frame, orient="vertical", bg=SURFACE)
        self._proj_listbox = tk.Listbox(
            list_frame, bg="#080810", fg=TEXT, font=FONT_MONO,
            relief="flat", bd=0, selectbackground=BORDER,
            selectforeground=TEXT, activestyle="none",
            yscrollcommand=sb.set
        )
        sb.config(command=self._proj_listbox.yview)
        self._proj_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._proj_listbox.bind("<Double-Button-1>", self._open_selected_project)
        self._proj_listbox.bind("<Return>",           self._open_selected_project)

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", padx=10, pady=8)
        for label, cmd in [
            ("Open in VS Code",     self._proj_open_vscode),
            ("Open Terminal",       self._proj_open_terminal),
            ("Set as active",       self._proj_set_active),
        ]:
            b = tk.Label(btn_row, text=label, bg=SURFACE, fg=MUTED,
                         font=FONT_UI, padx=10, pady=6, cursor="hand2")
            b.pack(side="left", padx=4)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>",    lambda e, w=b: w.config(fg=TEXT))
            b.bind("<Leave>",    lambda e, w=b: w.config(fg=MUTED))

        self._refresh_project_list()

    def _refresh_project_list(self):
        projects = _find_projects()
        self._all_projects = projects
        self._proj_listbox.delete(0, "end")
        active = self._project
        for p in projects:
            name = Path(p).name
            prefix = "▶ " if p == active else "  "
            self._proj_listbox.insert("end", prefix + name)

    def _selected_project(self) -> Optional[str]:
        sel = self._proj_listbox.curselection()
        if not sel or not self._all_projects:
            return None
        idx = sel[0]
        return self._all_projects[idx] if idx < len(self._all_projects) else None

    def _open_selected_project(self, _e=None):
        p = self._selected_project()
        if p:
            self._set_project(p)

    def _proj_open_vscode(self):
        p = self._selected_project() or self._project
        if p:
            _sh(f"code '{p}'")
            self._append_output(f"→ VS Code: {Path(p).name}\n", "cmd")

    def _proj_open_terminal(self):
        p = self._selected_project() or self._project
        if p:
            _sh(
                f"osascript -e 'tell application \"Terminal\" to do script \"cd \\\"{p}\\\"\"'"
            )
            self._append_output(f"→ Terminal: {p}\n", "cmd")

    def _proj_set_active(self):
        p = self._selected_project()
        if p:
            self._set_project(p)
            self._save_active_project(p)

    def _save_active_project(self, path: str):
        try:
            import sqlite3
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT OR REPLACE INTO user_facts (key, value) VALUES (?, ?)",
                ("active_project", path)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self._append_output(f"Could not save to DB: {e}\n", "error")

    # ── project state ─────────────────────────────────────────────────────────

    def _set_project(self, path: str):
        self._project = path
        name = Path(path).name
        self._badge_project.config(text=f"⬡  {name}", fg=TEXT)
        self._append_output(f"[project] → {path}\n", "cmd")
        self._update_git_badge()
        self._update_git_tab()

    def _refresh_project(self):
        path = _active_project_from_db()
        if path and os.path.isdir(path):
            self._set_project(path)

    # ── git badge (runs on background thread) ─────────────────────────────────

    def _update_git_badge(self):
        cwd = self._project
        if not cwd:
            return
        branch, _, _ = _sh("git branch --show-current", cwd=cwd)
        status, _, _ = _sh("git status --short", cwd=cwd)
        dirty = len([l for l in status.splitlines() if l.strip()])

        self._branch = branch or ""
        self._dirty  = dirty

        def _do():
            self._badge_branch.config(
                text=f"⎇ {branch}" if branch else ""
            )
            self._badge_dirty.config(
                text=f"  ✎ {dirty} dirty" if dirty else "  ✓ clean",
                fg=YELLOW if dirty else GREEN
            )
        self.root.after(0, _do)

    # ── quick actions ─────────────────────────────────────────────────────────

    def _qa_git_status(self):
        cwd = self._project
        if not cwd:
            self._append_output("[!] No active project\n", "warn")
            return
        out, _, _ = _sh("git status", cwd=cwd)
        self._append_output(out + "\n", "info")
        self._notebook.select(0)   # switch to OUTPUT tab

    def _qa_commit(self):
        cwd = self._project
        if not cwd:
            self._append_output("[!] No active project\n", "warn")
            return
        msg = simpledialog.askstring(
            "Commit message", "Enter commit message (blank = AI-generated):",
            parent=self.root
        )
        if msg is None:
            return   # cancelled
        self._append_output(f"$ git add -A && git commit…\n", "cmd")

        def _run():
            _sh("git add -A", cwd=cwd)
            staged, _, _ = _sh("git diff --cached --stat", cwd=cwd)
            if not staged:
                self._append_output("[!] Nothing staged\n", "warn")
                return
            if not msg:
                # AI message via Claude
                diff, _, _ = _sh("git diff --cached", cwd=cwd)
                try:
                    import anthropic as ant
                    from config import CONFIG
                    client = ant.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])
                    resp = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=80,
                        messages=[{
                            "role": "user",
                            "content": (
                                "Write ONE conventional commit message (type: short description, "
                                "max 72 chars, no quotes, no explanation):\n\n" + diff[:3000]
                            )
                        }]
                    )
                    commit_msg = resp.content[0].text.strip().strip('"').strip("'")
                except Exception as e:
                    commit_msg = "chore: update files"
                    self._append_output(f"[AI error] {e}\n", "warn")
            else:
                commit_msg = msg

            out, err, rc = _sh(["git", "commit", "-m", commit_msg], cwd=cwd)
            if rc == 0:
                self._append_output(f'✓ Committed: "{commit_msg}"\n', "nova")
            else:
                self._append_output(f"[commit error] {err}\n", "error")
            self._update_git_badge()
            self._update_git_tab()

        self._notebook.select(0)
        threading.Thread(target=_run, daemon=True).start()

    def _qa_push(self):
        cwd = self._project
        if not cwd:
            self._append_output("[!] No active project\n", "warn")
            return
        self._append_output("$ git push…\n", "cmd")

        def _run():
            out, err, rc = _sh("git push", cwd=cwd)
            result = out or err
            tag    = "nova" if rc == 0 else "error"
            self._append_output((result or "Done") + "\n", tag)

        self._notebook.select(0)
        threading.Thread(target=_run, daemon=True).start()

    def _qa_port(self):
        port = simpledialog.askstring(
            "Port check", "Which port?", parent=self.root
        )
        if not port:
            return
        out, _, _ = _sh(f"lsof -iTCP:{port} -sTCP:LISTEN -n -P")
        if out:
            lines   = out.splitlines()
            summary = "\n".join(lines[:6])
            self._append_output(f"Port {port}:\n{summary}\n", "info")
            if messagebox.askyesno("Kill it?", f"Kill process on port {port}?",
                                   parent=self.root):
                pids_out, _, _ = _sh(f"lsof -ti TCP:{port} -sTCP:LISTEN")
                for pid in pids_out.splitlines():
                    _sh(f"kill -9 {pid.strip()}")
                self._append_output(f"✓ Killed port {port}\n", "nova")
        else:
            self._append_output(f"Nothing on port {port}\n", "dim")
        self._notebook.select(0)

    def _qa_run_dev(self):
        cwd = self._project
        if not cwd:
            self._append_output("[!] No active project\n", "warn")
            return

        # Detect package manager
        pm = "npm"
        for m in ("pnpm", "yarn", "bun"):
            if _sh(f"which {m}")[2] == 0:
                pm = m
                break

        if (Path(cwd) / "package.json").exists():
            cmd = f"{pm} run dev"
        elif (Path(cwd) / "Makefile").exists():
            cmd = "make dev"
        elif (Path(cwd) / "Cargo.toml").exists():
            cmd = "cargo run"
        else:
            cmd = "python main.py"

        self._append_output(f"$ {cmd}  (background)\n", "cmd")
        subprocess.Popen(cmd, shell=True, cwd=cwd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._notebook.select(0)

    def _qa_pick_project(self):
        self._notebook.select(2)   # switch to PROJECT tab

    def _qa_refresh(self):
        self._update_git_badge()
        self._update_git_tab()
        self._refresh_project_list()
        self._append_output("⟳ Refreshed\n", "dim")

    # ── input bar ─────────────────────────────────────────────────────────────

    def _clear_cmd_placeholder(self, _e=None):
        if self._cmd_entry.get().startswith("$ "):
            self._cmd_entry.delete(0, "end")

    def _restore_cmd_placeholder(self, _e=None):
        if not self._cmd_entry.get():
            self._cmd_entry.insert(0, "$ shell command…")

    def _run_cmd_entry(self, _e=None):
        raw = self._cmd_entry.get().strip()
        if not raw or raw.startswith("$ "):
            return
        cmd = raw.lstrip("$ ").strip()
        self._cmd_entry.delete(0, "end")
        self._append_output(f"$ {cmd}\n", "cmd")
        cwd = self._project

        def _run():
            r = subprocess.run(
                cmd, shell=True, capture_output=True,
                text=True, cwd=cwd, timeout=60
            )
            out = r.stdout.strip()
            err = r.stderr.strip()
            if out:
                self._append_output(out + "\n", "info")
            if err:
                self._append_output(err + "\n", "warn" if r.returncode == 0 else "error")
            if not out and not err:
                self._append_output(f"(exit {r.returncode})\n", "dim")

        self._notebook.select(0)
        threading.Thread(target=_run, daemon=True).start()

    # ── background threads ────────────────────────────────────────────────────

    def _start_background_threads(self):
        # Log tailer
        threading.Thread(target=self._tail_log, daemon=True, name="LogTailer").start()
        # Git badge refresh
        threading.Thread(target=self._git_poller, daemon=True, name="GitPoller").start()
        # IPC command reader (Nova → DevUI)
        threading.Thread(target=self._ipc_reader, daemon=True, name="IPCReader").start()

    def _tail_log(self):
        """Tail nova.log and stream colourised output into the OUTPUT box."""
        colour_rules = [
            (re.compile(r"\[U\]"), "user"),
            (re.compile(r"\[I\].*nova:"), "nova"),
            (re.compile(r"\[W\]"), "warn"),
            (re.compile(r"\[E\]"), "error"),
            (re.compile(r"Action '"), "action"),
        ]
        while self._running:
            if LOG_PATH.exists():
                with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
                    f.seek(self._log_pos)
                    chunk = f.read()
                    self._log_pos = f.tell()
                if chunk:
                    for line in chunk.splitlines(keepends=True):
                        tag = "dim"
                        for pattern, t in colour_rules:
                            if pattern.search(line):
                                tag = t
                                break
                        self._append_output(line, tag)
            time.sleep(0.5)

    def _git_poller(self):
        while self._running:
            if self._project:
                self._update_git_badge()
            time.sleep(4)

    def _ipc_reader(self):
        """
        Read the .nova_cmd pipe. Nova writes action results here;
        dev_ui echoes them to the output panel.
        """
        last_mtime = 0
        while self._running:
            if CMD_PIPE.exists():
                mtime = CMD_PIPE.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    try:
                        content = CMD_PIPE.read_text().strip()
                        if content:
                            self._append_output(f"[Nova] {content}\n", "nova")
                    except Exception:
                        pass
            time.sleep(0.3)

    # ── run ───────────────────────────────────────────────────────────────────

    def run(self):
        self._append_output(
            "Nova DevPanel ready. Double-click a project or use the toolbar.\n",
            "nova"
        )
        try:
            self.root.mainloop()
        finally:
            self._running = False


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DevUI().run()
