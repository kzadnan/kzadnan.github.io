# hpc-login

`hpc-login` keeps one OpenSSH connection to an HPC login node alive on your machine and lets Cursor, Claude, and Hermes use it.

The service does not implement SSH. It runs the OpenSSH client you already have, with a multiplexed control socket (`ControlMaster`, `ControlPersist`) and client keepalives (`ServerAliveInterval`, `ServerAliveCountMax`). When that master exits, a local supervisor starts it again. Cursor, Claude Desktop, Claude Code, and the Hermes agent talk to a small MCP server over stdio. A status page on `127.0.0.1` shows whether the session is up.

Private keys and passwords are never written by this program. Authentication stays in your `ssh-agent`, your key files, and `~/.ssh/config`.

## What you need

- macOS or Linux
- Python 3.11 or newer
- OpenSSH client (`ssh` on `PATH`)

## Install

From this directory:

```bash
chmod +x bin/hpc-login
./bin/hpc-login --help
```

No virtualenv is required. The launcher adds this directory to `PYTHONPATH` and runs `python3 -m hpc_login`. Optional, if you want a console script on `PATH`:

```bash
python3 -m pip install -e .
```

## Configure

Copy the example and edit the copy. The copy is gitignored.

```bash
cp config.example.toml config.toml
```

You can instead keep it at `~/.config/hpc-login/config.toml`. The first file that exists is used, unless `HPC_LOGIN_CONFIG` points somewhere else:

1. `HPC_LOGIN_CONFIG`
2. `$XDG_CONFIG_HOME/hpc-login/config.toml`
3. `~/.config/hpc-login/config.toml`
4. `hpc-login/config.toml` next to this README

Set at least the login host. `user`, `port`, `identity_file`, and `proxy_jump` are optional when `~/.ssh/config` already has them. Environment variables override the file.

| Setting | Environment variable | Role |
| --- | --- | --- |
| `host` | `HPC_LOGIN_HOST` | Login hostname, or a `Host` alias from `~/.ssh/config` |
| `user` | `HPC_LOGIN_USER` | Remote user. Omit to let `~/.ssh/config` choose |
| `port` | `HPC_LOGIN_PORT` | Remote port. Omit to let `~/.ssh/config` choose |
| `identity_file` | `HPC_LOGIN_IDENTITY_FILE` | Path to a private key. The file is not copied |
| `proxy_jump` | `HPC_LOGIN_PROXY_JUMP` | OpenSSH `ProxyJump` value, one hop or a comma-separated chain |

Leave `identity_file` unset to use `ssh-agent` and whatever `IdentityFile` your SSH config already names. This program never asks for a password. `BatchMode=yes` is always set, so a missing key or an unknown host key fails instead of prompting.

Before the first run, accept the login host key (and the jump host, if you use one) with a normal interactive SSH session. That stores the key in `~/.ssh/known_hosts`.

```bash
ssh your-username@hpc.example.edu
```

The status page binds to `127.0.0.1` only. `0.0.0.0` and other addresses are rejected.

The control socket and status file live under `${XDG_STATE_HOME:-~/.local/state}/hpc-login/`, outside the repository. A very long home path falls back to `/tmp/hpc-login-<uid>/cm.sock` because Unix socket paths are short.

## Run the persistent connection

```bash
./bin/hpc-login serve
```

Leave that process running. It holds `ssh -N` as the control master and restarts it if it dies. The status page is printed on stderr, by default:

```text
http://127.0.0.1:8765/
```

Useful checks from another terminal:

```bash
./bin/hpc-login status
./bin/hpc-login reconnect
```

`status` exits 0 only when the master is connected.

When Cursor, Claude, or Hermes starts the MCP server, that process starts `hpc-login serve` in the background if it is not already running. One supervisor holds the lock, so the three clients share a single SSH master. Foreground `serve` is still the easiest way to watch the log. Background logs go to `serve.log` in the state directory.

Stop the foreground supervisor with Ctrl-C. It closes the master on the way out.

## How keepalive and reconnect work

The master is started with:

- `ControlMaster=yes` and a fixed `ControlPath`, so later `ssh` commands join the same TCP session
- `ControlPersist=yes`, so the multiplexed session is allowed to outlive an individual command
- `ServerAliveInterval` (default 30 seconds) and `ServerAliveCountMax` (default 3)

OpenSSH sends a keepalive every 30 seconds. After three missed replies it closes the connection, which is about 90 seconds of a dead path. The supervisor then starts a new master.

Reconnect policy:

- A live master is left alone.
- A dropped master is replaced immediately on the first failure.
- Further failures wait 1s, 2s, 4s, … capped at 60s, and then try again. The supervisor does not give up.
- `reconnect` from the status page, the CLI, or the MCP tool skips the wait.
- Commands refuse to open a second, unsupervised SSH session when the master is down. `ControlMaster=no` would otherwise fall back to dialing the cluster directly.

Command output is capped (`output_limit_bytes`, default 200000 bytes per stream) and each command has a wall-clock limit (`command_timeout`, default 120 seconds, and a tool call cannot raise it). File copies stop at `transfer_limit_bytes` (default 50 MB). Killing the local `ssh` client ends the remote command in the usual way (the remote shell gets SIGHUP). A remote process that ignores hangup can keep running on the cluster.

## MCP tools

| Tool | What it does |
| --- | --- |
| `connection_status` | Supervisor state, and whether the control master answers |
| `reconnect` | Ask the supervisor to drop the master and connect again |
| `run_command` | One remote shell command, with `timeout_seconds` and optional `working_directory` |
| `upload_file` | Copy a local file to a remote path |
| `download_file` | Copy a remote file to a local path |
| `list_directory` | `ls -la` on one remote directory |

Call `connection_status` before a command. If `master` is `down`, call `reconnect`, then check status again. The HTTP page can show status and trigger reconnect. It cannot run remote commands.

## Register the MCP server

Replace `/ABSOLUTE/PATH/TO/hpc-login` with the directory that contains `bin/hpc-login`. Start `./bin/hpc-login serve` at least once so you can see SSH errors, then restart the client after editing its config.

These shapes were checked against the vendor docs on 24 September 2026. Links are in [Sources](#sources).

### Cursor

Project config is `.cursor/mcp.json` in the project root. Global config is `~/.cursor/mcp.json`. Both use an `mcpServers` object. Cursor's stdio field table lists `type`, `command`, `args`, and optional `env` / `envFile`. `${workspaceFolder}` expands to the project that contains `.cursor/mcp.json`.

Project snippet, also saved at `examples/cursor.mcp.json`:

```json
{
  "mcpServers": {
    "hpc-login": {
      "type": "stdio",
      "command": "${workspaceFolder}/hpc-login/bin/hpc-login",
      "args": ["mcp"]
    }
  }
}
```

Global snippet:

```json
{
  "mcpServers": {
    "hpc-login": {
      "type": "stdio",
      "command": "/ABSOLUTE/PATH/TO/hpc-login/bin/hpc-login",
      "args": ["mcp"]
    }
  }
}
```

Reload Cursor after saving. MCP logs are in the Output panel under "MCP Logs".

### Claude Desktop

Claude Desktop reads `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

The current Desktop setup guide does not list a Linux path, and the server-building guide says Claude Desktop is not available on Linux. On Linux, use Claude Code below.

The file uses `mcpServers` with `command` and `args`. Paths must be absolute. Snippet, also at `examples/claude-desktop.mcp.json`:

```json
{
  "mcpServers": {
    "hpc-login": {
      "command": "/ABSOLUTE/PATH/TO/hpc-login/bin/hpc-login",
      "args": ["mcp"]
    }
  }
}
```

Quit and reopen Claude Desktop after saving.

### Claude Code

Claude Code does not read `claude_desktop_config.json`. User scope is stored in `~/.claude.json`. Project scope is `.mcp.json` at the project root (you approve it once inside Claude Code). A stdio entry is `command` plus `args`. An entry that has `url` and no `type` is ignored; this server has no URL.

Add it for your user:

```bash
claude mcp add --scope user --transport stdio hpc-login -- /ABSOLUTE/PATH/TO/hpc-login/bin/hpc-login mcp
```

Or write `.mcp.json` (see `examples/claude-code.mcp.json`). In that file, `${CLAUDE_PROJECT_DIR}` needs a default when you expand it yourself:

```json
{
  "mcpServers": {
    "hpc-login": {
      "command": "${CLAUDE_PROJECT_DIR:-.}/hpc-login/bin/hpc-login",
      "args": ["mcp"]
    }
  }
}
```

Check with `claude mcp list` and `/mcp` inside a session.

### Hermes

Hermes Agent reads `~/.hermes/config.yaml`. MCP servers live under `mcp_servers` (a YAML map, not the `mcpServers` JSON key). A stdio server has `command` and `args`. `timeout` and `connect_timeout` are seconds. `${userHome}` and `${workspaceFolder}` are expanded, and `${env:VAR}` works the same as in Cursor.

Add this block, also saved at `examples/hermes.config.yaml`:

```yaml
mcp_servers:
  hpc-login:
    command: "/ABSOLUTE/PATH/TO/hpc-login/bin/hpc-login"
    args: ["mcp"]
    timeout: 180
    connect_timeout: 30
```

Restart Hermes, or run its MCP reload, after editing the file. `hermes mcp add` can write the same entry: `command` is the launcher path and the remaining argument is `mcp`.

## Tests

The tests do not contact a cluster. They cover config parsing, reconnect backoff, output limits, SSH argument assembly, the loopback status server, and the MCP handshake.

```bash
python3 -m unittest discover -s tests -v
```

## What you still fill in

In `config.toml` or the environment:

- `host` — login node, or the `Host` alias you already use
- `user` — cluster username, if `~/.ssh/config` does not set it
- `identity_file` — only if you are not using `ssh-agent` or an `IdentityFile` in `~/.ssh/config`
- `proxy_jump` — only if you reach the cluster through a bastion and `~/.ssh/config` does not already set `ProxyJump`

Nothing else from your account belongs in this repository.

## Sources

Checked 24 September 2026:

- Cursor MCP config: [cursor.com/docs/mcp](https://cursor.com/docs/mcp). Project file `.cursor/mcp.json`, global file `~/.cursor/mcp.json`, `mcpServers`, stdio `type` / `command` / `args`, and `${workspaceFolder}` / `${env:NAME}` interpolation.
- Claude Desktop local servers: [modelcontextprotocol.io connect-local-servers](https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers). macOS and Windows paths for `claude_desktop_config.json`, `mcpServers` with `command` and `args`.
- Claude Desktop on Linux: [modelcontextprotocol.io build-server](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server) states that Claude Desktop is not available on Linux.
- Claude Code: [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp). `.mcp.json` for project scope, `~/.claude.json` for user scope, `claude mcp add --transport stdio`, and `${CLAUDE_PROJECT_DIR:-.}` in project files.
- Hermes Agent: [MCP guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) and [MCP config reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference). `~/.hermes/config.yaml`, key `mcp_servers`, stdio `command` / `args` / `timeout` / `connect_timeout`.
