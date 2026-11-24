# Daily Update: a Claude Code skill

Type `/daily-update` in Claude Code and get a short, plain English message covering
everything you got done since your last update, ready to send to your business partner,
client, manager or team.

## What it does

1. Reads every Claude Code session on your Mac since your last update. All terminals, all
   projects, not just the window you are in.
2. Groups the work by project and writes a short message in your voice: what got done,
   what is live, what you need from the other person, what is blocked or untested.
3. Shows you the draft and waits. You approve it or ask for changes.
4. Once you say yes, copies it to your clipboard so you can paste it into WhatsApp,
   Slack, iMessage, email or anything else.

It never sends or copies anything until you have seen the exact text and said yes.
Everything runs on your Mac. The helper script does not use the internet.

## What you need

- A Mac.
- Claude Code installed, and used for at least one piece of work (otherwise there is
  nothing to report yet).
- Python 3.8 or newer. Most Macs with developer tools already have it. To check, open
  Terminal and run `python3 --version`. If that fails, run `xcode-select --install`, wait
  for it to finish, and check again.

No accounts, no API keys, nothing else to install.

## Setup

1. Unzip the download. You should see `install.sh`, `README.md` and a `skill` folder.
2. Open Terminal and run the installer, replacing the path with wherever you unzipped it:

   ```bash
   bash ~/Downloads/daily-update/install.sh
   ```

3. You should see "Done. Next steps:". The installer has:
   - copied the skill to `~/.claude/skills/daily-update`
   - created your settings file at `~/.config/daily-update/config.json`

Running the installer again later is safe. It updates the skill and leaves your settings
and your update history alone.

## First run

1. Open a new Claude Code session. Skills are picked up when a session starts, so a
   window that was already open will not see it.
2. Type `/daily-update`.
3. The first time, Claude asks who the update is for and what name you go by, and saves
   both. You will not be asked again.
4. Claude may ask permission to run the helper script. Allow it.
5. Read the draft. Say "yes" to accept it, or tell Claude what to change.
6. After your yes, the message is on your clipboard. Paste it into your chat with Cmd+V.

Useful variations:

- `/daily-update last 3 days` to pick the time window yourself
- `/daily-update only the website work` to filter
- `/daily-update mention the call has moved to Friday` to add a point

With no earlier update on record it covers the last 24 hours. After that, each update
starts where the previous one stopped.

## Settings

Open `~/.config/daily-update/config.json` in any text editor, or just tell Claude "change
my daily update recipient to Sam".

| Setting | What it is | Example |
|---------|------------|---------|
| `your_name` | The name you write as | `"Jo"` |
| `recipient_name` | Who the update is for | `"Sam"` |
| `recipient_contact` | Optional. Phone number, email or handle. Only used if you set up sending | `"sam@example.com"` |
| `channel` | How the approved update is delivered: `clipboard`, `command` or `mcp` | `"clipboard"` |
| `send_command` | Only for `command`. See below | |
| `mcp_tool`, `mcp_arguments` | Only for `mcp`. See below | |
| `default_hours` | How far back to look when there is no earlier update | `24` |
| `exclude_folders` | Projects to always leave out. Any session whose folder path contains one of these is skipped | `["/private-stuff"]` |

### Optional: send it for you instead of copying

The clipboard is the default because it works for everyone. If you want the update sent
directly, there are two ways. In both cases you still see the draft and say yes first.

**A command.** Set `"channel": "command"` and put a shell command in `send_command`. The
approved text is passed to it on standard input. These variables are also available to
it: `DAILY_UPDATE_RECIPIENT_NAME`, `DAILY_UPDATE_RECIPIENT_CONTACT` and
`DAILY_UPDATE_MESSAGE_FILE` (a file holding the text). For example, to append every
update to a log file:

```json
"channel": "command",
"send_command": "cat >> ~/Documents/updates-log.txt"
```

Any command line tool that can send a message will work the same way. If the command
fails, nothing is retried and the text is put on your clipboard instead.

**A Claude Code tool.** If your Claude Code already has a connector (an MCP server) that
can send messages, set `"channel": "mcp"`, put the tool's exact name in `mcp_tool`, and
describe its inputs in `mcp_arguments`, using `{message}` for the text and `{contact}`
for the recipient contact:

```json
"channel": "mcp",
"mcp_tool": "mcp__yourserver__send_message",
"mcp_arguments": { "to": "{contact}", "text": "{message}" }
```

Tool names and inputs differ between connectors, so check yours. If the tool is not
available in a session, the update goes to your clipboard instead.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `/daily-update` is not recognised | Start a new Claude Code session. Check that `~/.claude/skills/daily-update/SKILL.md` exists. If not, run the installer again |
| "Python 3.8 or newer is needed" | Run `xcode-select --install` in Terminal, wait for it to finish, run the installer again |
| "0 sessions" or "No Claude Code work found" | There was no Claude Code activity in the window. Ask for a wider one: `/daily-update last 7 days` |
| "No Claude Code transcripts folder was found" | Claude Code has not been used on this Mac user account yet, or it keeps its data somewhere unusual. Tell Claude where, and it will pass that folder to the script |
| The update covers too much or too little | Say the window you want, for example `/daily-update since Monday 9am` |
| A project keeps showing up that should not | Add part of its folder path to `exclude_folders` |
| The update is not on the clipboard | The approved text is always saved at `~/.config/daily-update/last_update.txt`. Open that and copy it |
| "config file is broken" note | The settings file is not valid JSON. Delete `~/.config/daily-update/config.json` and run the installer again to get a fresh one |
| You want to start the history again | Delete `~/.config/daily-update/state.json`. The next update covers the last 24 hours |

To check the script on its own, run this in Terminal. It only reads, and it shows what
Claude will see:

```bash
python3 ~/.claude/skills/daily-update/scripts/daily_update.py collect --hours 24
```

## Privacy

- The script reads the Claude Code session files in `~/.claude/projects` on your own Mac.
  It does not upload them anywhere.
- Claude reads the digest in order to write your update, the same way it reads anything
  else in a session.
- The skill is told to leave passwords, keys and unrelated clients out of the update. You
  see the full text before anything happens, so read it before you say yes.

## Uninstall

```bash
rm -r ~/.claude/skills/daily-update ~/.config/daily-update
```
