#!/usr/bin/env python3
"""
Universal AI Agent Skill Discovery, Search, Retrieval & Cross-Harness Tool
==========================================================================
Discovers, indexes, searches, previews, and links agent skills across multiple
AI agent harnesses (.claude, .gemini, .agents, .cursor, .windsurf, .roo, .cline,
.continue, .goose, .aider, .openhands, etc.) so AI agents and human developers
can seamlessly discover, reuse, and share skills without re-downloading or duplicating.

Supported Harnesses & Environments:
  • Claude Code / Desktop (~/.claude/skills, plugins, workspaces)
  • Gemini CLI / Google Antigravity (~/.gemini/skills, config/plugins, builtin)
  • Universal Agent Skills Standard (~/.agents/skills, ~/.skills)
  • Cursor IDE (~/.cursor/skills, ~/.cursor/rules, .cursorrules)
  • Windsurf IDE (~/.codeium/windsurf, ~/.windsurf/skills, rules)
  • Roo Code / Cline (~/.roo/skills, ~/.cline/skills, VS Code globalStorage)
  • Goose AI Agent (~/.config/goose/skills, ~/.goose/skills)
  • Continue.dev (~/.continue/prompts, ~/.continue/skills)
  • Aider (~/.aider/prompts, ~/.aider/skills)
  • OpenHands (~/.openhands/skills)
  • GitHub Copilot Workspace (.github/skills, .copilot/skills)
  • Custom paths via $AGENT_SKILLS_PATH

Author: Antigravity AI Pair Programmer
Version: 1.2.0
"""

import os
import sys
import json
import re
import argparse
import shutil
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# Cross-platform safe UTF-8 stdout configuration
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==============================================================================
# HARNESS REGISTRY & DISCOVERY PATHS
# ==============================================================================

def get_home_dir() -> Path:
    """Returns the user's home directory across Windows, macOS, and Linux."""
    return Path.home()


def get_harness_definitions(workspace_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Returns discovery matrix for known AI agent harnesses.
    Includes Global, Plugin, Builtin, and Workspace paths for each harness.
    """
    home = get_home_dir()
    ws = workspace_root or Path.cwd()

    harnesses = [
        {
            "id": "claude",
            "name": "Claude Code / Desktop",
            "vendor": "Anthropic",
            "global_paths": [
                home / ".claude" / "skills",
                home / ".config" / "claude" / "skills",
            ],
            "plugin_paths": [
                home / ".claude" / "plugins" / "marketplaces",
                home / ".claude" / "plugins" / "cache",
                home / ".claude" / "plugins",
            ],
            "workspace_paths": [
                ws / ".claude" / "skills",
                ws / ".claude" / "plugins",
            ],
            "rules_paths": [
                ws / "CLAUDE.md",
                home / ".claude" / "CLAUDE.md",
            ]
        },
        {
            "id": "gemini",
            "name": "Gemini CLI / Antigravity",
            "vendor": "Google",
            "global_paths": [
                home / ".gemini" / "skills",
                home / ".gemini" / "config" / "skills",
            ],
            "plugin_paths": [
                home / ".gemini" / "config" / "plugins",
                home / ".gemini" / "plugins",
            ],
            "builtin_paths": [
                home / ".gemini" / "antigravity-cli" / "builtin" / "skills",
            ],
            "workspace_paths": [
                ws / ".gemini" / "skills",
                ws / ".gemini" / "plugins",
            ],
            "rules_paths": [
                ws / "GEMINI.md",
                home / ".gemini" / "config" / "rules",
            ]
        },
        {
            "id": "agents",
            "name": "Universal Agent Skills Standard",
            "vendor": "Open Standard",
            "global_paths": [
                home / ".agents" / "skills",
                home / ".skills",
            ],
            "plugin_paths": [
                home / ".agents" / "plugins",
            ],
            "workspace_paths": [
                ws / ".agents" / "skills",
                ws / ".skills",
            ],
            "rules_paths": [
                ws / "AGENTS.md",
                ws / ".agents" / "rules",
            ]
        },
        {
            "id": "cursor",
            "name": "Cursor IDE",
            "vendor": "Anysphere",
            "global_paths": [
                home / ".cursor" / "skills",
                home / ".cursor" / "rules",
            ],
            "workspace_paths": [
                ws / ".cursor" / "skills",
                ws / ".cursor" / "rules",
            ],
            "rules_paths": [
                ws / ".cursorrules",
            ]
        },
        {
            "id": "windsurf",
            "name": "Windsurf IDE",
            "vendor": "Codeium",
            "global_paths": [
                home / ".codeium" / "windsurf" / "skills",
                home / ".windsurf" / "skills",
                home / ".codeium" / "windsurf" / "rules",
            ],
            "workspace_paths": [
                ws / ".windsurf" / "skills",
                ws / ".windsurf" / "rules",
            ],
            "rules_paths": [
                ws / ".windsurfrules",
            ]
        },
        {
            "id": "roo",
            "name": "Roo Code (Roo Clinic)",
            "vendor": "RooVeterinaryInc",
            "global_paths": [
                home / ".roo" / "skills",
                home / ".config" / "Code" / "User" / "globalStorage" / "rooveterinaryinc.roo-cline" / "skills",
                home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "rooveterinaryinc.roo-cline" / "skills",
            ],
            "workspace_paths": [
                ws / ".roo" / "skills",
            ],
            "rules_paths": [
                ws / ".roomodes",
            ]
        },
        {
            "id": "cline",
            "name": "Cline",
            "vendor": "Cline",
            "global_paths": [
                home / ".cline" / "skills",
                home / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "skills",
                home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "skills",
            ],
            "workspace_paths": [
                ws / ".cline" / "skills",
            ],
            "rules_paths": [
                ws / ".clinerules",
            ]
        },
        {
            "id": "goose",
            "name": "Goose AI Agent",
            "vendor": "Block",
            "global_paths": [
                home / ".config" / "goose" / "skills",
                home / ".goose" / "skills",
            ],
            "workspace_paths": [
                ws / ".goose" / "skills",
            ]
        },
        {
            "id": "continue",
            "name": "Continue.dev",
            "vendor": "Continue",
            "global_paths": [
                home / ".continue" / "prompts",
                home / ".continue" / "skills",
                home / ".continue" / "rules",
            ],
            "workspace_paths": [
                ws / ".continue" / "prompts",
                ws / ".continue" / "skills",
                ws / ".continue" / "rules",
            ]
        },
        {
            "id": "aider",
            "name": "Aider",
            "vendor": "Paul Gauthier",
            "global_paths": [
                home / ".aider" / "prompts",
                home / ".aider" / "skills",
            ],
            "workspace_paths": [
                ws / ".aider" / "prompts",
                ws / ".aider" / "skills",
            ]
        },
        {
            "id": "openhands",
            "name": "OpenHands (OpenDevin)",
            "vendor": "OpenHands",
            "global_paths": [
                home / ".openhands" / "skills",
            ],
            "workspace_paths": [
                ws / ".openhands" / "skills",
            ]
        },
        {
            "id": "copilot",
            "name": "GitHub Copilot Workspace",
            "vendor": "GitHub",
            "workspace_paths": [
                ws / ".github" / "skills",
                ws / ".copilot" / "skills",
            ],
            "rules_paths": [
                ws / ".github" / "copilot-instructions.md",
            ]
        }
    ]

    # Support custom environment variable paths
    custom_env = os.environ.get("AGENT_SKILLS_PATH")
    if custom_env:
        custom_paths = [Path(p.strip()) for p in custom_env.split(os.pathsep) if p.strip()]
        harnesses.append({
            "id": "custom",
            "name": "Custom ($AGENT_SKILLS_PATH)",
            "vendor": "User Configured",
            "global_paths": custom_paths,
            "workspace_paths": []
        })

    return harnesses


# ==============================================================================
# ROBUST YAML FRONTMATTER & METADATA PARSER
# ==============================================================================

def parse_yaml_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Fast, zero-dependency YAML frontmatter parser for SKILL.md.
    Correctly parses multiline scalars (|, >), lists (- item), and key-values.
    """
    metadata: Dict[str, Any] = {}
    body = content

    pattern = r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        return metadata, body

    yaml_text = match.group(1)
    body = match.group(2).strip()

    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Check for top-level key: value
        if not line.startswith(" ") and not line.startswith("\t") and ":" in line:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val = parts[1].strip()

            # Multiline block scalar (| or >)
            if val in ("|", ">", "|-", ">-", "|+", ">+"):
                block_lines = []
                i += 1
                while i < len(lines):
                    subline = lines[i]
                    if subline.strip() and not subline.startswith(" ") and not subline.startswith("\t") and ":" in subline:
                        break  # Next root key reached
                    block_lines.append(subline.strip())
                    i += 1
                join_char = " " if val.startswith(">") else "\n"
                metadata[key] = join_char.join(block_lines).strip()
                continue

            # List or empty value
            elif not val:
                i += 1
                items = []
                while i < len(lines):
                    subline = lines[i]
                    sub_stripped = subline.strip()
                    if not sub_stripped or sub_stripped.startswith("#"):
                        i += 1
                        continue
                    if not subline.startswith(" ") and not subline.startswith("\t") and ":" in subline:
                        break
                    if sub_stripped.startswith("- "):
                        item_val = sub_stripped[2:].strip()
                        if (item_val.startswith('"') and item_val.endswith('"')) or (item_val.startswith("'") and item_val.endswith("'")):
                            item_val = item_val[1:-1]
                        items.append(item_val)
                    i += 1
                metadata[key] = items
                continue

            # Simple scalar
            else:
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if val.lower() == "true":
                    metadata[key] = True
                elif val.lower() == "false":
                    metadata[key] = False
                else:
                    metadata[key] = val
                i += 1
                continue
        i += 1

    return metadata, body


def inspect_skill_directory(skill_dir: Path, harness_id: str, harness_name: str, scope: str) -> Optional[Dict[str, Any]]:
    """
    Inspects a directory containing SKILL.md and returns rich structured metadata.
    """
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        for f in skill_dir.glob("*"):
            if f.name.lower() == "skill.md":
                skill_file = f
                break
        else:
            return None

    try:
        content = skill_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = ""

    meta, body = parse_yaml_frontmatter(content)

    skill_name = meta.get("name") or skill_dir.name
    description = meta.get("description", "")
    if not description and body:
        for line in body.splitlines():
            line_clean = line.strip().lstrip("#").strip()
            if line_clean:
                description = line_clean
                break

    # Clean description whitespace
    if isinstance(description, str):
        description = " ".join(description.split())

    # Resource discovery (scripts, references, assets)
    scripts = []
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for sf in scripts_dir.rglob("*"):
            if sf.is_file():
                scripts.append(str(sf.relative_to(skill_dir)).replace("\\", "/"))

    references = []
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        for rf in ref_dir.rglob("*"):
            if rf.is_file():
                references.append(str(rf.relative_to(skill_dir)).replace("\\", "/"))

    assets = []
    assets_dir = skill_dir / "assets"
    if assets_dir.is_dir():
        for af in assets_dir.rglob("*"):
            if af.is_file():
                assets.append(str(af.relative_to(skill_dir)).replace("\\", "/"))

    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "id": f"{harness_id}:{skill_name}",
        "name": skill_name,
        "harness_id": harness_id,
        "harness_name": harness_name,
        "scope": scope,
        "description": description,
        "version": meta.get("version", "1.0.0"),
        "author": meta.get("author", ""),
        "tags": tags,
        "path": str(skill_dir.resolve()),
        "skill_file": str(skill_file.resolve()),
        "has_scripts": len(scripts) > 0,
        "scripts": scripts,
        "has_references": len(references) > 0,
        "references": references,
        "has_assets": len(assets) > 0,
        "assets": assets,
        "metadata": meta,
        "content_length": len(content),
    }


# ==============================================================================
# DISCOVERY ENGINE
# ==============================================================================

class SkillDiscoveryEngine:
    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.harness_defs = get_harness_definitions(self.workspace_root)

    def scan_directory_for_skills(self, search_root: Path, harness_id: str, harness_name: str, scope: str, max_depth: int = 5) -> List[Dict[str, Any]]:
        """
        Recursively scans a root directory for SKILL.md files up to max_depth.
        """
        skills = []
        if not search_root.exists() or not search_root.is_dir():
            return skills

        seen_dirs = set()

        try:
            for root, dirs, files in os.walk(search_root):
                root_path = Path(root)
                try:
                    rel = root_path.relative_to(search_root)
                    depth = len(rel.parts)
                except ValueError:
                    depth = 0

                if depth > max_depth:
                    dirs.clear()
                    continue

                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".system_generated"}]

                for f in files:
                    if f.lower() == "skill.md":
                        skill_dir = root_path
                        if skill_dir in seen_dirs:
                            continue
                        seen_dirs.add(skill_dir)

                        skill_info = inspect_skill_directory(skill_dir, harness_id, harness_name, scope)
                        if skill_info:
                            skills.append(skill_info)
        except Exception:
            pass

        return skills

    def discover_all_skills(self) -> List[Dict[str, Any]]:
        """
        Runs comprehensive discovery across all harnesses and returns sorted skill list.
        """
        all_skills: List[Dict[str, Any]] = []
        seen_paths = set()

        for h in self.harness_defs:
            hid = h["id"]
            hname = h["name"]

            # Global paths
            for gp in h.get("global_paths", []):
                for sk in self.scan_directory_for_skills(gp, hid, hname, scope="global"):
                    if sk["path"] not in seen_paths:
                        seen_paths.add(sk["path"])
                        all_skills.append(sk)

            # Plugin paths
            for pp in h.get("plugin_paths", []):
                for sk in self.scan_directory_for_skills(pp, hid, hname, scope="plugin"):
                    if sk["path"] not in seen_paths:
                        seen_paths.add(sk["path"])
                        all_skills.append(sk)

            # Builtin paths
            for bp in h.get("builtin_paths", []):
                for sk in self.scan_directory_for_skills(bp, hid, hname, scope="builtin"):
                    if sk["path"] not in seen_paths:
                        seen_paths.add(sk["path"])
                        all_skills.append(sk)

            # Workspace paths
            for wp in h.get("workspace_paths", []):
                for sk in self.scan_directory_for_skills(wp, hid, hname, scope="workspace"):
                    if sk["path"] not in seen_paths:
                        seen_paths.add(sk["path"])
                        all_skills.append(sk)

        return sorted(all_skills, key=lambda s: (s["name"].lower(), s["harness_id"]))

    def get_harness_status(self) -> List[Dict[str, Any]]:
        """
        Returns status and health of all known agent harnesses on the machine.
        """
        statuses = []
        all_skills = self.discover_all_skills()

        for h in self.harness_defs:
            hid = h["id"]
            global_exists = any(p.exists() for p in h.get("global_paths", []))
            plugin_exists = any(p.exists() for p in h.get("plugin_paths", []))
            builtin_exists = any(p.exists() for p in h.get("builtin_paths", []))
            workspace_exists = any(p.exists() for p in h.get("workspace_paths", []))

            is_installed = global_exists or plugin_exists or builtin_exists or workspace_exists
            skills_count = len([s for s in all_skills if s["harness_id"] == hid])

            statuses.append({
                "id": hid,
                "name": h["name"],
                "vendor": h["vendor"],
                "installed": is_installed,
                "skills_count": skills_count,
                "global_paths": [str(p) for p in h.get("global_paths", []) if p.exists()],
                "plugin_paths": [str(p) for p in h.get("plugin_paths", []) if p.exists()],
                "builtin_paths": [str(p) for p in h.get("builtin_paths", []) if p.exists()],
                "workspace_paths": [str(p) for p in h.get("workspace_paths", []) if p.exists()],
            })

        return statuses

    def search_skills(self, query: str, harness_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Searches skills by name, description, tags, and content.
        """
        query_lower = query.lower()
        skills = self.discover_all_skills()

        matches = []
        for s in skills:
            if harness_filter and s["harness_id"].lower() != harness_filter.lower():
                continue

            score = 0
            if s["name"].lower() == query_lower:
                score += 100
            elif query_lower in s["name"].lower():
                score += 50

            if any(query_lower in t.lower() for t in s.get("tags", [])):
                score += 30

            if query_lower in s.get("description", "").lower():
                score += 20

            if score > 0:
                s_copy = dict(s)
                s_copy["match_score"] = score
                matches.append(s_copy)

        return sorted(matches, key=lambda x: -x["match_score"])

    def find_skill(self, skill_name: str, harness: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Finds a specific skill by exact or partial name.
        """
        skills = self.discover_all_skills()
        target = skill_name.strip().lower()

        # 1. Exact name + harness match
        for s in skills:
            if s["name"].lower() == target:
                if harness and s["harness_id"].lower() == harness.lower():
                    return s
                if not harness:
                    return s

        # 2. Exact directory name match
        for s in skills:
            if Path(s["path"]).name.lower() == target:
                return s

        # 3. Substring match
        for s in skills:
            if target in s["name"].lower():
                return s

        return None


# ==============================================================================
# LINKING & CROSS-HARNESS SYNC
# ==============================================================================

def link_or_copy_skill(source_skill_path: Path, target_dir: Path, use_copy: bool = False) -> Tuple[bool, str]:
    """
    Symlinks, Directory Junctions (Windows), or copies a skill directory into a target harness.
    """
    if not source_skill_path.exists() or not source_skill_path.is_dir():
        return False, f"Source skill directory does not exist: {source_skill_path}"

    skill_folder_name = source_skill_path.name
    dest_path = target_dir / skill_folder_name

    target_dir.mkdir(parents=True, exist_ok=True)

    if dest_path.exists():
        return False, f"Destination already exists: {dest_path}"

    if use_copy:
        try:
            shutil.copytree(source_skill_path, dest_path)
            return True, f"Successfully copied skill to: {dest_path}"
        except Exception as e:
            return False, f"Failed to copy: {e}"

    # Symlink / Junction Creation
    try:
        if platform.system() == "Windows":
            cmd_j = f'cmd.exe /c mklink /J "{dest_path}" "{source_skill_path}"'
            subprocess.run(cmd_j, shell=True, capture_output=True, text=True)
            
            if dest_path.exists():
                return True, f"Successfully created directory junction at: {dest_path}"
            
            cmd_ps = f'powershell -NoProfile -Command "New-Item -ItemType SymbolicLink -Path \'{dest_path}\' -Target \'{source_skill_path}\' -ErrorAction SilentlyContinue"'
            subprocess.run(cmd_ps, shell=True, capture_output=True, text=True)

            if dest_path.exists():
                return True, f"Successfully created symlink at: {dest_path}"

            shutil.copytree(source_skill_path, dest_path)
            return True, f"Created copy (link fallback) at: {dest_path}"
        else:
            dest_path.symlink_to(source_skill_path, target_is_directory=True)
            return True, f"Successfully created symlink at: {dest_path}"
    except Exception as e:
        try:
            shutil.copytree(source_skill_path, dest_path)
            return True, f"Created copy (fallback) at: {dest_path}"
        except Exception as ce:
            return False, f"Linking failed: {e} (Copy error: {ce})"


# ==============================================================================
# CLI FORMATTING & STYLING
# ==============================================================================

class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.CYAN = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.BLUE = ""
        cls.MAGENTA = ""
        cls.RED = ""
        cls.BOLD = ""
        cls.DIM = ""
        cls.RESET = ""


def format_table(rows: List[List[str]], headers: List[str]) -> str:
    """Formats list of rows into an aligned ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            clean_cell = re.sub(r"\033\[[0-9;]*m", "", str(cell))
            col_widths[i] = max(col_widths[i], len(clean_cell))

    header_line = "  ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
    separator = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    
    formatted_rows = []
    for row in rows:
        line_parts = []
        for i, cell in enumerate(row):
            clean_len = len(re.sub(r"\033\[[0-9;]*m", "", str(cell)))
            padding = " " * (col_widths[i] - clean_len)
            line_parts.append(f"{cell}{padding}")
        formatted_rows.append("  ".join(line_parts))

    return f"{Colors.BOLD}{header_line}{Colors.RESET}\n{Colors.DIM}{separator}{Colors.RESET}\n" + "\n".join(formatted_rows)


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def main():
    common_p = argparse.ArgumentParser(add_help=False)
    common_p.add_argument("--json", action="store_true", help="Output machine-readable JSON for AI agents")
    common_p.add_argument("--no-color", action="store_true", help="Disable colored terminal output")
    common_p.add_argument("--workspace-root", type=str, default=None, help="Custom workspace root directory to scan")

    parser = argparse.ArgumentParser(
        description="Universal AI Agent Skill Discovery, Retrieval & Cross-Harness CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common_p],
        epilog="""
Examples:
  # 1. Discover all installed agent harnesses
  python skill_discovery.py harnesses

  # 2. List all skills across all harnesses
  python skill_discovery.py list
  python skill_discovery.py list --harness claude
  python skill_discovery.py list --paths-only

  # 3. Search skills by keyword or tag
  python skill_discovery.py search audit
  python skill_discovery.py search frontend --json

  # 4. Fetch path to a skill for direct agent loading
  python skill_discovery.py get ponytail
  python skill_discovery.py get frontend-design --file

  # 5. Preview instructions & metadata
  python skill_discovery.py preview humanizer
  python skill_discovery.py preview ponytail-audit --full

  # 6. Share / Link a skill to another harness without reinstalling
  python skill_discovery.py link humanizer --to gemini
  python skill_discovery.py link frontend-design --to agents --project
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # Command: list
    list_p = subparsers.add_parser("list", aliases=["ls"], parents=[common_p], help="List all discovered skills across all harnesses")
    list_p.add_argument("--harness", "-H", type=str, help="Filter by harness ID (claude, gemini, agents, cursor, etc.)")
    list_p.add_argument("--scope", "-s", choices=["global", "workspace", "plugin", "builtin"], help="Filter by scope")
    list_p.add_argument("--paths-only", action="store_true", help="Print only absolute paths to SKILL.md files")

    # Command: search
    search_p = subparsers.add_parser("search", aliases=["find"], parents=[common_p], help="Search skills by keyword/query")
    search_p.add_argument("query", type=str, help="Search term (name, keyword, or tag)")
    search_p.add_argument("--harness", "-H", type=str, help="Filter search within specific harness")

    # Command: get / path
    get_p = subparsers.add_parser("get", aliases=["path"], parents=[common_p], help="Get exact path for a skill")
    get_p.add_argument("skill_name", type=str, help="Name of the skill")
    get_p.add_argument("--harness", "-H", type=str, help="Optional harness disambiguation")
    get_p.add_argument("--file", action="store_true", help="Return path to SKILL.md file instead of directory")

    # Command: preview / show
    preview_p = subparsers.add_parser("preview", aliases=["show"], parents=[common_p], help="Preview skill instructions and metadata")
    preview_p.add_argument("skill_name", type=str, help="Name of the skill")
    preview_p.add_argument("--harness", "-H", type=str, help="Optional harness disambiguation")
    preview_p.add_argument("--full", action="store_true", help="Show full content without line limit")

    # Command: harnesses
    harnesses_p = subparsers.add_parser("harnesses", aliases=["env"], parents=[common_p], help="Discover installed AI agent harnesses")

    # Command: link / install / share
    link_p = subparsers.add_parser("link", aliases=["install", "share"], parents=[common_p], help="Link a skill to another agent harness")
    link_p.add_argument("skill_name", type=str, help="Name of skill to link")
    link_p.add_argument("--to", required=True, type=str, help="Target harness (claude, gemini, agents, cursor, roo, cline, etc.)")
    link_p.add_argument("--from-harness", type=str, help="Source harness disambiguation")
    link_p.add_argument("--project", "--workspace", action="store_true", dest="project", help="Link to project workspace instead of user global")
    link_p.add_argument("--copy", action="store_true", help="Copy files instead of creating symbolic link/junction")

    # Command: sync
    sync_p = subparsers.add_parser("sync", parents=[common_p], help="Bulk sync/link all skills between two harnesses")
    sync_p.add_argument("--from-harness", "--from", required=True, dest="from_harness", type=str, help="Source harness")
    sync_p.add_argument("--to", required=True, type=str, help="Target harness")
    sync_p.add_argument("--project", "--workspace", action="store_true", dest="project", help="Sync to workspace instead of global")
    sync_p.add_argument("--copy", action="store_true", help="Copy instead of link")

    args = parser.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        Colors.disable()

    ws_root = Path(args.workspace_root) if args.workspace_root else Path.cwd()
    engine = SkillDiscoveryEngine(workspace_root=ws_root)

    cmd = args.command or "list"

    # =========================================================================
    # HANDLER: HARNESSES
    # =========================================================================
    if cmd in ("harnesses", "env"):
        statuses = engine.get_harness_status()
        if args.json:
            print(json.dumps(statuses, indent=2))
            return

        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Discovered AI Agent Harnesses ==={Colors.RESET}\n")
        rows = []
        for s in statuses:
            installed_badge = f"{Colors.GREEN}[Active]{Colors.RESET}" if s["installed"] else f"{Colors.DIM}[Not Found]{Colors.RESET}"
            skills_badge = f"{Colors.YELLOW}{s['skills_count']} skill(s){Colors.RESET}" if s['skills_count'] > 0 else f"{Colors.DIM}0 skills{Colors.RESET}"
            rows.append([s["id"], s["name"], s["vendor"], installed_badge, skills_badge])

        headers = ["ID", "Harness Name", "Vendor", "Status", "Skills Found"]
        print(format_table(rows, headers))
        print(f"\n{Colors.DIM}Tip: Run 'python skill_discovery.py list --harness <id>' to view skills in a harness.{Colors.RESET}\n")
        return

    # =========================================================================
    # HANDLER: LIST
    # =========================================================================
    if cmd in ("list", "ls"):
        skills = engine.discover_all_skills()

        if hasattr(args, "harness") and args.harness:
            skills = [s for s in skills if s["harness_id"].lower() == args.harness.lower()]
        if hasattr(args, "scope") and args.scope:
            skills = [s for s in skills if s["scope"].lower() == args.scope.lower()]

        if args.json:
            print(json.dumps(skills, indent=2))
            return

        if getattr(args, "paths_only", False):
            for s in skills:
                print(s["skill_file"])
            return

        if not skills:
            print(f"{Colors.YELLOW}No skills discovered in the specified criteria.{Colors.RESET}")
            return

        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Discovered AI Agent Skills ({len(skills)} total) ==={Colors.RESET}\n")
        rows = []
        for s in skills:
            desc = s["description"]
            if len(desc) > 55:
                desc = desc[:52] + "..."
            scope_badge = f"[{s['scope']}]"
            res_tags = []
            if s["has_scripts"]:
                res_tags.append("scripts")
            if s["has_references"]:
                res_tags.append("refs")
            res_str = ",".join(res_tags) if res_tags else "-"
            rows.append([s["name"], s["harness_name"], scope_badge, res_str, desc])

        headers = ["Skill Name", "Harness Source", "Scope", "Resources", "Description"]
        print(format_table(rows, headers))
        print(f"\n{Colors.DIM}Tip: Use 'python skill_discovery.py preview <name>' to inspect instructions.{Colors.RESET}\n")
        return

    # =========================================================================
    # HANDLER: SEARCH
    # =========================================================================
    if cmd in ("search", "find"):
        results = engine.search_skills(args.query, harness_filter=args.harness)
        if args.json:
            print(json.dumps(results, indent=2))
            return

        if not results:
            print(f"{Colors.YELLOW}No skills matching '{args.query}' found.{Colors.RESET}")
            return

        print(f"\n{Colors.BOLD}{Colors.GREEN}Search Results for '{args.query}' ({len(results)} found):{Colors.RESET}\n")
        rows = []
        for s in results:
            desc = s["description"]
            if len(desc) > 55:
                desc = desc[:52] + "..."
            rows.append([s["name"], s["harness_name"], f"[{s['scope']}]", desc])

        headers = ["Skill Name", "Harness", "Scope", "Description"]
        print(format_table(rows, headers))
        print("")
        return

    # =========================================================================
    # HANDLER: GET / PATH
    # =========================================================================
    if cmd in ("get", "path"):
        skill = engine.find_skill(args.skill_name, harness=args.harness)
        if not skill:
            if args.json:
                print(json.dumps({"error": f"Skill '{args.skill_name}' not found"}, indent=2))
            else:
                print(f"{Colors.RED}Error: Skill '{args.skill_name}' not found.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(skill, indent=2))
            return

        target_path = skill["skill_file"] if args.file else skill["path"]
        print(target_path)
        return

    # =========================================================================
    # HANDLER: PREVIEW / SHOW
    # =========================================================================
    if cmd in ("preview", "show"):
        skill = engine.find_skill(args.skill_name, harness=args.harness)
        if not skill:
            if args.json:
                print(json.dumps({"error": f"Skill '{args.skill_name}' not found"}, indent=2))
            else:
                print(f"{Colors.RED}Error: Skill '{args.skill_name}' not found.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        skill_file = Path(skill["skill_file"])
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            content = f"Error reading file: {e}"

        meta, body = parse_yaml_frontmatter(content)

        if args.json:
            result = dict(skill)
            result["full_content"] = content
            result["instructions_body"] = body
            print(json.dumps(result, indent=2))
            return

        print(f"\n{Colors.BOLD}{Colors.CYAN}======================================================{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}Skill: {skill['name']}{Colors.RESET} ({skill['version']})")
        print(f"{Colors.CYAN}======================================================{Colors.RESET}")
        print(f"{Colors.BOLD}Harness Source:{Colors.RESET} {skill['harness_name']} [{skill['scope']}]")
        print(f"{Colors.BOLD}Directory:{Colors.RESET}      {skill['path']}")
        print(f"{Colors.BOLD}SKILL.md:{Colors.RESET}       {skill['skill_file']}")
        if skill.get("description"):
            print(f"{Colors.BOLD}Description:{Colors.RESET}    {skill['description']}")
        if skill.get("tags"):
            print(f"{Colors.BOLD}Tags:{Colors.RESET}           {', '.join(skill['tags'])}")

        if skill.get("scripts"):
            print(f"\n{Colors.BOLD}Executable Scripts ({len(skill['scripts'])}):{Colors.RESET}")
            for sc in skill["scripts"]:
                print(f"  • scripts/{sc}")

        if skill.get("references"):
            print(f"\n{Colors.BOLD}Reference Docs ({len(skill['references'])}):{Colors.RESET}")
            for rf in skill["references"]:
                print(f"  • references/{rf}")

        print(f"\n{Colors.BOLD}{Colors.YELLOW}--- Instructions Preview ---{Colors.RESET}")
        lines = body.splitlines()
        max_lines = len(lines) if args.full else min(25, len(lines))
        for line in lines[:max_lines]:
            print(line)

        if not args.full and len(lines) > max_lines:
            print(f"\n{Colors.DIM}... [{len(lines) - max_lines} more lines. Use --full to see all instructions] ...{Colors.RESET}")
        print("")
        return

    # =========================================================================
    # HANDLER: LINK / INSTALL / SHARE
    # =========================================================================
    if cmd in ("link", "install", "share"):
        skill = engine.find_skill(args.skill_name, harness=args.from_harness)
        if not skill:
            print(f"{Colors.RED}Error: Skill '{args.skill_name}' not found.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        target_harness_id = args.to.lower().strip()
        matching_harnesses = [h for h in engine.harness_defs if h["id"] == target_harness_id]
        if not matching_harnesses:
            print(f"{Colors.RED}Error: Unknown target harness '{args.to}'. Available: claude, gemini, agents, cursor, windsurf, roo, cline, goose, continue, aider{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        th = matching_harnesses[0]
        if args.project:
            target_dirs = th.get("workspace_paths", [])
        else:
            target_dirs = th.get("global_paths", [])

        if not target_dirs:
            print(f"{Colors.RED}Error: No valid destination directory found for harness '{args.to}'.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        target_dir = target_dirs[0]
        src_path = Path(skill["path"])

        success, msg = link_or_copy_skill(src_path, target_dir, use_copy=args.copy)
        if success:
            print(f"{Colors.GREEN}[OK] {msg}{Colors.RESET}")
            print(f"Skill '{skill['name']}' is now instantly available in {th['name']}!")
        else:
            print(f"{Colors.RED}[FAIL] {msg}{Colors.RESET}", file=sys.stderr)
            sys.exit(1)
        return

    # =========================================================================
    # HANDLER: SYNC (BULK)
    # =========================================================================
    if cmd == "sync":
        source_harness_id = args.from_harness.lower().strip()
        target_harness_id = args.to.lower().strip()

        source_skills = [s for s in engine.discover_all_skills() if s["harness_id"] == source_harness_id]
        if not source_skills:
            print(f"{Colors.YELLOW}No skills found in source harness '{source_harness_id}'.{Colors.RESET}")
            return

        matching_targets = [h for h in engine.harness_defs if h["id"] == target_harness_id]
        if not matching_targets:
            print(f"{Colors.RED}Error: Unknown target harness '{target_harness_id}'.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        th = matching_targets[0]
        target_dirs = th.get("workspace_paths", []) if args.project else th.get("global_paths", [])
        if not target_dirs:
            print(f"{Colors.RED}Error: No destination directory for target harness '{target_harness_id}'.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)

        target_dir = target_dirs[0]
        print(f"\n{Colors.BOLD}{Colors.CYAN}Syncing {len(source_skills)} skill(s) from '{source_harness_id}' -> '{target_harness_id}' ({target_dir}){Colors.RESET}\n")

        synced = 0
        for s in source_skills:
            src_path = Path(s["path"])
            dest = target_dir / src_path.name
            if dest.exists():
                print(f"  {Colors.DIM}• {s['name']}: already exists, skipping{Colors.RESET}")
                continue
            success, msg = link_or_copy_skill(src_path, target_dir, use_copy=args.copy)
            if success:
                print(f"  {Colors.GREEN}[OK] {s['name']}: linked{Colors.RESET}")
                synced += 1
            else:
                print(f"  {Colors.RED}[FAIL] {s['name']}: {msg}{Colors.RESET}")

        print(f"\n{Colors.GREEN}Sync complete: {synced} skill(s) linked.{Colors.RESET}\n")
        return


if __name__ == "__main__":
    main()
