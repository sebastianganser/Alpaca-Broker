#!/usr/bin/env node
// PreToolUse hook: keep the local .env files out of the Claude session.
//
// The deny rules in .claude/settings.json cover only the Read/Edit tools. The
// local .env points at the production database, so this hook also stops shell
// commands (Bash, PowerShell), Grep calls and file tools that name `.env`,
// `.env.local` or `.env.production`. `.env.example` stays allowed.
//
// Exit code 2 blocks the call and shows stderr to Claude. Anything unexpected
// (no stdin, bad JSON, unknown tool) exits 0 so the session keeps working; the
// deny rules stay the primary control. Written in Node rather than Python on
// purpose: Python exits with code 2 when the script file is missing, which
// Claude Code would read as "block" for every tool call.
//
// The Grep tool (ripgrep) skips hidden and git-ignored files, but an explicit
// glob that matches the name overrides both (even `*`), so Grep globs are
// tested against the protected names.
//
// This guards against accidental access, not deliberate circumvention. Known
// limits: commands that do not spell out the name are not caught, e.g. shell
// wildcards (`cat .en*`), variables or other indirection, recursive greps over
// the repo (`grep -r X .`) and Windows 8.3 short names.
//
// Tests: node .claude/hooks/guard-env.test.mjs

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// `.env` as a path component: preceded by start of text or any character that
// cannot be part of a file name (whitespace, separators, quotes, `{`, `@`, …,
// so `process.env` does not match); an optional suffix such as `.local` (but
// not `.example`); optional trailing dots (Windows opens `.env.` as `.env`);
// and no further name characters after it.
const ENV_FILE =
  /(?:^|[^A-Za-z0-9_.-])\.env(?:\.(?!example\b)[A-Za-z0-9_-]+)?(?:\.+(?![A-Za-z0-9_.-]))?(?![A-Za-z0-9_.-])/i;

// ---- git commit messages ---------------------------------------------------
// A `git commit` (optionally after `cd <dir> &&`) may mention .env in its
// message without touching the file. Only literal message arguments given
// directly to that git commit are ignored; scanning stops at the first
// separator or redirection, so `&& cat .env`, `< .env`, `-F .env` and the
// heredocs of later commands are still checked. Text the shell expands
// (`-m "$(cat .env)"`, backticks, `@"…"@`) is not literal and stays in.
const GIT_COMMIT = /^\s*(?:cd\s+(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*)?git\s+commit\b/;
const MESSAGE_FLAG = String.raw`(?:--message|-[A-Za-z]*m)`;
// Double-quoted text counts as literal only without `$` and backticks at all
// (no escape handling: `\$` escapes in Bash but not in PowerShell).
const QUOTED_LITERALS = [
  new RegExp(String.raw`^${MESSAGE_FLAG}(?:=|\s+)'[^']*'`),
  new RegExp(String.raw`^${MESSAGE_FLAG}(?:=|\s+)"[^"$\x60]*"`),
];
const LITERAL_MESSAGE = {
  Bash: [
    // -m "$(cat <<'EOF' … EOF )": quoted delimiter, so the body is not
    // expanded; the body may not contain the delimiter line, so the first
    // delimiter line must close the substitution.
    new RegExp(
      String.raw`^${MESSAGE_FLAG}(?:=|\s+)"\$\(cat\s+<<-?\s*(['"])(\w+)\1[ \t]*\n` +
        String.raw`(?:(?![ \t]*\2[ \t]*\n)[^\n]*\n)*[ \t]*\2[ \t]*\n\s*\)"`,
    ),
    ...QUOTED_LITERALS,
  ],
  PowerShell: [
    // -m @'…'@ (literal here-string, closing '@ at column 0)
    new RegExp(String.raw`^${MESSAGE_FLAG}\s+@'[ \t]*\n(?:(?!'@)[^\n]*\n)*'@`),
    ...QUOTED_LITERALS,
  ],
};
// One plain argument: a quoted string or a bare word without shell operators.
const ARGUMENT = /^(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s"'&|;<>()`$]+)/;

function withoutCommitMessage(command, shell) {
  const commit = GIT_COMMIT.exec(command);
  if (!commit) return command;
  const literals = LITERAL_MESSAGE[shell] ?? [];
  let i = commit[0].length;
  let out = command.slice(0, i);
  while (i < command.length) {
    const rest = command.slice(i);
    const space = /^[ \t]+/.exec(rest);
    const message = space ? null : literals.map((re) => re.exec(rest)).find(Boolean);
    const argument = space || message ? null : ARGUMENT.exec(rest);
    const step = space ?? message ?? argument;
    if (!step) break; // separator, redirection, newline, substitution: stop here
    out += message ? " " : step[0];
    i += step[0].length;
  }
  return out + command.slice(i);
}

// ---- Grep globs --------------------------------------------------------------
const PROTECTED_NAMES = [".env", ".env.local", ".env.production"];

function expandBraces(glob) {
  const group = /\{([^{}]*)\}/.exec(glob);
  if (!group) return [glob];
  const before = glob.slice(0, group.index);
  const after = glob.slice(group.index + group[0].length);
  return group[1].split(",").flatMap((alt) => expandBraces(before + alt + after));
}

function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      i++;
      if (glob[i + 1] === "/") {
        i++;
        re += "(?:.*/)?";
      } else {
        re += ".*";
      }
    } else if (c === "*") {
      re += "[^/]*";
    } else if (c === "?") {
      re += "[^/]";
    } else if (c === "[" && glob.indexOf("]", i + 2) !== -1) {
      const end = glob.indexOf("]", i + 2);
      const body = glob.slice(i + 1, end).replace(/\\/g, "\\\\");
      re += `[${body.startsWith("!") ? "^" + body.slice(1) : body}]`;
      i = end;
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${re}$`, "i");
}

// True when the glob could select one of the protected files. Errors count as
// a match (block), exclusion globs (`!…`) never select anything.
export function globMayMatchEnv(glob) {
  if (typeof glob !== "string") return false;
  return glob
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .some((part) => {
      const g = part.replace(/\\/g, "/").replace(/^(?:\.\/|\/)+/, "");
      if (g.startsWith("!")) return false;
      if ((g.match(/\{/g) ?? []).length > 8) return true; // avoid exponential expansion
      try {
        const alternatives = expandBraces(g);
        if (alternatives.length > 256) return true;
        // Only the last segment decides the file name; any directory prefix may
        // lead to the real file (e.g. path ".." with glob "Alpaca-Broker/.env").
        return alternatives.some((alt) => {
          const base = alt.split("/").pop();
          if (base === "") return true;
          const re = globToRegExp(base);
          return PROTECTED_NAMES.some((name) => re.test(name));
        });
      } catch {
        return true;
      }
    });
}

// ---- entry point -------------------------------------------------------------
export function findEnvReference(toolName, toolInput) {
  if (!toolInput || typeof toolInput !== "object") return null;
  let fields;
  switch (toolName) {
    case "Bash":
    case "PowerShell":
      fields = [
        typeof toolInput.command === "string"
          ? withoutCommitMessage(toolInput.command, toolName)
          : null,
      ];
      break;
    case "Grep":
      // `pattern` is the search text (e.g. "process.env"), not a file.
      if (globMayMatchEnv(toolInput.glob)) return toolInput.glob;
      fields = [toolInput.path];
      break;
    case "Read":
    case "Edit":
    case "Write":
    case "MultiEdit":
      fields = [toolInput.file_path];
      break;
    default:
      return null;
  }
  for (const value of fields) {
    if (typeof value === "string" && ENV_FILE.test(value)) return value;
  }
  return null;
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8"));
  } catch {
    process.exit(0);
  }
  const hit = findEnvReference(payload?.tool_name, payload?.tool_input);
  if (hit === null) process.exit(0);
  process.stderr.write(
    "Blocked by .claude/hooks/guard-env.mjs: this call touches a local .env file, " +
      "which holds the production credentials. Use .env.example for the variable " +
      "names; if the real values are needed, ask Sebastian.\n",
  );
  process.exit(2);
}

// Run only when executed as a script, so tests can import findEnvReference.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
