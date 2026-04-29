"""
HP ALM MCP Server — Enterprise Edition
=======================================
Exposes HP ALM (Quality Center) as MCP tools for use by AI agents
(GitHub Copilot, Claude Desktop, etc.) and QA automation workflows.

34 tools across 8 categories:
  - Session          (1)
  - Test Plan        (9)  — folders, test cases, version control, design steps
  - Test Lab         (7)  — folders, test sets, test instances
  - Test Execution   (5)  — runs, steps, execute
  - Defects          (4)  — full CRUD
  - Requirements     (3)  — list, get, create
  - Attachments      (1)  — any entity
  - Search           (2)  — HPQL query, domains/projects discovery

Configuration (.env or environment variables):
  ALM_BASE_URL, ALM_USERNAME, ALM_PASSWORD, ALM_DOMAIN, ALM_PROJECT
  ALM_REQUEST_DELAY  (seconds between calls, default 2.0)

Run (stdio — works with Claude Desktop and VS Code Copilot):
  python -m alm_mcp.server
"""

import logging
import os
import platform
import socket
import threading
from typing import Optional

from mcp.server.fastmcp import FastMCP

from alm_mcp.alm_client import ALMClient, ALMError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "HP ALM MCP Server",
    instructions=(
        "Provides tools to interact with HP ALM (Quality Center). "
        "Supports Test Plan management (folders, test cases, design steps, version control), "
        "Test Lab (test sets, test instances, test runs, execution), "
        "Defect tracking (create, read, update), "
        "Requirements management, "
        "and generic HPQL search across any ALM entity."
    ),
)

_client: Optional[ALMClient] = None
_client_lock = threading.Lock()


def get_client() -> ALMClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = ALMClient()
            _client.connect()
    return _client


def _hostname() -> str:
    return platform.node() or socket.gethostname()


# ═══════════════════════════════════════════════════════════════════════
# 1. SESSION
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_refresh_session() -> dict:
    """Refresh or reconnect the ALM session.

    Use this if other tools start returning authentication or session errors.
    Tries a heartbeat first; reconnects fully if that fails.

    Returns:
        {"refreshed": true, "reconnected": true/false}
    """
    global _client
    if _client is not None:
        try:
            _client.update_session()
            return {"refreshed": True, "reconnected": False}
        except ALMError:
            pass
    _client = ALMClient()
    _client.connect()
    return {"refreshed": True, "reconnected": True}


# ═══════════════════════════════════════════════════════════════════════
# 2. TEST PLAN — FOLDERS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_ensure_test_plan_folder(folder_path: str) -> dict:
    """Ensure a Test Plan folder path exists, creating any missing folders.

    Args:
        folder_path: '/' separated path, e.g. 'AppName/Sprint1/Regression'.

    Returns:
        {"folder_id": "<id>", "folder_path": "<path>"}
    """
    client = get_client()
    folder_id = client.ensure_folder_path(folder_path)
    return {"folder_id": folder_id, "folder_path": folder_path}


@mcp.tool()
def alm_ensure_test_lab_folder(folder_path: str) -> dict:
    """Ensure a Test Lab (test-set) folder path exists, creating any missing folders.

    Args:
        folder_path: '/' separated path, e.g. 'Automation/Sprint1'.

    Returns:
        {"folder_id": "<id>", "folder_path": "<path>"}
    """
    client = get_client()
    folder_id = client.ensure_test_set_folder_path(folder_path)
    return {"folder_id": folder_id, "folder_path": folder_path}


# ═══════════════════════════════════════════════════════════════════════
# 3. TEST PLAN — TEST CASES
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_list_test_cases(folder_id: str) -> dict:
    """List all test cases inside a Test Plan folder.

    Args:
        folder_id: ALM numeric ID of the test plan folder.

    Returns:
        {"tests": [{"id", "name", "status", "owner"}, ...], "count": <n>, "folder_id": "<id>"}
    """
    client = get_client()
    tests = client.list_tests_in_folder(folder_id)
    return {"tests": tests, "count": len(tests), "folder_id": folder_id}


@mcp.tool()
def alm_get_test_case(test_id: str) -> dict:
    """Get the full details of a test case by ID, including all ALM fields.

    Args:
        test_id: ALM numeric ID of the test case.

    Returns:
        {"test_id": "<id>", "fields": {all ALM field names and values}}
    """
    client = get_client()
    details = client.get_test_case_details(test_id)
    return {"test_id": test_id, "fields": details}


@mcp.tool()
def alm_find_test_by_name(test_name: str) -> dict:
    """Look up a test case ID by its exact name.

    Args:
        test_name: Exact name of the test case in ALM.

    Returns:
        {"found": true/false, "test_id": "<id>", "test_name": "<name>"}
    """
    client = get_client()
    test_id = client.get_test_id_by_name(test_name)
    if not test_id:
        return {"found": False, "test_name": test_name}
    return {"found": True, "test_id": test_id, "test_name": test_name}


@mcp.tool()
def alm_create_test_case(
    folder_id: str,
    test_name: str,
    steps: Optional[list[dict]] = None,
) -> dict:
    """Create a manual test case in a Test Plan folder, optionally with design steps.

    Handles check-out/check-in automatically when steps are provided.

    Args:
        folder_id: ID of the parent folder.
        test_name: Name for the new test case.
        steps: Optional list of design steps. Each step is a dict with keys:
               "name" (step number/title), "description", "expected".

    Returns:
        {"test_id": "<id>", "test_name": "<name>", "folder_id": "<id>", "steps_added": <n>}
    """
    client = get_client()
    test_id = client.create_test_case(folder_id, test_name)
    if steps:
        status = client.check_test_version_status(test_id)
        if status != "Checked_Out":
            client.check_out_test(test_id)
        for step in steps:
            client.create_design_step(
                test_id,
                step.get("name", ""),
                step.get("description", ""),
                step.get("expected", ""),
            )
        status = client.check_test_version_status(test_id)
        if status != "Checked_In":
            client.check_in_test(test_id)
    return {
        "test_id": test_id,
        "test_name": test_name,
        "folder_id": folder_id,
        "steps_added": len(steps) if steps else 0,
    }


@mcp.tool()
def alm_update_test_case(
    test_id: str,
    fields: dict,
    auto_checkout: bool = True,
) -> dict:
    """Update any field(s) on an existing test case.

    Common fields: "name", "status", "description", "owner", "priority", "subtype-id".
    The test case must be checked out — set auto_checkout=true to handle this automatically.

    Args:
        test_id: ALM ID of the test case.
        fields: Dict of ALM field names → new values.
                Example: {"status": "Ready", "owner": "jsmith", "description": "Updated desc"}.
        auto_checkout: If true (default), checks out before and checks in after editing.

    Returns:
        {"test_id", "fields_updated": [...], "vc_status": "<status>"}
    """
    client = get_client()
    if auto_checkout:
        status = client.check_test_version_status(test_id)
        if status != "Checked_Out":
            client.check_out_test(test_id)
    client.update_test_case_fields(test_id, fields)
    final_status = None
    if auto_checkout:
        final_status = client.check_test_version_status(test_id)
        if final_status != "Checked_In":
            final_status = client.check_in_test(test_id)
    return {"test_id": test_id, "fields_updated": list(fields.keys()), "vc_status": final_status}


@mcp.tool()
def alm_bulk_create_test_cases(
    folder_path: str,
    test_cases: list[dict],
) -> dict:
    """Create multiple test cases with design steps under a folder path in one call.

    Creates any missing folders along the path automatically.

    Args:
        folder_path: '/' separated path e.g. 'AppName/Sprint1/Smoke'.
        test_cases: List of test case dicts:
                    [{"name": "TC-001",
                      "steps": [{"name": "Step 1",
                                 "description": "Navigate to login",
                                 "expected": "Login page is shown"}]}]

    Returns:
        {"folder_id": "<id>", "created": [...], "failed": [...]}
    """
    client = get_client()
    folder_id = client.ensure_folder_path(folder_path)
    created: list[dict] = []
    failed: list[dict] = []
    for tc in test_cases:
        try:
            test_id = client.create_test_case(folder_id, tc["name"])
            steps = tc.get("steps", [])
            if steps:
                status = client.check_test_version_status(test_id)
                if status != "Checked_Out":
                    client.check_out_test(test_id)
                for step in steps:
                    client.create_design_step(
                        test_id,
                        step.get("name", ""),
                        step.get("description", ""),
                        step.get("expected", ""),
                    )
                status = client.check_test_version_status(test_id)
                if status != "Checked_In":
                    client.check_in_test(test_id)
            created.append({"name": tc["name"], "test_id": test_id})
        except ALMError as exc:
            failed.append({"name": tc.get("name", "unknown"), "error": str(exc)})
    return {"folder_id": folder_id, "created": created, "failed": failed}


# ═══════════════════════════════════════════════════════════════════════
# 4. TEST PLAN — VERSION CONTROL
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_get_test_version_status(test_id: str) -> dict:
    """Get the version control status of a test case (Checked_In or Checked_Out).

    Args:
        test_id: ALM ID of the test case.

    Returns:
        {"test_id": "<id>", "vc_status": "Checked_In" | "Checked_Out" | null}
    """
    client = get_client()
    status = client.check_test_version_status(test_id)
    return {"test_id": test_id, "vc_status": status}


@mcp.tool()
def alm_checkout_test(test_id: str) -> dict:
    """Check out a test case so its fields and design steps can be edited.

    Args:
        test_id: ALM ID of the test case.

    Returns:
        {"test_id": "<id>", "vc_status": "<status>"}
    """
    client = get_client()
    status = client.check_out_test(test_id)
    return {"test_id": test_id, "vc_status": status}


@mcp.tool()
def alm_checkin_test(test_id: str) -> dict:
    """Check in a test case after editing to save a new version in ALM.

    Args:
        test_id: ALM ID of the test case.

    Returns:
        {"test_id": "<id>", "vc_status": "<status>"}
    """
    client = get_client()
    status = client.check_in_test(test_id)
    return {"test_id": test_id, "vc_status": status}


# ═══════════════════════════════════════════════════════════════════════
# 5. TEST PLAN — DESIGN STEPS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_add_design_steps(
    test_id: str,
    steps: list[dict],
    delete_existing: bool = False,
) -> dict:
    """Add design steps to a test case (test must already be checked out).

    Args:
        test_id: ALM ID of the test case (must be in Checked_Out state).
        steps: List of steps, each a dict with "name", "description", "expected".
               Example: [{"name": "Step 1", "description": "Open browser", "expected": "Browser opens"}]
        delete_existing: If true, deletes all current steps before adding new ones.

    Returns:
        {"test_id": "<id>", "steps_added": <n>, "delete_existing": true/false}
    """
    client = get_client()
    if delete_existing:
        client.delete_design_steps(test_id)
    for step in steps:
        client.create_design_step(
            test_id,
            step.get("name", ""),
            step.get("description", ""),
            step.get("expected", ""),
        )
    return {"test_id": test_id, "steps_added": len(steps), "delete_existing": delete_existing}


# ═══════════════════════════════════════════════════════════════════════
# 6. TEST LAB — TEST SETS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_find_test_set(test_set_name: str) -> dict:
    """Find a test set in Test Lab by its exact name and return its ID.

    Args:
        test_set_name: Exact name of the test set.

    Returns:
        {"found": true/false, "test_set_id": "<id>", "test_set_name": "<name>"}
    """
    client = get_client()
    test_set_id = client.get_test_set_id(test_set_name)
    if not test_set_id:
        return {"found": False, "test_set_name": test_set_name}
    return {"found": True, "test_set_id": test_set_id, "test_set_name": test_set_name}


@mcp.tool()
def alm_create_test_set(parent_folder_id: str, test_set_name: str) -> dict:
    """Create a new test set inside a Test Lab folder.

    Args:
        parent_folder_id: ID of the test-set folder (use alm_ensure_test_lab_folder).
        test_set_name: Name for the new test set.

    Returns:
        {"test_set_id": "<id>", "test_set_name": "<name>", "parent_folder_id": "<id>"}
    """
    client = get_client()
    test_set_id = client.create_test_set(parent_folder_id, test_set_name)
    return {"test_set_id": test_set_id, "test_set_name": test_set_name, "parent_folder_id": parent_folder_id}


# ═══════════════════════════════════════════════════════════════════════
# 7. TEST LAB — TEST INSTANCES
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_add_test_to_set(
    test_set_id: str,
    test_id: str,
    status: str = "No Run",
) -> dict:
    """Pull (add) a test case from Test Plan into a Test Lab test set.

    This is the 'Move TC from Test Plan to Test Lab' operation in ALM.

    Args:
        test_set_id: ID of the target test set.
        test_id: ID of the test case in Test Plan.
        status: Initial run status — 'No Run', 'Passed', 'Failed', 'Blocked'.

    Returns:
        {"success": true/false, "test_instance_id": "<id>", "test_set_id", "test_id"}
    """
    client = get_client()
    test_config_id = client.get_test_config_id(test_id)
    if not test_config_id:
        return {"success": False, "error": f"No test configuration found for test ID {test_id}."}
    instance_id = client.create_test_instance(test_set_id, test_id, test_config_id, status)
    return {
        "success": True,
        "test_instance_id": instance_id,
        "test_set_id": test_set_id,
        "test_id": test_id,
        "initial_status": status,
    }


@mcp.tool()
def alm_list_test_instances(test_set_id: str) -> dict:
    """List all test instances (pulled tests) inside a test set.

    Args:
        test_set_id: ID of the test set.

    Returns:
        {"instances": [{"id", "name", "status"}, ...], "count": <n>, "test_set_id": "<id>"}
    """
    client = get_client()
    instances = client.list_test_instances(test_set_id)
    return {"instances": instances, "count": len(instances), "test_set_id": test_set_id}


@mcp.tool()
def alm_find_test_instance(test_set_id: str, test_case_name: str) -> dict:
    """Find a test instance inside a test set by test case name (partial match supported).

    Args:
        test_set_id: ID of the test set.
        test_case_name: Name of the test case (exact or partial match).

    Returns:
        {"found": true/false, "test_instance_id": "<id>", "test_case_name", "test_set_id"}
    """
    client = get_client()
    instance_id = client.get_test_instance_id(test_set_id, test_case_name)
    if not instance_id:
        return {"found": False, "test_case_name": test_case_name, "test_set_id": test_set_id}
    return {"found": True, "test_instance_id": instance_id, "test_case_name": test_case_name, "test_set_id": test_set_id}


@mcp.tool()
def alm_get_test_config(test_id: str) -> dict:
    """Get the test configuration ID for a test case (required internally to create test runs).

    Args:
        test_id: ALM ID of the test case.

    Returns:
        {"found": true/false, "test_config_id": "<id>", "test_id": "<id>"}
    """
    client = get_client()
    config_id = client.get_test_config_id(test_id)
    if not config_id:
        return {"found": False, "test_id": test_id}
    return {"found": True, "test_config_id": config_id, "test_id": test_id}


# ═══════════════════════════════════════════════════════════════════════
# 8. TEST EXECUTION — RUNS & STEPS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_create_test_run(
    test_id: str,
    test_set_id: str,
    test_instance_id: str,
    test_name: str,
    status: str = "Not Completed",
    peer_reviewer: str = "",
) -> dict:
    """Create a manual test run record for an existing test instance.

    Args:
        test_id: ALM ID of the test case.
        test_set_id: ALM ID of the test set (cycle).
        test_instance_id: ALM ID of the test instance inside the test set.
        test_name: Display name/label for this run.
        status: Initial status — 'Not Completed', 'Passed', 'Failed', 'Blocked'.
        peer_reviewer: Optional ALM username to set as peer reviewer (user-template-06 field).

    Returns:
        {"success": true/false, "test_run_id": "<id>", "test_name", "status"}
    """
    client = get_client()
    test_config_id = client.get_test_config_id(test_id)
    if not test_config_id:
        return {"success": False, "error": f"No test config found for test ID {test_id}"}
    run_id = client.create_test_run(
        test_config_id, test_set_id, test_id, test_instance_id, test_name, _hostname(), status, peer_reviewer
    )
    return {"success": True, "test_run_id": run_id, "test_name": test_name, "status": status}


@mcp.tool()
def alm_update_run_status(test_run_id: str, status: str) -> dict:
    """Update the overall pass/fail status of a test run.

    Args:
        test_run_id: ALM ID of the run.
        status: 'Passed', 'Failed', 'Not Completed', 'Blocked'.

    Returns:
        {"test_run_id": "<id>", "status": "<status>", "updated": true}
    """
    client = get_client()
    client.update_run_status(test_run_id, status)
    return {"test_run_id": test_run_id, "status": status, "updated": True}


@mcp.tool()
def alm_get_run_steps(test_run_id: str) -> dict:
    """Get all run steps for a test run, sorted by step order.

    Returns step IDs and order numbers — use these to map evidence (screenshots)
    to the correct step before calling alm_update_run_step or alm_attach_to_entity.

    Args:
        test_run_id: ALM ID of the run.

    Returns:
        {"test_run_id": "<id>",
         "run_steps": [{"id", "step_order", "name"}, ...],
         "count": <n>}
    """
    client = get_client()
    steps = client.get_run_steps(test_run_id)
    return {"test_run_id": test_run_id, "run_steps": steps, "count": len(steps)}


@mcp.tool()
def alm_update_run_step(
    test_run_id: str,
    run_step_id: str,
    status: str,
    comments: str = "",
) -> dict:
    """Update the status and actual-result comment for a single run step.

    Args:
        test_run_id: ALM ID of the run.
        run_step_id: ALM ID of the run step (from alm_get_run_steps).
        status: 'Passed', 'Failed', 'Not Completed'.
        comments: Actual result / comments text to record against this step.

    Returns:
        {"test_run_id", "run_step_id", "status", "updated": true}
    """
    client = get_client()
    client.update_run_step(test_run_id, run_step_id, status, comments)
    return {"test_run_id": test_run_id, "run_step_id": run_step_id, "status": status, "updated": True}


@mcp.tool()
def alm_execute_test(
    test_set_id: str,
    test_id: str,
    test_name: str,
    status: str,
    comments: str = "",
    peer_reviewer: str = "",
) -> dict:
    """Full end-to-end test execution in a single call.

    Finds the test instance → creates a run → updates run status → updates all run steps.
    This is the primary tool for recording automation results in ALM.

    Args:
        test_set_id: ALM ID of the test set containing the test instance.
        test_id: ALM ID of the test case in Test Plan.
        test_name: Name matching the test instance in the test set.
        status: 'Passed' or 'Failed'.
        comments: Optional comment written to step actual-result field.
        peer_reviewer: Optional ALM username to set as peer reviewer (user-template-06).

    Returns:
        {"success": true/false, "test_run_id", "status", "steps_updated": <n>}
    """
    client = get_client()
    instance_id = client.get_test_instance_id(test_set_id, test_name)
    if not instance_id:
        return {
            "success": False,
            "error": (
                f"Test instance for '{test_name}' not found in test set {test_set_id}. "
                "Use alm_add_test_to_set to pull it in first."
            ),
        }
    test_config_id = client.get_test_config_id(test_id)
    if not test_config_id:
        return {"success": False, "error": f"No test config found for test ID {test_id}"}
    run_id = client.create_test_run(
        test_config_id, test_set_id, test_id, instance_id, test_name, _hostname(),
        "Not Completed", peer_reviewer,
    )
    client.update_run_status(run_id, status)
    step_comment = comments or (
        "Test passed as expected." if status == "Passed" else "Test failed — see actual result."
    )
    steps = client.get_run_steps(run_id)
    steps_updated = 0
    for step in steps:
        client.update_run_step(run_id, step["id"], status, step_comment)
        steps_updated += 1
        if status == "Failed":
            break  # Mark only first failing step (standard QC practice)
    return {
        "success": True,
        "test_run_id": run_id,
        "test_name": test_name,
        "status": status,
        "steps_updated": steps_updated,
    }


# ═══════════════════════════════════════════════════════════════════════
# 9. DEFECTS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_list_defects(
    query: str = "",
    page_size: int = 100,
) -> dict:
    """List defects from the project with optional HPQL filter.

    Args:
        query: HPQL filter string (without braces).
               Examples:
                 "status[Open]"
                 "status[Open];priority[4-Very High]"
                 "owner[jsmith]"
                 "name[*login*]"
               Leave empty to list all defects up to page_size.
        page_size: Maximum number of defects to return (default 100).

    Returns:
        {"defects": [...], "count": <n>}
    """
    client = get_client()
    defects = client.list_defects(query=query, page_size=page_size)
    return {"defects": defects, "count": len(defects)}


@mcp.tool()
def alm_get_defect(defect_id: str) -> dict:
    """Get the full details of a defect by its ID.

    Args:
        defect_id: ALM numeric ID of the defect.

    Returns:
        {"defect_id": "<id>", "fields": {all ALM field names and values}}
    """
    client = get_client()
    details = client.get_defect(defect_id)
    return {"defect_id": defect_id, "fields": details}


@mcp.tool()
def alm_create_defect(
    name: str,
    severity: str = "2-Medium",
    priority: str = "2-Medium",
    description: str = "",
    extra_fields: Optional[dict] = None,
) -> dict:
    """Create a new defect (bug) in the ALM project.

    Args:
        name: Defect summary / title (required).
        severity: '1-Low', '2-Medium', '3-High', '4-Very High'.
        priority: '1-Low', '2-Medium', '3-High', '4-Very High'.
        description: Detailed description of the defect.
        extra_fields: Any additional ALM field names → values, e.g.
                      {"owner": "jsmith", "status": "Open", "detected-in-rel": "2.1",
                       "environment": "QA", "component": "Login"}.

    Returns:
        {"success": true, "defect_id": "<id>", "name": "<name>"}
    """
    client = get_client()
    fields: dict = {
        "name": name,
        "severity": severity,
        "priority": priority,
        "status": "New",
        "detected-by": client.username,
        "owner": client.username,
    }
    if description:
        fields["description"] = description
    if extra_fields:
        fields.update(extra_fields)
    defect_id = client.create_defect(fields)
    return {"success": True, "defect_id": defect_id, "name": name}


@mcp.tool()
def alm_update_defect(
    defect_id: str,
    fields: dict,
) -> dict:
    """Update any field(s) on an existing defect.

    Common fields: "status", "priority", "severity", "owner", "description",
                   "closing-version", "fix-version", "environment", "component".

    Args:
        defect_id: ALM ID of the defect to update.
        fields: Dict of ALM field names → new values.
                Example: {"status": "Fixed", "owner": "jsmith", "closing-version": "2.1"}

    Returns:
        {"defect_id": "<id>", "fields_updated": [...], "updated": true}
    """
    client = get_client()
    client.update_defect(defect_id, fields)
    return {"defect_id": defect_id, "fields_updated": list(fields.keys()), "updated": True}


# ═══════════════════════════════════════════════════════════════════════
# 10. REQUIREMENTS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_list_requirements(
    query: str = "",
    page_size: int = 100,
) -> dict:
    """List requirements from the project with optional HPQL filter.

    Args:
        query: HPQL filter string, e.g. "status[Not Covered]", "priority[4-Very High]".
               Leave empty to list all requirements up to page_size.
        page_size: Maximum number of results (default 100).

    Returns:
        {"requirements": [...], "count": <n>}
    """
    client = get_client()
    reqs = client.list_requirements(query=query, page_size=page_size)
    return {"requirements": reqs, "count": len(reqs)}


@mcp.tool()
def alm_get_requirement(req_id: str) -> dict:
    """Get the full details of a requirement by ID.

    Args:
        req_id: ALM numeric ID of the requirement.

    Returns:
        {"req_id": "<id>", "fields": {all ALM field names and values}}
    """
    client = get_client()
    details = client.get_requirement(req_id)
    return {"req_id": req_id, "fields": details}


@mcp.tool()
def alm_create_requirement(
    name: str,
    req_type: str = "Business",
    description: str = "",
    extra_fields: Optional[dict] = None,
) -> dict:
    """Create a new requirement in the ALM project.

    Args:
        name: Requirement name / title (required).
        req_type: Requirement type, e.g. 'Business', 'Functional', 'Testing', 'Undefined'.
        description: Detailed requirement description.
        extra_fields: Optional dict of additional ALM field names → values.

    Returns:
        {"success": true, "req_id": "<id>", "name": "<name>"}
    """
    client = get_client()
    fields: dict = {"name": name, "type-id": req_type}
    if description:
        fields["description"] = description
    if extra_fields:
        fields.update(extra_fields)
    req_id = client.create_requirement(fields)
    return {"success": True, "req_id": req_id, "name": name}


# ═══════════════════════════════════════════════════════════════════════
# 11. ATTACHMENTS
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_attach_to_entity(
    entity_type: str,
    entity_id: str,
    file_path: str,
) -> dict:
    """Upload a local file as an attachment to any ALM entity.

    Supports screenshots, test reports, logs, and any other file type.

    Args:
        entity_type: ALM collection name — one of:
                     'runs'         → attach to a test run (reports, screenshots)
                     'run-steps'    → attach a screenshot/evidence to a single run step
                                      (use the run-step ID from alm_get_run_steps)
                     'defects'      → attach to a defect (screenshots, logs, evidence)
                     'tests'        → attach to a test case
                     'test-sets'    → attach to a test set
                     'requirements' → attach to a requirement
        entity_id: Numeric ALM ID of the entity.
                   For 'run-steps', this is the run-step ID (not the run ID).
        file_path: Absolute path to the file on the machine running this server.
                   Example: 'C:/reports/screenshot.png'

    Returns:
        {"success": true, "entity_type", "entity_id", "file_path"}
    """
    client = get_client()
    valid_types = {"runs", "run-steps", "defects", "tests", "test-sets", "requirements"}
    if entity_type not in valid_types:
        return {
            "success": False,
            "error": f"Invalid entity_type '{entity_type}'. Must be one of: {sorted(valid_types)}",
        }
    # Resolve and validate file path to prevent path traversal
    resolved_path = os.path.realpath(file_path)
    if not os.path.isfile(resolved_path):
        return {"success": False, "error": f"File not found or is not a regular file: {file_path}"}
    client.attach_to_entity(entity_type, entity_id, resolved_path)
    return {"success": True, "entity_type": entity_type, "entity_id": entity_id, "file_path": resolved_path}


# ═══════════════════════════════════════════════════════════════════════
# 12. SEARCH & DISCOVERY
# ═══════════════════════════════════════════════════════════════════════


@mcp.tool()
def alm_search(
    entity_type: str,
    query: str = "",
    fields: str = "id,name",
    page_size: int = 100,
) -> dict:
    """Generic HPQL search across any ALM entity collection.

    Use this for advanced queries not covered by the specific tools above.

    Args:
        entity_type: ALM entity collection — e.g. 'defects', 'tests', 'requirements',
                     'test-sets', 'test-instances', 'runs', 'test-folders'.
        query: HPQL filter string (without braces).
               Examples:
                 "status[Open];severity[4-Very High]"
                 "name[*login*]"
                 "owner[jsmith];creation-time[> '2026-01-01']"
                 "owner[u1197976];parent-id[2400]"  ← filter tests by folder + owner
               Leave empty to return all entities up to page_size.
               NOTE: For 'tests', use 'parent-id' to filter by folder — there is no
               'subject' field. Combining 'parent-id' with multiple OR values (e.g.
               parent-id[1|2|3]) may cause HTTP 500 on some ALM servers; query
               each folder separately in that case.
        fields: Comma-separated ALM field names to include in the response.
                Example: 'id,name,status,owner,creation-time'
                For 'tests': valid fields include id,name,status,owner,parent-id,
                creation-time,subtype-id,description. 'subject' is NOT a valid field.
        page_size: Maximum records to return (default 100).

    Returns:
        {"entity_type", "results": [...], "count": <n>}
    """
    client = get_client()
    results = client.search_entities(entity_type, query=query, fields=fields, page_size=page_size)
    return {"entity_type": entity_type, "results": results, "count": len(results)}


@mcp.tool()
def alm_list_domains_projects() -> dict:
    """List all ALM domains and their projects accessible to the current user.

    Use this to discover available domains and project names.

    Returns:
        {"domains": [{"domain": "<name>", "projects": ["<name>", ...]}, ...]}
    """
    client = get_client()
    domains = client.list_domains_and_projects()
    return {"domains": domains}


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
