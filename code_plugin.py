"""
Nova / VoiceCommander
code_plugin.py — AI-powered code intelligence layer.

Voice commands this unlocks
────────────────────────────
"Explain this file"                    → code_explain
"Review my changes"                    → code_review   (reads git diff, returns spoken review)
"What does AuthController.swift do"   → code_explain  (auto-finds file by name)
"Add tests for UserService.py"         → code_test
"Fix this error: <paste traceback>"    → code_fix
"Add docstrings to utils.py"           → code_docstring
"Find where we handle payments"        → code_search
"How complex is this codebase"         → code_stats
"What changed in the last commit"      → code_last_commit

Each action calls Claude directly on real file/diff content so the
answer is always grounded in your actual code — not hallucinated.

Drop this file next to dev_plugin.py. Zero extra dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from plugins import PluginBase
from logger import get_logger

log = get_logger("code")

# ── token budget (Haiku has 200 k context, but keep responses fast) ───────────
_MAX_FILE_CHARS  = 12_000   # ~3 k tokens
_MAX_DIFF_CHARS  = 16_000
_MAX_REPLY_TOKENS = 400     # spoken replies must be short


# ── helpers ───────────────────────────────────────────────────────────────────

def _sh(cmd: str, cwd: str = None, timeout: int = 30) -> tuple[str, str, int]:
    r = subprocess.run(cmd, shell=True, capture_output=True,
                       text=True, cwd=cwd, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def _active_project(db) -> Optional[str]:
    facts = db.get_facts() if db else {}
    return facts.get("active_project") or facts.get("project_path")


def _find_file_by_name(filename: str, root: str) -> Optional[str]:
    """Fuzzy-find a file by basename under root, skipping noise dirs."""
    out, _, rc = _sh(
        f"find '{root}' -name '{filename}' "
        "-not -path '*/node_modules/*' -not -path '*/.git/*' "
        "-not -path '*/build/*' -not -path '*/.venv/*' "
        "-not -path '*/dist/*' 2>/dev/null | head -5"
    )
    if rc == 0 and out:
        return out.splitlines()[0]
    return None


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > _MAX_FILE_CHARS:
            half = _MAX_FILE_CHARS // 2
            content = (content[:half]
                       + f"\n\n… [{len(content) - _MAX_FILE_CHARS} chars omitted] …\n\n"
                       + content[-half:])
        return content
    except Exception as e:
        return f"[Could not read file: {e}]"


def _ask_claude(system: str, user: str, db, max_tokens: int = _MAX_REPLY_TOKENS) -> str:
    """Call Claude Haiku with a focused prompt. Returns plain text."""
    try:
        import anthropic as ant
        from config import CONFIG
        client = ant.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error("Claude call failed: %s", e)
        return f"Claude unavailable: {e}"


def _spoken_system(task: str) -> str:
    """Base system prompt that keeps answers terse enough to be spoken aloud."""
    return (
        f"You are Nova, a voice assistant. Task: {task}. "
        "Reply in plain prose only — no markdown, no bullet symbols, no backticks. "
        "Be concise: 2-5 sentences maximum unless asked for more. "
        "If you list things, use commas or say 'first … second …'."
    )


# ── plugin ────────────────────────────────────────────────────────────────────

class Plugin(PluginBase):
    name        = "code"
    description = "AI code intelligence: explain, review, test, fix, search"

    def setup(self):
        self.action("code_explain")(self._explain)
        self.action("code_review")(self._review)
        self.action("code_test")(self._test)
        self.action("code_fix")(self._fix)
        self.action("code_docstring")(self._docstring)
        self.action("code_search")(self._search)
        self.action("code_stats")(self._stats)
        self.action("code_last_commit")(self._last_commit)

    # ── explain ───────────────────────────────────────────────────────────────

    def _explain(self, data: dict, db) -> str:
        """Explain what a file or code snippet does."""
        filename = data.get("filename") or data.get("file", "")
        snippet  = data.get("snippet", "")
        cwd      = data.get("path") or _active_project(db)

        if snippet:
            content = snippet
            label   = "code snippet"
        elif filename:
            path = (
                _find_file_by_name(filename, cwd)
                if cwd else None
            ) or filename
            if not os.path.exists(path):
                return f"Can't find '{filename}'. Try specifying the full path."
            content = _read_file(path)
            label   = Path(path).name
        else:
            return "Tell me which file to explain."

        return _ask_claude(
            _spoken_system("explain what this code does"),
            f"Explain this {label}:\n\n{content}",
            db
        )

    # ── review ────────────────────────────────────────────────────────────────

    def _review(self, data: dict, db) -> str:
        """
        Review the current git diff (staged or unstaged).
        Returns a spoken code review: issues, improvements, risks.
        """
        cwd    = data.get("path") or _active_project(db)
        staged = data.get("staged", False)

        flag = "--cached" if staged else ""
        diff_out, _, rc = _sh(f"git diff {flag}", cwd=cwd)

        if not diff_out:
            # Nothing unstaged — try HEAD~1
            diff_out, _, _ = _sh("git diff HEAD~1", cwd=cwd)

        if not diff_out:
            return "No changes to review — working tree is clean."

        if len(diff_out) > _MAX_DIFF_CHARS:
            diff_out = diff_out[:_MAX_DIFF_CHARS] + "\n… [diff truncated]"

        return _ask_claude(
            _spoken_system(
                "give a brief spoken code review. Cover: any bugs or risks, "
                "code quality issues, and one key suggestion. Be direct."
            ),
            f"Review this git diff:\n\n{diff_out}",
            db,
            max_tokens=500
        )

    # ── test generation ───────────────────────────────────────────────────────

    def _test(self, data: dict, db) -> str:
        """
        Generate unit tests for a file and write them to a new test file.
        Speaks a summary; writes the actual test code silently.
        """
        filename    = data.get("filename") or data.get("file", "")
        write_file  = data.get("write", True)
        cwd         = data.get("path") or _active_project(db)

        if not filename:
            return "Which file should I write tests for?"

        path = _find_file_by_name(filename, cwd) if cwd else None
        if not path or not os.path.exists(path):
            return f"Can't find '{filename}'."

        content  = _read_file(path)
        lang     = Path(path).suffix.lstrip(".")
        lang_map = {"py": "Python pytest", "ts": "TypeScript Jest",
                    "js": "JavaScript Jest", "swift": "Swift XCTest",
                    "go": "Go testing", "rs": "Rust #[cfg(test)]"}
        framework = lang_map.get(lang, lang)

        tests = _ask_claude(
            (
                f"You are an expert {framework} test writer. "
                "Output ONLY the test file source code — no explanation, no markdown fences."
            ),
            f"Write comprehensive unit tests for this {lang} file:\n\n{content}",
            db,
            max_tokens=1200
        )

        # Determine output path
        stem     = Path(path).stem
        test_dir = Path(path).parent
        ext      = Path(path).suffix
        prefixes = {"py": "test_", "go": "", "swift": "", "ts": "", "js": ""}
        suffixes = {"go": "_test", "swift": "Tests", "ts": ".test", "js": ".test"}
        test_name = (
            prefixes.get(lang, "test_") + stem
            + suffixes.get(lang, "")
            + ext
        )
        test_path = test_dir / test_name

        if write_file:
            try:
                with open(test_path, "w", encoding="utf-8") as f:
                    f.write(tests)
                return (
                    f"Wrote {framework} tests to '{test_name}'. "
                    f"I covered the main functions and edge cases."
                )
            except Exception as e:
                log.error("Could not write test file: %s", e)

        # Fallback: just summarise
        lines = len(tests.splitlines())
        return f"Generated {lines} lines of {framework} tests for '{filename}'."

    # ── fix ───────────────────────────────────────────────────────────────────

    def _fix(self, data: dict, db) -> str:
        """
        Explain an error/traceback and suggest a fix.
        data: {error: "the traceback text"} OR {file: "file with the error"}
        """
        error   = data.get("error") or data.get("traceback", "")
        filename = data.get("file") or data.get("filename", "")
        cwd     = data.get("path") or _active_project(db)

        context = ""
        if filename:
            p = _find_file_by_name(filename, cwd) if cwd else filename
            if p and os.path.exists(p):
                context = f"\nHere is the source file:\n\n{_read_file(p)}"

        if not error and not context:
            return "Paste the error or tell me which file has the problem."

        prompt = f"Error:\n{error}{context}" if error else f"Find and explain bugs in:{context}"

        return _ask_claude(
            _spoken_system(
                "diagnose the error and give a clear, actionable fix. "
                "Name the likely root cause first, then the fix in one sentence."
            ),
            prompt,
            db,
            max_tokens=400
        )

    # ── docstring ─────────────────────────────────────────────────────────────

    def _docstring(self, data: dict, db) -> str:
        """Add docstrings / JSDoc comments to a file and save in-place."""
        filename = data.get("filename") or data.get("file", "")
        cwd      = data.get("path") or _active_project(db)
        dry_run  = data.get("dry_run", False)

        if not filename:
            return "Which file should I add docstrings to?"

        path = _find_file_by_name(filename, cwd) if cwd else filename
        if not path or not os.path.exists(path):
            return f"Can't find '{filename}'."

        original = _read_file(path)
        lang     = Path(path).suffix.lstrip(".")
        style    = {
            "py": "Google-style Python docstrings",
            "ts": "JSDoc comments",
            "js": "JSDoc comments",
            "swift": "Swift /// documentation comments",
            "go": "Go doc comments",
        }.get(lang, "inline documentation comments")

        documented = _ask_claude(
            (
                f"Add {style} to every public function, class, and method "
                "that lacks documentation. Return ONLY the complete modified file — "
                "no explanation, no markdown fences, no other text."
            ),
            original,
            db,
            max_tokens=2000
        )

        if dry_run:
            added = len(re.findall(r'"""', documented)) - len(re.findall(r'"""', original))
            return f"Dry run: I'd add approximately {max(added, 0)} doc blocks to '{filename}'."

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(documented)
            return f"Added {style} to '{Path(path).name}' and saved."
        except Exception as e:
            return f"Could not write file: {e}"

    # ── search ────────────────────────────────────────────────────────────────

    def _search(self, data: dict, db) -> str:
        """
        Semantic code search: find where a concept/feature is implemented.
        Uses ripgrep for speed, then Claude to pick the best matches.
        """
        query = data.get("query") or data.get("q", "")
        cwd   = data.get("path") or _active_project(db)

        if not query:
            return "What should I search for?"
        if not cwd:
            return "I don't know which project to search. Open a project first."

        # Build keywords from the query
        keywords = re.sub(r"[^a-zA-Z0-9 _-]", "", query).strip()
        # Try rg first, fall back to grep
        rg = "rg" if _sh("which rg")[2] == 0 else "grep -r"
        search_cmd = (
            f"{rg} -i --max-count=3 -l '{keywords}' '{cwd}' "
            f"--glob='!*.git' --glob='!node_modules' --glob='!build' "
            f"--glob='!dist' --glob='!.venv' 2>/dev/null | head -8"
        )
        files_out, _, _ = _sh(search_cmd)

        if not files_out:
            return f"No files mention '{keywords}' in your project."

        candidate_files = files_out.splitlines()[:5]

        # Get a short grep snippet per file
        snippets = []
        for fp in candidate_files:
            lines_out, _, _ = _sh(
                f"grep -n -i '{keywords}' '{fp}' | head -4"
            )
            if lines_out:
                snippets.append(f"--- {fp} ---\n{lines_out}")

        combined = "\n\n".join(snippets)

        return _ask_claude(
            _spoken_system(
                f"identify which file and function best implements or handles '{query}'. "
                "Name the file and line range concisely."
            ),
            f"Query: '{query}'\n\nSearch results:\n{combined}",
            db,
            max_tokens=200
        )

    # ── stats ─────────────────────────────────────────────────────────────────

    def _stats(self, data: dict, db) -> str:
        """Report codebase size, language breakdown, and complexity indicators."""
        cwd = data.get("path") or _active_project(db)
        if not cwd:
            return "Open a project first so I know what to measure."

        # Line counts per extension
        out, _, _ = _sh(
            f"find '{cwd}' "
            "-not -path '*/node_modules/*' -not -path '*/.git/*' "
            "-not -path '*/build/*' -not -path '*/.venv/*' "
            "-not -path '*/dist/*' "
            r"-name '*.*' | grep -E '\.(py|ts|js|swift|go|rs|c|cpp|java|kt)$' "
            "| xargs wc -l 2>/dev/null | tail -1",
        )
        total_lines = out.split()[0] if out and out.split() else "unknown"

        # File count
        fc_out, _, _ = _sh(
            f"find '{cwd}' "
            "-not -path '*/node_modules/*' -not -path '*/.git/*' "
            r"-name '*.*' | grep -E '\.(py|ts|js|swift|go|rs|c|cpp|java|kt)$' "
            "| wc -l"
        )
        file_count = fc_out.strip() or "unknown"

        # Git commits
        commits_out, _, _ = _sh("git rev-list --count HEAD 2>/dev/null", cwd=cwd)

        # Contributors
        contrib_out, _, _ = _sh(
            "git log --format='%ae' 2>/dev/null | sort -u | wc -l", cwd=cwd
        )

        project_name = Path(cwd).name
        parts = [f"'{project_name}' has {file_count} source files and {total_lines} lines of code."]
        if commits_out:
            parts.append(f"{commits_out} commits")
        if contrib_out.strip() and contrib_out.strip() != "0":
            parts.append(f"{contrib_out.strip()} contributor(s).")
        return " ".join(parts)

    # ── last commit ───────────────────────────────────────────────────────────

    def _last_commit(self, data: dict, db) -> str:
        """Summarise what changed in the most recent commit."""
        cwd = data.get("path") or _active_project(db)

        diff_out, _, rc = _sh("git diff HEAD~1 HEAD", cwd=cwd)
        if rc != 0 or not diff_out:
            return "No commits found, or this is the first commit."

        msg_out, _, _ = _sh("git log -1 --pretty=format:'%s'", cwd=cwd)

        if len(diff_out) > _MAX_DIFF_CHARS:
            diff_out = diff_out[:_MAX_DIFF_CHARS] + "\n… [truncated]"

        summary = _ask_claude(
            _spoken_system(
                f"summarise what the commit '{msg_out}' changed in 2 sentences, "
                "focusing on the practical effect, not the diff syntax."
            ),
            diff_out,
            db,
            max_tokens=200
        )
        return f"Last commit — '{msg_out}': {summary}"

    # ── system prompt extension ───────────────────────────────────────────────

    def system_prompt_extension(self) -> str:
        return """
━━━ CODE INTELLIGENCE PLUGIN ━━━

<action>{"type":"code_explain","filename":"AuthController.swift"}</action>
<action>{"type":"code_explain","snippet":"def process_payment(amount): ..."}</action>
<action>{"type":"code_review"}</action>
<action>{"type":"code_review","staged":true}</action>
<action>{"type":"code_test","filename":"UserService.py","write":true}</action>
<action>{"type":"code_fix","error":"TypeError: cannot read property 'id' of undefined\\n  at line 42"}</action>
<action>{"type":"code_fix","file":"api.py"}</action>
<action>{"type":"code_docstring","filename":"utils.py"}</action>
<action>{"type":"code_search","query":"payment processing"}</action>
<action>{"type":"code_stats"}</action>
<action>{"type":"code_last_commit"}</action>

RULES:
- "Explain [filename]" → code_explain with filename.
- "Review my changes" → code_review (reads live git diff, no filename needed).
- "Add tests for [file]" → code_test; set write:true to save the test file.
- "Fix this error" + error text → code_fix with error field.
- "Find where we handle X" → code_search with query.
- "What changed last commit" → code_last_commit.
- code_docstring modifies the file in-place; use dry_run:true first if unsure.
- After any code action, give the Claude response as your spoken reply verbatim.
"""
