// Universal AI Agent Skill Discovery, Retrieval & Cross-Harness Tool (Rust)
// Built with ponytail minimalism: zero bloat, stdlib + serde, single fast binary.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

// ==============================================================================
// DATA STRUCTURES
// ==============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HarnessDef {
    pub id: String,
    pub name: String,
    pub vendor: String,
    pub global_paths: Vec<PathBuf>,
    pub plugin_paths: Vec<PathBuf>,
    pub builtin_paths: Vec<PathBuf>,
    pub workspace_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillInfo {
    pub id: String,
    pub name: String,
    pub harness_id: String,
    pub harness_name: String,
    pub scope: String, // "global", "workspace", "plugin", "builtin"
    pub description: String,
    pub version: String,
    pub author: String,
    pub tags: Vec<String>,
    pub path: String,
    pub skill_file: String,
    pub has_scripts: bool,
    pub scripts: Vec<String>,
    pub has_references: bool,
    pub references: Vec<String>,
    pub has_assets: bool,
    pub assets: Vec<String>,
    pub metadata: HashMap<String, serde_json::Value>,
    pub content_length: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HarnessStatus {
    pub id: String,
    pub name: String,
    pub vendor: String,
    pub installed: bool,
    pub skills_count: usize,
    pub global_paths: Vec<String>,
    pub plugin_paths: Vec<String>,
    pub builtin_paths: Vec<String>,
    pub workspace_paths: Vec<String>,
}

// ==============================================================================
// HARNESS REGISTRY
// ==============================================================================

fn get_home_dir() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(p) = env::var("USERPROFILE") {
            return PathBuf::from(p);
        }
    }
    if let Ok(p) = env::var("HOME") {
        return PathBuf::from(p);
    }
    PathBuf::from(".")
}

fn clean_path_str(p: &Path) -> String {
    let s = p.to_string_lossy().to_string();
    if let Some(stripped) = s.strip_prefix(r"\\?\") {
        stripped.to_string()
    } else {
        s
    }
}

fn get_harness_definitions(workspace_root: &Path) -> Vec<HarnessDef> {
    let home = get_home_dir();
    let ws = workspace_root;

    let mut defs = vec![
        HarnessDef {
            id: "claude".into(),
            name: "Claude Code / Desktop".into(),
            vendor: "Anthropic".into(),
            global_paths: vec![
                home.join(".claude").join("skills"),
                home.join(".config").join("claude").join("skills"),
            ],
            plugin_paths: vec![
                home.join(".claude").join("plugins").join("marketplaces"),
                home.join(".claude").join("plugins").join("cache"),
                home.join(".claude").join("plugins"),
            ],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".claude").join("skills"),
                ws.join(".claude").join("plugins"),
            ],
        },
        HarnessDef {
            id: "gemini".into(),
            name: "Gemini CLI / Antigravity".into(),
            vendor: "Google".into(),
            global_paths: vec![
                home.join(".gemini").join("skills"),
                home.join(".gemini").join("config").join("skills"),
            ],
            plugin_paths: vec![
                home.join(".gemini").join("config").join("plugins"),
                home.join(".gemini").join("plugins"),
            ],
            builtin_paths: vec![
                home.join(".gemini").join("antigravity-cli").join("builtin").join("skills"),
            ],
            workspace_paths: vec![
                ws.join(".gemini").join("skills"),
                ws.join(".gemini").join("plugins"),
            ],
        },
        HarnessDef {
            id: "agents".into(),
            name: "Universal Agent Skills Standard".into(),
            vendor: "Open Standard".into(),
            global_paths: vec![
                home.join(".agents").join("skills"),
                home.join(".skills"),
            ],
            plugin_paths: vec![
                home.join(".agents").join("plugins"),
            ],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".agents").join("skills"),
                ws.join(".skills"),
            ],
        },
        HarnessDef {
            id: "cursor".into(),
            name: "Cursor IDE".into(),
            vendor: "Anysphere".into(),
            global_paths: vec![
                home.join(".cursor").join("skills"),
                home.join(".cursor").join("rules"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".cursor").join("skills"),
                ws.join(".cursor").join("rules"),
            ],
        },
        HarnessDef {
            id: "windsurf".into(),
            name: "Windsurf IDE".into(),
            vendor: "Codeium".into(),
            global_paths: vec![
                home.join(".codeium").join("windsurf").join("skills"),
                home.join(".windsurf").join("skills"),
                home.join(".codeium").join("windsurf").join("rules"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".windsurf").join("skills"),
                ws.join(".windsurf").join("rules"),
            ],
        },
        HarnessDef {
            id: "roo".into(),
            name: "Roo Code (Roo Clinic)".into(),
            vendor: "RooVeterinaryInc".into(),
            global_paths: vec![
                home.join(".roo").join("skills"),
                home.join(".config").join("Code").join("User").join("globalStorage").join("rooveterinaryinc.roo-cline").join("skills"),
                home.join("AppData").join("Roaming").join("Code").join("User").join("globalStorage").join("rooveterinaryinc.roo-cline").join("skills"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".roo").join("skills"),
            ],
        },
        HarnessDef {
            id: "cline".into(),
            name: "Cline".into(),
            vendor: "Cline".into(),
            global_paths: vec![
                home.join(".cline").join("skills"),
                home.join(".config").join("Code").join("User").join("globalStorage").join("saoudrizwan.claude-dev").join("skills"),
                home.join("AppData").join("Roaming").join("Code").join("User").join("globalStorage").join("saoudrizwan.claude-dev").join("skills"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".cline").join("skills"),
            ],
        },
        HarnessDef {
            id: "goose".into(),
            name: "Goose AI Agent".into(),
            vendor: "Block".into(),
            global_paths: vec![
                home.join(".config").join("goose").join("skills"),
                home.join(".goose").join("skills"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".goose").join("skills"),
            ],
        },
        HarnessDef {
            id: "continue".into(),
            name: "Continue.dev".into(),
            vendor: "Continue".into(),
            global_paths: vec![
                home.join(".continue").join("prompts"),
                home.join(".continue").join("skills"),
                home.join(".continue").join("rules"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".continue").join("prompts"),
                ws.join(".continue").join("skills"),
                ws.join(".continue").join("rules"),
            ],
        },
        HarnessDef {
            id: "aider".into(),
            name: "Aider".into(),
            vendor: "Paul Gauthier".into(),
            global_paths: vec![
                home.join(".aider").join("prompts"),
                home.join(".aider").join("skills"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".aider").join("prompts"),
                ws.join(".aider").join("skills"),
            ],
        },
        HarnessDef {
            id: "openhands".into(),
            name: "OpenHands (OpenDevin)".into(),
            vendor: "OpenHands".into(),
            global_paths: vec![
                home.join(".openhands").join("skills"),
            ],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".openhands").join("skills"),
            ],
        },
        HarnessDef {
            id: "copilot".into(),
            name: "GitHub Copilot Workspace".into(),
            vendor: "GitHub".into(),
            global_paths: vec![],
            plugin_paths: vec![],
            builtin_paths: vec![],
            workspace_paths: vec![
                ws.join(".github").join("skills"),
                ws.join(".copilot").join("skills"),
            ],
        },
    ];

    if let Ok(custom_env) = env::var("AGENT_SKILLS_PATH") {
        let separator = if cfg!(windows) { ';' } else { ':' };
        let custom_paths: Vec<PathBuf> = custom_env
            .split(separator)
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from)
            .collect();

        if !custom_paths.is_empty() {
            defs.push(HarnessDef {
                id: "custom".into(),
                name: "Custom ($AGENT_SKILLS_PATH)".into(),
                vendor: "User Configured".into(),
                global_paths: custom_paths,
                plugin_paths: vec![],
                builtin_paths: vec![],
                workspace_paths: vec![],
            });
        }
    }

    defs
}

// ==============================================================================
// YAML FRONTMATTER & METADATA PARSER
// ==============================================================================

// ponytail: hand-rolled minimalist YAML parser handles standard SKILL.md frontmatter (scalars, multiline |, >, lists) without pulling heavy yaml-rust dependency
pub fn parse_yaml_frontmatter(content: &str) -> (HashMap<String, serde_json::Value>, String) {
    let mut meta = HashMap::new();
    let mut body = content.to_string();

    let lines: Vec<&str> = content.lines().collect();
    if lines.is_empty() || lines[0].trim() != "---" {
        return (meta, body);
    }

    let mut end_idx = None;
    for (i, line) in lines.iter().enumerate().skip(1) {
        if line.trim() == "---" {
            end_idx = Some(i);
            break;
        }
    }

    let end_idx = match end_idx {
        Some(idx) => idx,
        None => return (meta, body),
    };

    body = lines[end_idx + 1..].join("\n").trim().to_string();

    let mut i = 1;
    while i < end_idx {
        let line = lines[i];
        let stripped = line.trim();
        if stripped.is_empty() || stripped.starts_with('#') {
            i += 1;
            continue;
        }

        if !line.starts_with(' ') && !line.starts_with('\t') && line.contains(':') {
            let parts: Vec<&str> = line.splitn(2, ':').collect();
            let key = parts[0].trim().to_string();
            let val = parts[1].trim();

            if val == "|" || val == ">" || val == "|-" || val == ">-" || val == "|+" || val == ">+" {
                let mut block_lines = Vec::new();
                i += 1;
                while i < end_idx {
                    let subline = lines[i];
                    if !subline.trim().is_empty() && !subline.starts_with(' ') && !subline.starts_with('\t') && subline.contains(':') {
                        break;
                    }
                    block_lines.push(subline.trim());
                    i += 1;
                }
                let join_char = if val.starts_with('>') { " " } else { "\n" };
                let combined = block_lines.join(join_char).trim().to_string();
                meta.insert(key, serde_json::Value::String(combined));
                continue;
            } else if val.is_empty() {
                i += 1;
                let mut items = Vec::new();
                while i < end_idx {
                    let subline = lines[i];
                    let sub_stripped = subline.trim();
                    if sub_stripped.is_empty() || sub_stripped.starts_with('#') {
                        i += 1;
                        continue;
                    }
                    if !subline.starts_with(' ') && !subline.starts_with('\t') && subline.contains(':') {
                        break;
                    }
                    if sub_stripped.starts_with("- ") {
                        let item_val = sub_stripped[2..].trim().trim_matches('"').trim_matches('\'');
                        items.push(serde_json::Value::String(item_val.to_string()));
                    }
                    i += 1;
                }
                meta.insert(key, serde_json::Value::Array(items));
                continue;
            } else {
                let clean_val = val.trim_matches('"').trim_matches('\'');
                if clean_val.eq_ignore_ascii_case("true") {
                    meta.insert(key, serde_json::Value::Bool(true));
                } else if clean_val.eq_ignore_ascii_case("false") {
                    meta.insert(key, serde_json::Value::Bool(false));
                } else {
                    meta.insert(key, serde_json::Value::String(clean_val.to_string()));
                }
                i += 1;
                continue;
            }
        }
        i += 1;
    }

    (meta, body)
}

fn collect_relative_files(base_dir: &Path, sub_folder: &str) -> Vec<String> {
    let mut files = Vec::new();
    let target = base_dir.join(sub_folder);
    if target.is_dir() {
        if let Ok(entries) = fs::read_dir(&target) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_file() {
                    if let Ok(rel) = path.strip_prefix(base_dir) {
                        files.push(rel.to_string_lossy().replace('\\', "/"));
                    }
                }
            }
        }
    }
    files
}

pub fn inspect_skill_dir(skill_dir: &Path, harness_id: &str, harness_name: &str, scope: &str) -> Option<SkillInfo> {
    let mut skill_file = skill_dir.join("SKILL.md");
    if !skill_file.is_file() {
        if let Ok(entries) = fs::read_dir(skill_dir) {
            let mut found = false;
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_file() && p.file_name().and_then(|n| n.to_str()).map(|s| s.eq_ignore_ascii_case("skill.md")).unwrap_or(false) {
                    skill_file = p;
                    found = true;
                    break;
                }
            }
            if !found {
                return None;
            }
        } else {
            return None;
        }
    }

    let content = fs::read_to_string(&skill_file).unwrap_or_default();
    let (meta, body) = parse_yaml_frontmatter(&content);

    let default_name = skill_dir.file_name().and_then(|n| n.to_str()).unwrap_or("unnamed").to_string();
    let name = meta.get("name").and_then(|v| v.as_str()).unwrap_or(&default_name).to_string();

    let mut description = meta.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if description.is_empty() && !body.is_empty() {
        for line in body.lines() {
            let clean = line.trim().trim_start_matches('#').trim();
            if !clean.is_empty() {
                description = clean.to_string();
                break;
            }
        }
    }
    description = description.split_whitespace().collect::<Vec<&str>>().join(" ");

    let version = meta.get("version").and_then(|v| v.as_str()).unwrap_or("1.0.0").to_string();
    let author = meta.get("author").and_then(|v| v.as_str()).unwrap_or("").to_string();

    let mut tags = Vec::new();
    if let Some(tag_val) = meta.get("tags") {
        if let Some(arr) = tag_val.as_array() {
            for t in arr {
                if let Some(s) = t.as_str() {
                    tags.push(s.to_string());
                }
            }
        } else if let Some(s) = tag_val.as_str() {
            for t in s.split(',') {
                let trimmed = t.trim();
                if !trimmed.is_empty() {
                    tags.push(trimmed.to_string());
                }
            }
        }
    }

    let scripts = collect_relative_files(skill_dir, "scripts");
    let references = collect_relative_files(skill_dir, "references");
    let assets = collect_relative_files(skill_dir, "assets");

    let clean_dir_path = clean_path_str(&skill_dir.canonicalize().unwrap_or_else(|_| skill_dir.to_path_buf()));
    let clean_file_path = clean_path_str(&skill_file.canonicalize().unwrap_or_else(|_| skill_file.clone()));

    Some(SkillInfo {
        id: format!("{}:{}", harness_id, name),
        name,
        harness_id: harness_id.to_string(),
        harness_name: harness_name.to_string(),
        scope: scope.to_string(),
        description,
        version,
        author,
        tags,
        path: clean_dir_path,
        skill_file: clean_file_path,
        has_scripts: !scripts.is_empty(),
        scripts,
        has_references: !references.is_empty(),
        references,
        has_assets: !assets.is_empty(),
        assets,
        metadata: meta,
        content_length: content.len(),
    })
}

// ==============================================================================
// SCANNING ENGINE
// ==============================================================================

fn scan_dir_recursive(root: &Path, harness_id: &str, harness_name: &str, scope: &str, max_depth: usize, current_depth: usize, results: &mut Vec<SkillInfo>, seen: &mut HashSet<PathBuf>) {
    if current_depth > max_depth || !root.is_dir() {
        return;
    }

    let entries = match fs::read_dir(root) {
        Ok(e) => e,
        Err(_) => return,
    };

    let mut has_skill_file = false;
    let mut subdirs = Vec::new();

    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file() {
            if let Some(fname) = path.file_name().and_then(|n| n.to_str()) {
                if fname.eq_ignore_ascii_case("skill.md") {
                    has_skill_file = true;
                }
            }
        } else if path.is_dir() {
            if let Some(dname) = path.file_name().and_then(|n| n.to_str()) {
                if !matches!(dname, ".git" | "node_modules" | ".venv" | "venv" | "__pycache__" | "dist" | "build" | ".system_generated") {
                    subdirs.push(path);
                }
            }
        }
    }

    if has_skill_file {
        let canon = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
        if !seen.contains(&canon) {
            seen.insert(canon);
            if let Some(info) = inspect_skill_dir(root, harness_id, harness_name, scope) {
                results.push(info);
            }
        }
    }

    for sub in subdirs {
        scan_dir_recursive(&sub, harness_id, harness_name, scope, max_depth, current_depth + 1, results, seen);
    }
}

pub fn discover_all_skills(workspace_root: &Path) -> Vec<SkillInfo> {
    let harness_defs = get_harness_definitions(workspace_root);
    let mut all_skills = Vec::new();
    let mut seen = HashSet::new();

    for h in &harness_defs {
        for gp in &h.global_paths {
            scan_dir_recursive(gp, &h.id, &h.name, "global", 5, 0, &mut all_skills, &mut seen);
        }
        for pp in &h.plugin_paths {
            scan_dir_recursive(pp, &h.id, &h.name, "plugin", 5, 0, &mut all_skills, &mut seen);
        }
        for bp in &h.builtin_paths {
            scan_dir_recursive(bp, &h.id, &h.name, "builtin", 5, 0, &mut all_skills, &mut seen);
        }
        for wp in &h.workspace_paths {
            scan_dir_recursive(wp, &h.id, &h.name, "workspace", 5, 0, &mut all_skills, &mut seen);
        }
    }

    all_skills.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()).then_with(|| a.harness_id.cmp(&b.harness_id)));
    all_skills
}

pub fn get_harness_statuses(workspace_root: &Path) -> Vec<HarnessStatus> {
    let harness_defs = get_harness_definitions(workspace_root);
    let skills = discover_all_skills(workspace_root);

    harness_defs.into_iter().map(|h| {
        let global_exists = h.global_paths.iter().any(|p| p.exists());
        let plugin_exists = h.plugin_paths.iter().any(|p| p.exists());
        let builtin_exists = h.builtin_paths.iter().any(|p| p.exists());
        let workspace_exists = h.workspace_paths.iter().any(|p| p.exists());

        let installed = global_exists || plugin_exists || builtin_exists || workspace_exists;
        let count = skills.iter().filter(|s| s.harness_id == h.id).count();

        HarnessStatus {
            id: h.id,
            name: h.name,
            vendor: h.vendor,
            installed,
            skills_count: count,
            global_paths: h.global_paths.into_iter().filter(|p| p.exists()).map(|p| clean_path_str(&p)).collect(),
            plugin_paths: h.plugin_paths.into_iter().filter(|p| p.exists()).map(|p| clean_path_str(&p)).collect(),
            builtin_paths: h.builtin_paths.into_iter().filter(|p| p.exists()).map(|p| clean_path_str(&p)).collect(),
            workspace_paths: h.workspace_paths.into_iter().filter(|p| p.exists()).map(|p| clean_path_str(&p)).collect(),
        }
    }).collect()
}

// ==============================================================================
// LINKING & SHARING
// ==============================================================================

pub fn link_or_copy(src: &Path, dest_parent: &Path, copy: bool) -> Result<String, String> {
    if !src.is_dir() {
        return Err(format!("Source does not exist: {}", src.display()));
    }
    let folder_name = src.file_name().ok_or("Invalid source folder name")?;
    let dest = dest_parent.join(folder_name);

    if dest.exists() {
        return Err(format!("Destination already exists: {}", dest.display()));
    }

    fs::create_dir_all(dest_parent).map_err(|e| e.to_string())?;

    if copy {
        copy_dir_all(src, &dest).map_err(|e| format!("Copy failed: {}", e))?;
        return Ok(format!("Copied skill to {}", dest.display()));
    }

    #[cfg(windows)]
    {
        let _ = Command::new("cmd.exe")
            .args(["/c", "mklink", "/J", &dest.to_string_lossy(), &src.to_string_lossy()])
            .output();

        if dest.exists() {
            return Ok(format!("Created directory junction at {}", dest.display()));
        }

        copy_dir_all(src, &dest).map_err(|e| format!("Copy fallback failed: {}", e))?;
        Ok(format!("Copied skill (junction fallback) to {}", dest.display()))
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;
        if let Err(e) = symlink(src, &dest) {
            copy_dir_all(src, &dest).map_err(|ce| format!("Symlink failed: {}, Copy fallback failed: {}", e, ce))?;
            return Ok(format!("Copied skill (symlink fallback) to {}", dest.display()));
        }
        Ok(format!("Created symlink at {}", dest.display()))
    }
}

fn copy_dir_all(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        if ty.is_dir() {
            copy_dir_all(&entry.path(), &dst.join(entry.file_name()))?;
        } else {
            fs::copy(entry.path(), dst.join(entry.file_name()))?;
        }
    }
    Ok(())
}

// ==============================================================================
// CLI FORMATTING
// ==============================================================================

fn strip_ansi(s: &str) -> String {
    let mut out = String::new();
    let mut in_escape = false;
    for c in s.chars() {
        if c == '\x1b' {
            in_escape = true;
        } else if in_escape {
            if c == 'm' {
                in_escape = false;
            }
        } else {
            out.push(c);
        }
    }
    out
}

fn print_table(rows: &[Vec<String>], headers: &[&str]) {
    if rows.is_empty() {
        return;
    }
    let mut widths = headers.iter().map(|h| h.len()).collect::<Vec<_>>();
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            if i < widths.len() {
                let clean_len = strip_ansi(cell).len();
                widths[i] = widths[i].max(clean_len);
            }
        }
    }

    let header_line = headers.iter().enumerate().map(|(i, h)| format!("{:<width$}", h, width = widths[i])).collect::<Vec<_>>().join("  ");
    let sep_line = widths.iter().map(|w| "-".repeat(*w)).collect::<Vec<_>>().join("  ");

    println!("\x1b[1m{}\x1b[0m", header_line);
    println!("\x1b[2m{}\x1b[0m", sep_line);
    for row in rows {
        let parts: Vec<String> = row.iter().enumerate().map(|(i, cell)| {
            let clean_len = strip_ansi(cell).len();
            let pad = if widths[i] > clean_len { " ".repeat(widths[i] - clean_len) } else { String::new() };
            format!("{}{}", cell, pad)
        }).collect();
        println!("{}", parts.join("  "));
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let is_json = args.iter().any(|a| a == "--json");
    let paths_only = args.iter().any(|a| a == "--paths-only");
    let is_copy = args.iter().any(|a| a == "--copy");
    let is_full = args.iter().any(|a| a == "--full");
    let is_project = args.iter().any(|a| a == "--project" || a == "--workspace");

    let harness_filter = args.iter().position(|a| a == "--harness" || a == "-H").and_then(|i| args.get(i + 1));
    let scope_filter = args.iter().position(|a| a == "--scope" || a == "-s").and_then(|i| args.get(i + 1));
    let to_filter = args.iter().position(|a| a == "--to").and_then(|i| args.get(i + 1));
    let from_filter = args.iter().position(|a| a == "--from" || a == "--from-harness").and_then(|i| args.get(i + 1));

    let is_help = args.iter().any(|a| a == "--help" || a == "-h" || a == "help") || args.len() <= 1;
    if is_help {
        println!("\x1b[1m\x1b[36mUniversal AI Agent Skill Discovery, Retrieval & Cross-Harness CLI (Rust)\x1b[0m");
        println!("\x1b[2mDiscovers, searches, previews, and links agent skills across 12+ AI agent harnesses.\x1b[0m\n");
        println!("\x1b[1mUSAGE:\x1b[0m");
        println!("  skills <COMMAND> [OPTIONS]\n");
        println!("\x1b[1mCOMMANDS:\x1b[0m");
        println!("  \x1b[32mharnesses\x1b[0m, \x1b[32menv\x1b[0m                 Discover and inspect installed AI agent harnesses on system");
        println!("  \x1b[32mlist\x1b[0m, \x1b[32mls\x1b[0m                       List all discovered skills across all harnesses");
        println!("  \x1b[32msearch\x1b[0m, \x1b[32mfind\x1b[0m <QUERY>           Search skills by keyword, tag, or description");
        println!("  \x1b[32mget\x1b[0m, \x1b[32mpath\x1b[0m <NAME>              Get the absolute path of a skill (or --file for SKILL.md)");
        println!("  \x1b[32mpreview\x1b[0m, \x1b[32mshow\x1b[0m <NAME>          Preview instructions, YAML frontmatter, and scripts/refs");
        println!("  \x1b[32mlink\x1b[0m <NAME> --to <HARNESS>   Instantly share/link a skill to another harness (zero-copy)");
        println!("  \x1b[32msync\x1b[0m --from <H1> --to <H2>   Bulk sync/link all skills between two agent harnesses\n");
        println!("\x1b[1mOPTIONS:\x1b[0m");
        println!("  \x1b[33m--help, -h\x1b[0m                   Print help information");
        println!("  \x1b[33m--json\x1b[0m                       Output machine-readable JSON for calling AI agents");
        println!("  \x1b[33m--harness, -H <ID>\x1b[0m           Filter by harness (claude, gemini, agents, cursor, etc.)");
        println!("  \x1b[33m--scope, -s <SCOPE>\x1b[0m          Filter by scope (global, workspace, plugin, builtin)");
        println!("  \x1b[33m--paths-only\x1b[0m                 Output only absolute file paths to SKILL.md files");
        println!("  \x1b[33m--file\x1b[0m                       Get path to SKILL.md instead of directory");
        println!("  \x1b[33m--full\x1b[0m                       Show full instructions without line limit in preview");
        println!("  \x1b[33m--project, --workspace\x1b[0m       Target workspace directory instead of user global");
        println!("  \x1b[33m--copy\x1b[0m                       Force file copy instead of directory junction/symlink\n");
        println!("\x1b[1mEXAMPLES:\x1b[0m");
        println!("  skills harnesses");
        println!("  skills list --harness claude");
        println!("  skills search frontend --json");
        println!("  skills get humanizer --file");
        println!("  skills preview ponytail-audit --full");
        println!("  skills link humanizer --to gemini");
        println!("  skills sync --from claude --to gemini");
        return;
    }

    let ws_root = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let command = args.iter().skip(1).find(|a| !a.starts_with('-')).map(|s| s.as_str()).unwrap_or("list");

    match command {
        "harnesses" | "env" => {
            let statuses = get_harness_statuses(&ws_root);
            if is_json {
                println!("{}", serde_json::to_string_pretty(&statuses).unwrap());
                return;
            }

            println!("\n\x1b[1m\x1b[36m=== Discovered AI Agent Harnesses ===\x1b[0m\n");
            let rows: Vec<Vec<String>> = statuses.into_iter().map(|s| {
                let status_str = if s.installed { "\x1b[32m[Active]\x1b[0m".to_string() } else { "\x1b[2m[Not Found]\x1b[0m".to_string() };
                let skills_str = if s.skills_count > 0 { format!("\x1b[33m{} skill(s)\x1b[0m", s.skills_count) } else { "\x1b[2m0 skills\x1b[0m".to_string() };
                vec![s.id, s.name, s.vendor, status_str, skills_str]
            }).collect();

            print_table(&rows, &["ID", "Harness Name", "Vendor", "Status", "Skills Found"]);
            println!("\n\x1b[2mTip: Run 'skills list --harness <id>' to inspect skills for a harness.\x1b[0m\n");
        }

        "list" | "ls" => {
            let mut skills = discover_all_skills(&ws_root);

            if let Some(h) = harness_filter {
                skills.retain(|s| s.harness_id.eq_ignore_ascii_case(h));
            }
            if let Some(sc) = scope_filter {
                skills.retain(|s| s.scope.eq_ignore_ascii_case(sc));
            }

            if is_json {
                println!("{}", serde_json::to_string_pretty(&skills).unwrap());
                return;
            }

            if paths_only {
                for s in skills {
                    println!("{}", s.skill_file);
                }
                return;
            }

            if skills.is_empty() {
                println!("\x1b[33mNo skills discovered in the specified criteria.\x1b[0m");
                return;
            }

            println!("\n\x1b[1m\x1b[36m=== Discovered AI Agent Skills ({} total) ===\x1b[0m\n", skills.len());
            let rows: Vec<Vec<String>> = skills.into_iter().map(|s| {
                let mut desc = s.description;
                if desc.len() > 55 {
                    desc = format!("{}...", &desc[..52]);
                }
                let mut res = Vec::new();
                if s.has_scripts { res.push("scripts"); }
                if s.has_references { res.push("refs"); }
                let res_str = if res.is_empty() { "-".to_string() } else { res.join(",") };

                vec![s.name, s.harness_name, format!("[{}]", s.scope), res_str, desc]
            }).collect();

            print_table(&rows, &["Skill Name", "Harness Source", "Scope", "Resources", "Description"]);
            println!("\n\x1b[2mTip: Run 'skills preview <name>' to inspect instructions.\x1b[0m\n");
        }

        "search" | "find" => {
            let query = args.iter().skip(1).filter(|a| !a.starts_with('-')).nth(1).unwrap_or(&String::new()).to_lowercase();
            if query.is_empty() {
                eprintln!("\x1b[31mError: Missing search query. Usage: skills search <query>\x1b[0m");
                std::process::exit(1);
            }

            let mut skills = discover_all_skills(&ws_root);
            if let Some(h) = harness_filter {
                skills.retain(|s| s.harness_id.eq_ignore_ascii_case(h));
            }

            let mut matches = Vec::new();
            for s in skills {
                let mut score = 0;
                if s.name.to_lowercase() == query {
                    score += 100;
                } else if s.name.to_lowercase().contains(&query) {
                    score += 50;
                }
                if s.tags.iter().any(|t| t.to_lowercase().contains(&query)) {
                    score += 30;
                }
                if s.description.to_lowercase().contains(&query) {
                    score += 20;
                }
                if score > 0 {
                    matches.push((score, s));
                }
            }

            matches.sort_by(|a, b| b.0.cmp(&a.0));
            let results: Vec<SkillInfo> = matches.into_iter().map(|(_, s)| s).collect();

            if is_json {
                println!("{}", serde_json::to_string_pretty(&results).unwrap());
                return;
            }

            if results.is_empty() {
                println!("\x1b[33mNo skills matching '{}' found.\x1b[0m", query);
                return;
            }

            println!("\n\x1b[1m\x1b[32mSearch Results for '{}' ({} found):\x1b[0m\n", query, results.len());
            let rows: Vec<Vec<String>> = results.into_iter().map(|s| {
                let mut desc = s.description;
                if desc.len() > 55 {
                    desc = format!("{}...", &desc[..52]);
                }
                vec![s.name, s.harness_name, format!("[{}]", s.scope), desc]
            }).collect();

            print_table(&rows, &["Skill Name", "Harness", "Scope", "Description"]);
            println!();
        }

        "get" | "path" => {
            let target_name = args.iter().skip(1).filter(|a| !a.starts_with('-')).nth(1).unwrap_or(&String::new()).to_lowercase();
            if target_name.is_empty() {
                eprintln!("\x1b[31mError: Missing skill name. Usage: skills get <name>\x1b[0m");
                std::process::exit(1);
            }

            let skills = discover_all_skills(&ws_root);
            let found = skills.iter().find(|s| {
                if let Some(h) = harness_filter {
                    if !s.harness_id.eq_ignore_ascii_case(h) {
                        return false;
                    }
                }
                s.name.eq_ignore_ascii_case(&target_name) || s.name.to_lowercase().contains(&target_name)
            });

            match found {
                Some(skill) => {
                    if is_json {
                        println!("{}", serde_json::to_string_pretty(skill).unwrap());
                    } else if args.iter().any(|a| a == "--file") {
                        println!("{}", skill.skill_file);
                    } else {
                        println!("{}", skill.path);
                    }
                }
                None => {
                    if is_json {
                        println!("{{\"error\": \"Skill '{}' not found\"}}", target_name);
                    } else {
                        eprintln!("\x1b[31mError: Skill '{}' not found.\x1b[0m", target_name);
                    }
                    std::process::exit(1);
                }
            }
        }

        "preview" | "show" => {
            let target_name = args.iter().skip(1).filter(|a| !a.starts_with('-')).nth(1).unwrap_or(&String::new()).to_lowercase();
            if target_name.is_empty() {
                eprintln!("\x1b[31mError: Missing skill name. Usage: skills preview <name>\x1b[0m");
                std::process::exit(1);
            }

            let skills = discover_all_skills(&ws_root);
            let found = skills.iter().find(|s| s.name.eq_ignore_ascii_case(&target_name) || s.name.to_lowercase().contains(&target_name));

            match found {
                Some(skill) => {
                    let content = fs::read_to_string(&skill.skill_file).unwrap_or_default();
                    let (_, body) = parse_yaml_frontmatter(&content);

                    if is_json {
                        let mut val = serde_json::to_value(skill).unwrap();
                        if let Some(obj) = val.as_object_mut() {
                            obj.insert("full_content".into(), serde_json::Value::String(content));
                            obj.insert("instructions_body".into(), serde_json::Value::String(body));
                        }
                        println!("{}", serde_json::to_string_pretty(&val).unwrap());
                        return;
                    }

                    println!("\n\x1b[1m\x1b[36m======================================================\x1b[0m");
                    println!("\x1b[1m\x1b[32mSkill: {}\x1b[0m ({})", skill.name, skill.version);
                    println!("\x1b[36m======================================================\x1b[0m");
                    println!("\x1b[1mHarness Source:\x1b[0m {} [{}]", skill.harness_name, skill.scope);
                    println!("\x1b[1mDirectory:\x1b[0m      {}", skill.path);
                    println!("\x1b[1mSKILL.md:\x1b[0m       {}", skill.skill_file);
                    if !skill.description.is_empty() {
                        println!("\x1b[1mDescription:\x1b[0m    {}", skill.description);
                    }
                    if !skill.tags.is_empty() {
                        println!("\x1b[1mTags:\x1b[0m           {}", skill.tags.join(", "));
                    }

                    if skill.has_scripts {
                        println!("\n\x1b[1mExecutable Scripts ({}):\x1b[0m", skill.scripts.len());
                        for sc in &skill.scripts {
                            println!("  • scripts/{}", sc);
                        }
                    }

                    if skill.has_references {
                        println!("\n\x1b[1mReference Docs ({}):\x1b[0m", skill.references.len());
                        for rf in &skill.references {
                            println!("  • references/{}", rf);
                        }
                    }

                    println!("\n\x1b[1m\x1b[33m--- Instructions Preview ---\x1b[0m");
                    let lines: Vec<&str> = body.lines().collect();
                    let max_lines = if is_full { lines.len() } else { 25.min(lines.len()) };
                    for line in &lines[..max_lines] {
                        println!("{}", line);
                    }
                    if !is_full && lines.len() > max_lines {
                        println!("\n\x1b[2m... [{} more lines. Use --full to see all instructions] ...\x1b[0m", lines.len() - max_lines);
                    }
                    println!();
                }
                None => {
                    eprintln!("\x1b[31mError: Skill '{}' not found.\x1b[0m", target_name);
                    std::process::exit(1);
                }
            }
        }

        "link" | "install" | "share" => {
            let target_name = args.iter().skip(1).filter(|a| !a.starts_with('-')).nth(1).unwrap_or(&String::new()).to_lowercase();
            let to_harness = match to_filter {
                Some(t) => t.to_lowercase(),
                None => {
                    eprintln!("\x1b[31mError: Missing destination harness. Usage: skills link <skill> --to <harness>\x1b[0m");
                    std::process::exit(1);
                }
            };

            let skills = discover_all_skills(&ws_root);
            let skill = skills.iter().find(|s| s.name.eq_ignore_ascii_case(&target_name) || s.name.to_lowercase().contains(&target_name));
            let skill = match skill {
                Some(s) => s,
                None => {
                    eprintln!("\x1b[31mError: Skill '{}' not found.\x1b[0m", target_name);
                    std::process::exit(1);
                }
            };

            let defs = get_harness_definitions(&ws_root);
            let target_def = defs.iter().find(|d| d.id.eq_ignore_ascii_case(&to_harness));
            let target_def = match target_def {
                Some(d) => d,
                None => {
                    eprintln!("\x1b[31mError: Unknown target harness '{}'.\x1b[0m", to_harness);
                    std::process::exit(1);
                }
            };

            let dest_parent = if is_project {
                target_def.workspace_paths.first()
            } else {
                target_def.global_paths.first()
            };

            let dest_parent = match dest_parent {
                Some(p) => p,
                None => {
                    eprintln!("\x1b[31mError: No valid path for harness '{}'.\x1b[0m", to_harness);
                    std::process::exit(1);
                }
            };

            match link_or_copy(Path::new(&skill.path), dest_parent, is_copy) {
                Ok(msg) => {
                    println!("\x1b[32m[OK] {}\x1b[0m", msg);
                    println!("Skill '{}' is now instantly available in {}!", skill.name, target_def.name);
                }
                Err(err) => {
                    eprintln!("\x1b[31m[FAIL] {}\x1b[0m", err);
                    std::process::exit(1);
                }
            }
        }

        "sync" => {
            let from_harness = match from_filter {
                Some(f) => f.to_lowercase(),
                None => {
                    eprintln!("\x1b[31mError: Missing source harness. Usage: skills sync --from <harness> --to <harness>\x1b[0m");
                    std::process::exit(1);
                }
            };
            let to_harness = match to_filter {
                Some(t) => t.to_lowercase(),
                None => {
                    eprintln!("\x1b[31mError: Missing target harness. Usage: skills sync --from <harness> --to <harness>\x1b[0m");
                    std::process::exit(1);
                }
            };

            let skills = discover_all_skills(&ws_root);
            let source_skills: Vec<&SkillInfo> = skills.iter().filter(|s| s.harness_id.eq_ignore_ascii_case(&from_harness)).collect();
            if source_skills.is_empty() {
                println!("\x1b[33mNo skills found in source harness '{}'.\x1b[0m", from_harness);
                return;
            }

            let defs = get_harness_definitions(&ws_root);
            let target_def = match defs.iter().find(|d| d.id.eq_ignore_ascii_case(&to_harness)) {
                Some(d) => d,
                None => {
                    eprintln!("\x1b[31mError: Unknown target harness '{}'.\x1b[0m", to_harness);
                    std::process::exit(1);
                }
            };

            let dest_parent = if is_project {
                target_def.workspace_paths.first()
            } else {
                target_def.global_paths.first()
            };

            let dest_parent = match dest_parent {
                Some(p) => p,
                None => {
                    eprintln!("\x1b[31mError: No valid destination path for harness '{}'.\x1b[0m", to_harness);
                    std::process::exit(1);
                }
            };

            println!("\n\x1b[1m\x1b[36mSyncing {} skill(s) from '{}' -> '{}' ({})\x1b[0m\n", source_skills.len(), from_harness, to_harness, dest_parent.display());
            let mut synced = 0;
            for s in source_skills {
                let src_path = Path::new(&s.path);
                let dest = dest_parent.join(&s.name);
                if dest.exists() {
                    println!("  \x1b[2m• {}: already exists, skipping\x1b[0m", s.name);
                    continue;
                }
                match link_or_copy(src_path, dest_parent, is_copy) {
                    Ok(_) => {
                        println!("  \x1b[32m[OK] {}: linked\x1b[0m", s.name);
                        synced += 1;
                    }
                    Err(e) => {
                        println!("  \x1b[31m[FAIL] {}: {}\x1b[0m", s.name, e);
                    }
                }
            }
            println!("\n\x1b[32mSync complete: {} skill(s) linked.\x1b[0m\n", synced);
        }

        _ => {
            println!("\x1b[31mUnknown command: '{}'\x1b[0m", command);
            println!("Run 'skills --help' for usage instructions.");
        }
    }
}
