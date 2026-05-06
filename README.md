# HP ALM MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes **HP ALM / Quality Center** as tools for AI agents — GitHub Copilot, Claude Desktop, and any other MCP-compatible client.

It covers all day-to-day QA workflows: creating and managing test cases, pulling tests into test sets, recording execution results, filing defects, and managing requirements — all callable by an AI agent through natural language.

---

## Table of Contents

- [Features](#features)
- [Available Tools](#available-tools)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
  - [Claude Desktop](#claude-desktop)
  - [VS Code / GitHub Copilot](#vs-code--github-copilot)
  - [Standalone (stdio)](#standalone-stdio)
- [Project Structure](#project-structure)
- [Security Notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Features

| Category | Count | What you can do |
|---|---|---|
| Session | 1 | Refresh / reconnect the ALM session |
| Test Plan — Folders | 2 | Create nested folder trees in Test Plan and Test Lab |
| Test Plan — Test Cases | 5 | List, get, find, create, bulk-create, update test cases |
| Test Plan — Version Control | 3 | Check out, check in, get VC status |
| Test Plan — Design Steps | 1 | Add / replace design steps |
| Test Lab — Test Sets | 2 | Find and create test sets |
| Test Lab — Test Instances | 3 | Add tests to sets, list instances, find by name |
| Test Execution | 5 | Create runs, update run/step status, full end-to-end execute |
| Defects | 4 | List, get, create, update defects |
| Requirements | 3 | List, get, create requirements |
| Attachments | 1 | Attach any file to any ALM entity |
| Search & Discovery | 2 | Generic HPQL search, list domains/projects |
| **Total** | **32** | |

---

## Available Tools

| Tool | Description |
|------|-------------|
| `alm_refresh_session` | Refresh or reconnect the ALM session |
| `alm_ensure_test_plan_folder` | Create nested Test Plan folder path |
| `alm_ensure_test_lab_folder` | Create nested Test Lab folder path |
| `alm_list_test_cases` | List test cases in a folder |
| `alm_get_test_case` | Get full details of a test case |
| `alm_find_test_by_name` | Find a test case ID by exact name |
| `alm_create_test_case` | Create a test case with optional design steps |
| `alm_update_test_case` | Update any field(s) on a test case |
| `alm_bulk_create_test_cases` | Create many test cases in one call |
| `alm_get_test_version_status` | Get VC status (Checked_In / Checked_Out) |
| `alm_checkout_test` | Check out a test case for editing |
| `alm_checkin_test` | Check in a test case after editing |
| `alm_add_design_steps` | Add / replace design steps on a test case |
| `alm_find_test_set` | Find a test set by name |
| `alm_create_test_set` | Create a test set in a Test Lab folder |
| `alm_add_test_to_set` | Pull a test from Test Plan into a test set |
| `alm_list_test_instances` | List all instances in a test set |
| `alm_find_test_instance` | Find a test instance by test case name |
| `alm_get_test_config` | Get test configuration ID for a test case |
| `alm_create_test_run` | Create a manual test run record |
| `alm_update_run_status` | Update the pass/fail status of a run |
| `alm_get_run_steps` | Get all run steps for a run |
| `alm_update_run_step` | Update status and actual result of a run step |
| `alm_execute_test` | Full end-to-end execution in one call |
| `alm_list_defects` | List defects with optional HPQL filter |
| `alm_get_defect` | Get full details of a defect |
| `alm_create_defect` | Create a new defect |
| `alm_update_defect` | Update any field(s) on a defect |
| `alm_list_requirements` | List requirements with optional HPQL filter |
| `alm_get_requirement` | Get full details of a requirement |
| `alm_create_requirement` | Create a new requirement |
| `alm_attach_to_entity` | Attach a local file to any ALM entity |
| `alm_search` | Generic HPQL search across any entity collection |
| `alm_list_domains_projects` | Discover all accessible domains and projects |

---

## Prerequisites

- **Python 3.11+**
- An accessible **HP ALM / Quality Center** server (12.x, 15.x, 16.x tested)
- Network access from the machine running this server to your ALM instance

---

## Installation

### Option A — Install directly from GitHub (recommended for users)

```bash
pip install git+https://github.com/UditMahaldar/alm-mcp.git
```

### Option B — Clone and install locally (recommended for contributors)

```bash
git clone https://github.com/UditMahaldar/alm-mcp.git
cd alm-mcp
pip install -e ".[dev]"
```

---

## Configuration

All configuration is via environment variables or a `.env` file in the working directory.

```bash
# Copy the example and edit it
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALM_BASE_URL` | ✅ | — | Base URL of your ALM server, e.g. `https://alm.company.com/qcbin` |
| `ALM_USERNAME` | ✅ | — | ALM login username |
| `ALM_PASSWORD` | ✅ | — | ALM login password |
| `ALM_DOMAIN` | ✅ | — | ALM domain name |
| `ALM_PROJECT` | ✅ | — | ALM project name |
| `ALM_REQUEST_DELAY` | ❌ | `2.0` | Seconds between API calls — increase if ALM throttles requests |

> **Security**: Never commit your `.env` file. It is listed in `.gitignore` by default.

---

## Running the Server

### Claude Desktop

Add this block to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "hp-alm": {
      "command": "python",
      "args": ["-m", "alm_mcp.server"],
      "env": {
        "ALM_BASE_URL": "https://your-alm-server.example.com/qcbin",
        "ALM_USERNAME": "your_username",
        "ALM_PASSWORD": "your_password",
        "ALM_DOMAIN": "YOUR_DOMAIN",
        "ALM_PROJECT": "YOUR_PROJECT"
      }
    }
  }
}
```

Restart Claude Desktop. You should see **HP ALM MCP Server** in the tools list.

### VS Code / GitHub Copilot

Add this to your VS Code `settings.json` (or `.vscode/mcp.json` in the workspace):

```json
{
  "mcp": {
    "servers": {
      "hp-alm": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "alm_mcp.server"],
        "env": {
          "ALM_BASE_URL": "https://your-alm-server.example.com/qcbin",
          "ALM_USERNAME": "your_username",
          "ALM_PASSWORD": "your_password",
          "ALM_DOMAIN": "YOUR_DOMAIN",
          "ALM_PROJECT": "YOUR_PROJECT"
        }
      }
    }
  }
}
```

Alternatively, if you installed via pip and want to use the entry-point script:

```json
{
  "mcp": {
    "servers": {
      "hp-alm": {
        "type": "stdio",
        "command": "alm-mcp",
        "env": { "..." : "..." }
      }
    }
  }
}
```

### Standalone (stdio)

```bash
# With a .env file in the current directory
python -m alm_mcp.server

# Or with explicit env vars
ALM_BASE_URL=https://... ALM_USERNAME=user ALM_PASSWORD=pass \
  ALM_DOMAIN=DEFAULT ALM_PROJECT=MyProject \
  python -m alm_mcp.server
```

---

## Project Structure

```
alm-mcp/
├── src/
│   └── alm_mcp/
│       ├── __init__.py      # Package init
│       ├── config.py        # pydantic-settings configuration
│       ├── alm_client.py    # HP ALM REST API client
│       └── server.py        # MCP tool definitions (FastMCP)
├── .env.example             # Environment variable template
├── .gitignore
├── LICENSE
├── README.md
└── pyproject.toml
```

---

## Security Notes

- **SSL verification is disabled** (`verify=False`) because many enterprise ALM deployments use self-signed certificates. This is intentional and matches standard practice for on-premise ALM.
- **Credentials are never stored** in code. They are loaded exclusively from environment variables or `.env` files.
- **XML injection prevention**: all user-supplied values are escaped with `html.escape()` before being inserted into ALM XML payloads.
- **Path traversal prevention**: `alm_attach_to_entity` resolves and validates `file_path` with `os.path.realpath()` before opening the file.
- **No secrets in error messages**: authentication error messages do not include the HTTP response body, preventing credential leakage.

---

## Contributing

1. Fork the repository and create a feature branch.
2. Install dev dependencies: `pip install -e ".[dev]"`
3. Make your changes.
4. Run the test suite: `pytest`
5. Run the linter: `ruff check src/ tests/`
6. Open a pull request with a clear description.

---

## License

[MIT](LICENSE)
