// mdv: render markdown in the terminal with ANSI colors.
// Usage: mdv [file]   (reads stdin when no file is given)
use pulldown_cmark::{CodeBlockKind, Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use std::io::{Read, Write};

const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const ITALIC: &str = "\x1b[3m";
const UNDERLINE: &str = "\x1b[4m";
const STRIKE: &str = "\x1b[9m";
const CODE: &str = "\x1b[38;5;214m"; // orange
const LINK: &str = "\x1b[34m";

fn heading_color(level: HeadingLevel) -> &'static str {
    match level {
        HeadingLevel::H1 => "\x1b[1;4;35m", // bold underline magenta
        HeadingLevel::H2 => "\x1b[1;36m",   // bold cyan
        HeadingLevel::H3 => "\x1b[1;32m",   // bold green
        HeadingLevel::H4 => "\x1b[1;33m",   // bold yellow
        HeadingLevel::H5 => "\x1b[1;34m",   // bold blue
        HeadingLevel::H6 => "\x1b[1;37m",   // bold white
    }
}

fn render(md: &str) -> String {
    let mut out = String::new();
    // Inline styles stack, so after closing one we re-apply whatever is still open.
    let mut styles: Vec<&str> = Vec::new();
    let mut lists: Vec<Option<u64>> = Vec::new(); // None = bullet, Some(n) = next number
    let mut quote = 0usize;
    let mut in_code_block = false;
    let mut link_url = String::new();

    let restyle = |out: &mut String, styles: &[&str]| {
        out.push_str(RESET);
        styles.iter().for_each(|s| out.push_str(s));
    };
    let prefix = |quote: usize| match quote {
        0 => String::new(),
        n => format!("{DIM}{}{RESET}", "\u{2502} ".repeat(n)),
    };

    for ev in Parser::new_ext(md, Options::all()) {
        match ev {
            Event::Start(tag) => match tag {
                Tag::Heading { level, .. } => {
                    styles.push(heading_color(level));
                    out.push_str(&prefix(quote));
                    out.push_str(heading_color(level));
                }
                Tag::Paragraph => out.push_str(&prefix(quote)),
                Tag::BlockQuote(_) => quote += 1,
                Tag::CodeBlock(kind) => {
                    in_code_block = true;
                    if let CodeBlockKind::Fenced(lang) = kind {
                        if !lang.is_empty() {
                            out.push_str(&format!("{DIM}  \u{2500}\u{2500} {lang}{RESET}\n"));
                        }
                    }
                }
                Tag::List(start) => {
                    if !lists.is_empty() && !out.ends_with('\n') {
                        out.push('\n');
                    }
                    lists.push(start);
                }
                Tag::Item => {
                    let indent = "  ".repeat(lists.len() - 1);
                    let marker = match lists.last_mut() {
                        Some(Some(n)) => { *n += 1; format!("{}.", *n - 1) }
                        _ => "\u{2022}".to_string(),
                    };
                    out.push_str(&format!("{}{indent}{CODE}{marker}{RESET} ", prefix(quote)));
                }
                Tag::Emphasis => { styles.push(ITALIC); out.push_str(ITALIC) }
                Tag::Strong => { styles.push(BOLD); out.push_str(BOLD) }
                Tag::Strikethrough => { styles.push(STRIKE); out.push_str(STRIKE) }
                Tag::Link { dest_url, .. } => {
                    link_url = dest_url.to_string();
                    styles.push(UNDERLINE);
                    out.push_str(LINK);
                    out.push_str(UNDERLINE);
                }
                _ => {}
            },
            Event::End(tag) => match tag {
                TagEnd::Heading(_) => { styles.pop(); out.push_str(RESET); out.push_str("\n\n") }
                TagEnd::Paragraph => out.push_str(if lists.is_empty() { "\n\n" } else { "\n" }),
                TagEnd::BlockQuote(_) => quote -= 1,
                TagEnd::CodeBlock => { in_code_block = false; out.push('\n') }
                TagEnd::List(_) => { lists.pop(); if lists.is_empty() { out.push('\n') } }
                TagEnd::Item => if !out.ends_with('\n') { out.push('\n') },
                TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => {
                    styles.pop();
                    restyle(&mut out, &styles);
                }
                TagEnd::Link => {
                    styles.pop();
                    restyle(&mut out, &styles);
                    out.push_str(&format!(" {DIM}({link_url}){RESET}"));
                    restyle(&mut out, &styles);
                }
                _ => {}
            },
            Event::Text(t) if in_code_block => {
                for line in t.lines() {
                    out.push_str(&format!("{}  {CODE}{line}{RESET}\n", prefix(quote)));
                }
            }
            Event::Text(t) => out.push_str(&t),
            Event::Code(t) => {
                out.push_str(&format!("{CODE}{t}"));
                restyle(&mut out, &styles);
            }
            Event::SoftBreak => out.push(' '),
            Event::HardBreak => out.push('\n'),
            Event::Rule => out.push_str(&format!("{DIM}{}{RESET}\n\n", "\u{2500}".repeat(40))),
            Event::TaskListMarker(done) => out.push_str(if done { "[x] " } else { "[ ] " }),
            _ => {}
        }
    }
    out.trim_end().to_string() + RESET + "\n"
}

fn main() {
    let mut md = String::new();
    match std::env::args().nth(1) {
        Some(path) => md = std::fs::read_to_string(&path).unwrap_or_else(|e| {
            eprintln!("mdv: {path}: {e}");
            std::process::exit(1)
        }),
        None => { std::io::stdin().read_to_string(&mut md).unwrap(); }
    }
    // PowerShell pipes prepend a BOM, which would hide a leading "#"
    std::io::stdout().write_all(render(md.trim_start_matches('\u{feff}')).as_bytes()).unwrap();
}

#[test]
fn renders_basics() {
    let out = render("# Hi\n\nsome **bold** and `code`\n\n- a\n- b\n\n1. x\n2. y\n");
    assert!(out.contains("\x1b[1;4;35mHi"));
    assert!(out.contains("\x1b[1mbold\x1b[0m"));
    assert!(out.contains("code") && !out.contains("`") && !out.contains("#"));
    assert!(out.contains("\u{2022}"));
    assert!(out.contains("1.") && out.contains("2."));
}
