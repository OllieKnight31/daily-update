#!/usr/bin/env python3
"""Daily Update helper: collect your Claude Code work, then deliver an approved update.

Subcommands:
  collect                  Print a digest of every Claude Code session since the last update.
  collect --hours 48       Use a fixed window instead.
  collect --since 2026-01-31T09:00
  mark-sent [--at ISO]     Record the time the next update should start from.
  config                   Show the settings in use.
  config --set key=value   Save one setting (your_name, recipient_name, recipient_contact, channel).
  deliver --approved       Read the approved update on standard input and deliver it.

Settings and state live in ~/.config/daily-update (or the folder named by DAILY_UPDATE_HOME).
Nothing here uses the network. Works with Python 3.8 or newer, standard library only.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MAX_TOTAL = 60000  # characters of digest before trimming kicks in
SEND_TIMEOUT = 120  # seconds allowed for a custom send command
SKIP_PREFIXES = ("/daily-update",)
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
CHANNELS = ("clipboard", "command", "mcp")
DEFAULTS = {
    "your_name": "",
    "recipient_name": "",
    "recipient_contact": "",
    "channel": "clipboard",
    "send_command": "",
    "mcp_tool": "",
    "mcp_arguments": {},
    "default_hours": 24,
    "exclude_folders": [],
}
SETTABLE = ("your_name", "recipient_name", "recipient_contact", "channel")


# ---------- locations ----------

def data_home():
    return os.path.expanduser(os.environ.get("DAILY_UPDATE_HOME") or "~/.config/daily-update")


def config_path():
    return os.path.join(data_home(), "config.json")


def state_path():
    return os.path.join(data_home(), "state.json")


def last_update_path():
    return os.path.join(data_home(), "last_update.txt")


def projects_dir(override=None):
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return os.path.join(os.path.expanduser(base), "projects")


def warn(msg):
    print("Warning: " + msg, file=sys.stderr)


# ---------- config and state ----------

def load_config():
    """Return (config, problem). Never raises: a missing or broken file gives the defaults."""
    cfg = dict(DEFAULTS)
    path = config_path()
    problem = None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k in DEFAULTS:
                if k in raw and raw[k] is not None:
                    cfg[k] = raw[k]
        else:
            problem = "%s is not a JSON object, using the defaults" % path
    except FileNotFoundError:
        problem = "no config file at %s, using the defaults" % path
    except (OSError, ValueError) as e:
        problem = "could not read %s (%s), using the defaults" % (path, e)

    for k in ("your_name", "recipient_name", "recipient_contact", "send_command", "mcp_tool"):
        cfg[k] = cfg[k].strip() if isinstance(cfg[k], str) else str(cfg[k]).strip()
    ch = str(cfg["channel"]).strip().lower()
    if ch not in CHANNELS:
        problem = problem or "unknown channel %r in config, using clipboard" % cfg["channel"]
        ch = "clipboard"
    cfg["channel"] = ch
    if not isinstance(cfg["mcp_arguments"], dict):
        cfg["mcp_arguments"] = {}
    if not isinstance(cfg["exclude_folders"], list):
        cfg["exclude_folders"] = []
    cfg["exclude_folders"] = [str(x) for x in cfg["exclude_folders"] if str(x).strip()]
    try:
        hours = float(cfg["default_hours"])
        cfg["default_hours"] = hours if hours > 0 else 24
    except (TypeError, ValueError):
        cfg["default_hours"] = 24
    return cfg, problem


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def parse_ts(s):
    """Parse an ISO timestamp. Returns an aware datetime, or None if it cannot be read."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def load_last_sent():
    try:
        with open(state_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        warn("could not read %s, ignoring it" % state_path())
        return None
    if not isinstance(raw, dict):
        warn("%s is not in the expected shape, ignoring it" % state_path())
        return None
    dt = parse_ts(raw.get("last_sent"))
    if raw.get("last_sent") and dt is None:
        warn("the saved last update time could not be read, ignoring it")
    return dt


# ---------- reading transcripts ----------

def user_text(msg):
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        parts = [c]
    elif isinstance(c, list):
        parts = [b.get("text") for b in c if isinstance(b, dict) and b.get("type") == "text"]
    else:
        parts = []
    text = "\n".join(p for p in parts if isinstance(p, str) and p).strip()
    # Skip harness wrappers, slash command plumbing, tool results and reminders.
    if not text or text.startswith("<") or text.startswith("[Request interrupted"):
        return ""
    return text


def assistant_parts(msg):
    texts, files = [], []
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return texts, files
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            t = b.get("text")
            if isinstance(t, str) and t.strip():
                texts.append(t.strip())
        elif b.get("type") == "tool_use" and b.get("name") in FILE_TOOLS:
            inp = b.get("input")
            if isinstance(inp, dict):
                p = inp.get("file_path") or inp.get("notebook_path")
                if isinstance(p, str) and p:
                    files.append(p)
    return texts, files


def clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def read_session(path, since):
    s = {"title": None, "cwd": None, "first": None, "last": None, "asks": [], "replies": [], "files": []}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            t = d.get("type")
            if t == "ai-title":
                if isinstance(d.get("aiTitle"), str):
                    s["title"] = d["aiTitle"]
                continue
            if t not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                continue
            ts = parse_ts(d.get("timestamp"))
            if ts is None or ts < since:
                continue
            if not s["cwd"] and isinstance(d.get("cwd"), str):
                s["cwd"] = d["cwd"]
            s["first"] = s["first"] or ts
            s["last"] = ts
            if t == "user":
                if d.get("isCompactSummary"):
                    continue  # an automatic recap of earlier context, not something you asked
                txt = user_text(d.get("message"))
                if txt and not txt.lstrip().startswith(SKIP_PREFIXES):
                    s["asks"].append(txt)
            else:
                if d.get("isApiErrorMessage"):
                    continue
                texts, files = assistant_parts(d.get("message"))
                s["replies"].extend(texts)
                s["files"].extend(files)
    return s


def collect(since, root, exclude):
    sessions, unreadable = [], 0
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < since:
                continue
            s = read_session(path, since)
        except OSError:
            unreadable += 1
            continue
        if not (s["asks"] or s["files"]):
            continue
        if s["cwd"] and any(x in s["cwd"] for x in exclude):
            continue
        sessions.append(s)
    sessions.sort(key=lambda x: x["first"])
    return sessions, unreadable


def tidy(path, home):
    return "~" + path[len(home):] if home and path.startswith(home) else path


def render_one(i, s, ask_n, reply_n, keep_replies, keep_asks, keep_files):
    home = os.path.expanduser("~")
    out = ["## Session %d: %s" % (i, clip(s["title"] or "untitled", 120))]
    first, last = s["first"].astimezone(), s["last"].astimezone()
    out.append("Folder: %s | %s to %s" % (tidy(s["cwd"] or "?", home), first.strftime("%Y-%m-%d %H:%M"),
                                         last.strftime("%H:%M" if first.date() == last.date() else "%Y-%m-%d %H:%M")))
    asks = s["asks"]
    out.append("You asked:")
    if keep_asks and len(asks) > keep_asks:
        head = keep_asks // 2
        tail = keep_asks - head
        out.extend("- " + clip(a, ask_n) for a in asks[:head])
        out.append("- (%d more requests in between, left out to save space)" % (len(asks) - keep_asks))
        out.extend("- " + clip(a, ask_n) for a in asks[-tail:])
    else:
        out.extend("- " + clip(a, ask_n) for a in asks)
    replies = s["replies"][-keep_replies:] if keep_replies else s["replies"]
    if replies:
        out.append("Claude reported (latest last):")
        out.extend("- " + clip(r, reply_n) for r in replies)
    files = sorted(set(tidy(f, home) for f in s["files"]))
    if files:
        more = " ... and %d more" % (len(files) - keep_files) if len(files) > keep_files else ""
        out.append("Files written or edited (%d): %s%s" % (len(files), ", ".join(files[:keep_files]), more))
    out.append("")
    return "\n".join(out)


def render(sessions):
    """Compress step by step until the digest fits, then drop whole sessions rather than cut one in half."""
    levels = ((600, 1200, 0, 0, 25), (400, 700, 6, 30, 25), (250, 500, 3, 12, 15), (150, 400, 1, 6, 8), (100, 250, 1, 4, 5))
    blocks = []
    for level in levels:
        blocks = [render_one(i, s, *level) for i, s in enumerate(sessions, 1)]
        if sum(len(b) + 1 for b in blocks) <= MAX_TOTAL:
            return "\n".join(blocks), 0
    kept, used = [], 0
    for b in blocks:
        if used + len(b) + 1 > MAX_TOTAL:
            break
        kept.append(b)
        used += len(b) + 1
    return "\n".join(kept), len(blocks) - len(kept)


# ---------- subcommands ----------

def cmd_collect(a):
    cfg, _ = load_config()
    now = datetime.now(timezone.utc)
    if a.since:
        since = parse_ts(a.since)
        if since is None:
            print("Error: --since needs a date like 2026-01-31 or 2026-01-31T09:00, got %r" % a.since, file=sys.stderr)
            return 2
        why = "requested start"
    elif a.hours is not None:
        if not a.hours > 0:
            print("Error: --hours must be more than 0", file=sys.stderr)
            return 2
        since, why = now - timedelta(hours=a.hours), "last %gh" % a.hours
    else:
        last = load_last_sent()
        if last is not None and last <= now:
            since, why = last, "since the last update"
        else:
            hours = cfg["default_hours"]
            since, why = now - timedelta(hours=hours), "last %gh (no previous update recorded)" % hours

    root = projects_dir(a.projects_dir)
    print("# Work across all Claude Code sessions, %s (from %s)" % (why, since.astimezone().strftime("%Y-%m-%d %H:%M")))
    print("# Collected at: %s" % now.isoformat(timespec="seconds"))
    if not os.path.isdir(root):
        print("# 0 session(s)")
        print("\nNo Claude Code transcripts folder was found at %s. Use Claude Code at least once, "
              "or pass --projects-dir if yours lives somewhere else." % root)
        return 0
    sessions, unreadable = collect(since, root, cfg["exclude_folders"])
    print("# %d session(s)\n" % len(sessions))
    if unreadable:
        print("Note: %d transcript file(s) could not be opened and were skipped.\n" % unreadable)
    if not sessions:
        print("No Claude Code work found in this window. Try a wider one, for example --hours 72.")
        return 0
    text, dropped = render(sessions)
    print(text)
    if dropped:
        print("Note: the %d most recent session(s) were left out because the digest was too long. "
              "Run again with a shorter window (for example --hours 12) to see them." % dropped)
    return 0


def cmd_mark_sent(a):
    when = datetime.now(timezone.utc)
    if a.at:
        when = parse_ts(a.at)
        if when is None:
            print("Error: --at needs a timestamp like 2026-01-31T09:00:00+00:00, got %r" % a.at, file=sys.stderr)
            return 2
        when = min(when.astimezone(timezone.utc), datetime.now(timezone.utc))
    stamp = when.isoformat(timespec="seconds")
    write_json(state_path(), {"last_sent": stamp})
    print("Marked: the next update will start from %s" % stamp)
    return 0


def cmd_config(a):
    cfg, problem = load_config()
    if a.set:
        try:
            with open(config_path(), encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            raw = dict(DEFAULTS)
        except (OSError, ValueError) as e:
            print("Error: %s is broken (%s). Fix or delete it, then try again." % (config_path(), e), file=sys.stderr)
            return 2
        for item in a.set:
            key, sep, value = item.partition("=")
            key = key.strip()
            if not sep or key not in SETTABLE:
                print("Error: use --set key=value with one of: %s" % ", ".join(SETTABLE), file=sys.stderr)
                return 2
            value = value.strip()
            if key == "channel" and value not in CHANNELS:
                print("Error: channel must be one of: %s" % ", ".join(CHANNELS), file=sys.stderr)
                return 2
            raw[key] = value
        write_json(config_path(), raw)
        cfg, problem = load_config()
        print("Saved to %s" % config_path())

    if problem:
        print("Note: " + problem)
    print("Config file: %s" % config_path())
    print("Your name: %s" % (cfg["your_name"] or "(not set)"))
    print("Recipient name: %s" % (cfg["recipient_name"] or "(not set)"))
    print("Recipient contact: %s" % (cfg["recipient_contact"] or "(not set)"))
    print("Channel: %s" % cfg["channel"])
    if cfg["channel"] == "command":
        print("Send command: %s" % ("set" if cfg["send_command"] else "NOT SET, will fall back to clipboard"))
    if cfg["channel"] == "mcp":
        print("MCP tool: %s" % (cfg["mcp_tool"] or "NOT SET, will fall back to clipboard"))
        print("MCP arguments: %s" % json.dumps(cfg["mcp_arguments"], ensure_ascii=False))
    print("Excluded folders: %s" % (", ".join(cfg["exclude_folders"]) or "(none)"))
    last = load_last_sent()
    print("Last update: %s" % (last.astimezone().strftime("%Y-%m-%d %H:%M") if last else "none recorded"))
    print("Clipboard available: %s" % ("yes" if shutil.which("pbcopy") else "no"))
    return 0


def to_clipboard(text):
    exe = shutil.which("pbcopy")
    if not exe:
        return False
    try:
        env = dict(os.environ)
        env.setdefault("LANG", "en_GB.UTF-8")
        subprocess.run([exe], input=text.encode("utf-8"), env=env, check=True, timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def cmd_deliver(a):
    cfg, problem = load_config()
    if problem and not problem.startswith("no config file"):
        warn(problem)
    if a.file:
        try:
            with open(os.path.expanduser(a.file), encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            print("Error: could not read %s (%s)" % (a.file, e), file=sys.stderr)
            return 2
    else:
        if sys.stdin.isatty():
            print("Error: pass the approved update on standard input or with --file", file=sys.stderr)
            return 2
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    text = text.strip()
    if not text:
        print("Error: the update is empty, nothing delivered", file=sys.stderr)
        return 2
    if not a.approved:
        print("Refused: nothing is delivered without --approved. Show the draft, get a clear yes, "
              "then run this again with --approved.", file=sys.stderr)
        return 2

    saved = last_update_path()
    try:
        os.makedirs(data_home(), exist_ok=True)
        with open(saved, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        os.chmod(saved, 0o600)
    except OSError as e:
        warn("could not save a copy to %s (%s)" % (saved, e))
        saved = None

    channel = cfg["channel"]
    if channel == "command" and not cfg["send_command"]:
        warn("channel is 'command' but send_command is empty, using the clipboard instead")
        channel = "clipboard"

    if channel == "command":
        env = dict(os.environ)
        env["DAILY_UPDATE_RECIPIENT_NAME"] = cfg["recipient_name"]
        env["DAILY_UPDATE_RECIPIENT_CONTACT"] = cfg["recipient_contact"]
        env["DAILY_UPDATE_MESSAGE_FILE"] = saved or ""
        try:
            r = subprocess.run(cfg["send_command"], shell=True, input=text.encode("utf-8"), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=SEND_TIMEOUT)
            output = r.stdout.decode("utf-8", errors="replace").strip()
            if output:
                print("Send command output: " + clip(output, 600))
            if r.returncode == 0:
                print("RESULT: sent (send command finished with exit code 0)")
                return 0
            print("RESULT: failed (send command exit code %d). Do not retry without asking." % r.returncode)
        except subprocess.TimeoutExpired:
            print("RESULT: unclear (send command did not finish in %d seconds). It may or may not have sent. "
                  "Do not retry without asking." % SEND_TIMEOUT)
        except OSError as e:
            print("RESULT: failed (could not run the send command: %s)" % e)
        if to_clipboard(text):
            print("The update is on the clipboard in case you want to paste it by hand.")
        if saved:
            print("A copy is saved at %s" % saved)
        return 1

    # clipboard (also the safe stand-in for the mcp channel, where Claude makes the tool call itself)
    if to_clipboard(text):
        print("RESULT: copied (the update is on the clipboard, ready to paste into any chat)")
        if saved:
            print("A copy is saved at %s" % saved)
        return 0
    if saved:
        print("RESULT: saved-only (the clipboard was not available). The update is saved at %s" % saved)
        return 0
    print("RESULT: failed (no clipboard and the copy could not be saved). Copy the update from the screen.")
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="daily_update.py", description="Collect Claude Code work and deliver an approved update.")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("collect", help="print a digest of Claude Code sessions since the last update")
    c.add_argument("--hours", type=float, help="look back this many hours")
    c.add_argument("--since", help="start time, for example 2026-01-31T09:00")
    c.add_argument("--projects-dir", help="transcripts folder (default ~/.claude/projects)")

    m = sub.add_parser("mark-sent", help="record where the next update should start from")
    m.add_argument("--at", help="the 'Collected at' time printed by collect (default: now)")

    g = sub.add_parser("config", help="show or change settings")
    g.add_argument("--set", action="append", metavar="KEY=VALUE", help="save one setting; may be repeated")

    d = sub.add_parser("deliver", help="deliver an approved update read from standard input")
    d.add_argument("--approved", action="store_true", help="confirm the user approved this exact text")
    d.add_argument("--file", help="read the update from this file instead of standard input")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    return {"collect": cmd_collect, "mark-sent": cmd_mark_sent, "config": cmd_config, "deliver": cmd_deliver}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
