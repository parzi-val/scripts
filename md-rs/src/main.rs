// mdv: render markdown in the terminal with ANSI colors.
// Usage: mdv [-i] [-r] [file]   (reads stdin when no file is given)
//   Clears the screen first; -i renders inline without clearing.
//   -r keeps running and re-renders whenever the file changes.
use pulldown_cmark::{Alignment, CodeBlockKind, Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use std::io::{IsTerminal, Read, Write};

const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const ITALIC: &str = "\x1b[3m";
const UNDERLINE: &str = "\x1b[4m";
const STRIKE: &str = "\x1b[9m";
const CODE: &str = "\x1b[38;5;214m"; // orange
const LINK: &str = "\x1b[34m";
const PATH: &str = "\x1b[38;5;141m"; // lavender
const FILE: &str = "\x1b[1;38;5;203m"; // bold coral

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

// Visible width: chars outside ANSI escapes.
// ponytail: counts chars, so wide CJK/emoji misalign; use unicode-width if that matters.
fn vis(s: &str) -> usize {
    let mut n = 0;
    let mut esc = false;
    for c in s.chars() {
        match (esc, c) {
            (false, '\x1b') => esc = true,
            (true, c) if c.is_ascii_alphabetic() => esc = false,
            (true, _) => {}
            _ => n += 1,
        }
    }
    n
}

// Word-wrap ANSI-styled text to `w` columns, carrying open styles onto the next line.
fn wrap(s: &str, w: usize) -> Vec<String> {
    let mut lines = vec![String::new()];
    let (mut len, mut active) = (0usize, String::new());
    for word in s.split(' ') {
        if len > 0 && len + 1 + vis(word) > w {
            lines.last_mut().unwrap().push_str(RESET);
            lines.push(active.clone());
            len = 0;
        } else if len > 0 {
            lines.last_mut().unwrap().push(' ');
            len += 1;
        }
        let mut chars = word.chars();
        while let Some(c) = chars.next() {
            if c == '\x1b' {
                let mut esc = String::from(c);
                for d in chars.by_ref() {
                    esc.push(d);
                    if d.is_ascii_alphabetic() { break }
                }
                if esc == RESET { active.clear() } else { active.push_str(&esc) }
                lines.last_mut().unwrap().push_str(&esc);
                continue;
            }
            if len == w { // word longer than the column: hard break
                lines.last_mut().unwrap().push_str(RESET);
                lines.push(active.clone());
                len = 0;
            }
            lines.last_mut().unwrap().push(c);
            len += 1;
        }
    }
    lines
}

fn draw_table(rows: &[Vec<String>], aligns: &[Alignment], width: usize, pre: &str) -> String {
    let n = rows.iter().map(Vec::len).max().unwrap_or(0);
    fn cell(r: &[String], i: usize) -> &str { r.get(i).map(String::as_str).unwrap_or("") }
    let mut ws: Vec<usize> = (0..n).map(|i| rows.iter().map(|r| vis(cell(r, i))).max().unwrap_or(0).max(1)).collect();
    // Shrink the widest column until the table fits the terminal (cells then wrap).
    // First pass keeps every column at least as wide as its longest word, so words
    // only get split mid-way when there's no other way to fit.
    let longest_word: Vec<usize> = (0..n)
        .map(|i| rows.iter().flat_map(|r| cell(r, i).split(' ').map(vis)).max().unwrap_or(0)).collect();
    let avail = width.saturating_sub(vis(pre) + 3 * n + 1);
    for floor in [&longest_word, &vec![8; n]] {
        while ws.iter().sum::<usize>() > avail {
            let Some(i) = (0..n).filter(|&i| ws[i] > floor[i]).max_by_key(|&i| ws[i]) else { break };
            ws[i] -= 1;
        }
    }
    let rule = |l: &str, m: &str, r: &str| {
        let segs: Vec<String> = ws.iter().map(|w| "\u{2500}".repeat(w + 2)).collect();
        format!("{pre}{DIM}{l}{}{r}{RESET}\n", segs.join(m))
    };
    let wrapped: Vec<Vec<Vec<String>>> = rows.iter()
        .map(|r| (0..n).map(|i| wrap(cell(r, i), ws[i])).collect()).collect();
    let multiline = wrapped.iter().skip(1).any(|r| r.iter().any(|c| c.len() > 1));

    let mut out = rule("\u{250c}", "\u{252c}", "\u{2510}");
    for (ri, row) in wrapped.iter().enumerate() {
        if ri == 1 || (ri > 1 && multiline) {
            out.push_str(&rule("\u{251c}", "\u{253c}", "\u{2524}"));
        }
        for li in 0..row.iter().map(Vec::len).max().unwrap_or(1) {
            out.push_str(&format!("{pre}{DIM}\u{2502}{RESET}"));
            for (i, lines) in row.iter().enumerate() {
                let text = lines.get(li).map(String::as_str).unwrap_or("");
                let gap = ws[i].saturating_sub(vis(text));
                let (l, r) = match aligns.get(i) {
                    Some(Alignment::Right) => (gap, 0),
                    Some(Alignment::Center) => (gap / 2, gap - gap / 2),
                    _ => (0, gap),
                };
                out.push_str(&format!(" {}{text}{RESET}{} {DIM}\u{2502}{RESET}", " ".repeat(l), " ".repeat(r)));
            }
            out.push('\n');
        }
    }
    out + &rule("\u{2514}", "\u{2534}", "\u{2518}")
}

fn render(md: &str, width: usize) -> String {
    let mut out = String::new();
    let mut rows: Vec<Vec<String>> = Vec::new();
    let mut aligns: Vec<Alignment> = Vec::new();
    let mut cell_start = 0usize;
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
                Tag::Table(a) => { aligns = a; rows.clear() }
                Tag::TableHead => { styles.push(BOLD); rows.push(Vec::new()) }
                Tag::TableRow => rows.push(Vec::new()),
                Tag::TableCell => {
                    cell_start = out.len();
                    styles.iter().for_each(|s| out.push_str(s));
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
                // Cells render into `out` like any inline text, then get moved into `rows`.
                TagEnd::TableCell => {
                    let c = out.split_off(cell_start);
                    rows.last_mut().unwrap().push(c);
                }
                TagEnd::TableHead => { styles.pop(); }
                TagEnd::Table => {
                    out.push_str(&draw_table(&rows, &aligns, width, &prefix(quote)));
                    out.push('\n');
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

fn term_width() -> usize {
    terminal_size::terminal_size().map(|(w, _)| w.0 as usize).unwrap_or(100)
}

// Builds the whole screen in one string so a reload repaints without flicker.
fn show(md: &str, path: Option<&str>, clear: bool) {
    let mut s = String::new();
    // Clear screen + scrollback, cursor home. Skipped when piped so files stay clean.
    if clear && std::io::stdout().is_terminal() {
        s.push_str("\x1b[2J\x1b[3J\x1b[H");
    }
    // Header: "<dir> <file>", each underlined in its own color.
    if let Some(path) = path {
        let abs = std::path::absolute(path).unwrap_or(path.into());
        let dir = abs.parent().map(|d| d.display().to_string()).unwrap_or_default();
        let name = abs.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        s.push_str(&format!("{UNDERLINE}{PATH}{dir}{RESET} {UNDERLINE}{FILE}{name}{RESET}\n\n"));
    }
    // PowerShell pipes prepend a BOM, which would hide a leading "#"
    s.push_str(&render(md.trim_start_matches('\u{feff}'), term_width()));
    let mut out = std::io::stdout().lock();
    out.write_all(s.as_bytes()).unwrap();
    out.flush().unwrap();
}

fn main() {
    let (flags, files): (Vec<String>, Vec<String>) =
        std::env::args().skip(1).partition(|a| a.starts_with('-'));
    let inline = flags.iter().any(|f| f == "-i");
    let reload = flags.iter().any(|f| f == "-r");
    let Some(path) = files.into_iter().next() else {
        if reload {
            eprintln!("mdv: -r needs a file");
            std::process::exit(1)
        }
        let mut md = String::new();
        std::io::stdin().read_to_string(&mut md).unwrap();
        return show(&md, None, !inline);
    };
    let read = || std::fs::read_to_string(&path);
    let md = read().unwrap_or_else(|e| {
        eprintln!("mdv: {path}: {e}");
        std::process::exit(1)
    });
    show(&md, Some(&path), !inline || reload);
    if !reload {
        return;
    }
    // Live mode: repaint when the file's mtime or the terminal width changes. Ctrl+C quits.
    // ponytail: 300ms polling, swap for the notify crate if latency/CPU ever matters.
    let mtime = || std::fs::metadata(&path).and_then(|m| m.modified()).ok();
    let (mut last_mtime, mut last_width) = (mtime(), term_width());
    loop {
        std::thread::sleep(std::time::Duration::from_millis(300));
        let (m, w) = (mtime(), term_width());
        if m == last_mtime && w == last_width {
            continue;
        }
        // Editors may delete/rename the file mid-save; keep the old screen and retry next tick.
        if let Ok(md) = read() {
            show(&md, Some(&path), true);
            (last_mtime, last_width) = (m, w);
        }
    }
}

#[test]
fn renders_basics() {
    let out = render("# Hi\n\nsome **bold** and `code`\n\n- a\n- b\n\n1. x\n2. y\n", 80);
    assert!(out.contains("\x1b[1;4;35mHi"));
    assert!(out.contains("\x1b[1mbold\x1b[0m"));
    assert!(out.contains("code") && !out.contains("`") && !out.contains("#"));
    assert!(out.contains("\u{2022}"));
    assert!(out.contains("1.") && out.contains("2."));
}

#[test]
fn renders_tables() {
    let long = "word ".repeat(30);
    let out = render(&format!("| A | B |\n|---|--:|\n| x | {long}|\n| **y** | 2 |\n"), 40);
    let lines: Vec<&str> = out.lines().collect();
    // Every table line fits the width and has the same visible length.
    assert!(lines.iter().all(|l| vis(l) == vis(lines[0]) && vis(l) <= 40), "{out}");
    assert!(out.contains("\x1b[1mA") && out.contains("\x1b[1my"));
    assert!(lines.len() > 6, "long cell should wrap: {out}");
    assert_eq!(wrap("\x1b[1mab cd\x1b[0m", 2), ["\x1b[1mab\x1b[0m", "\x1b[1mcd\x1b[0m"]);
}
