"""
ALM REST API client — adapted from the project's HPIntegration utility.

Key differences from the original:
- Raises ALMError instead of calling sys.exit()
- Configurable request delay (default 2s) instead of hard-coded time.sleep(10)
- Returns structured dicts; JSON parsing is centralised in helper methods
- XSRF token forwarded on every mutating request
"""

import html
import logging
import time
from typing import Optional

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from alm_mcp.config import get_settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class ALMError(Exception):
    """Raised when an ALM API call fails."""


class ALMSessionExpired(ALMError):
    """Raised specifically when the ALM session has expired (HTTP 401)."""


class ALMClient:
    def __init__(self) -> None:
        cfg = get_settings()
        self.base_url: str = cfg.base_url
        self.username: str = cfg.username
        self.password: str = cfg.password
        self.domain: str = cfg.domain
        self.project: str = cfg.project
        self._delay: float = cfg.request_delay

        self.lwsso_cookie: Optional[str] = None
        self.cookie: Optional[str] = None
        self.xsrf_token: Optional[str] = None
        self._session: requests.Session = self._make_session()

    # ------------------------------------------------------------------
    # Auth / session
    # ------------------------------------------------------------------

    @staticmethod
    def _make_session() -> requests.Session:
        """Return a requests.Session with automatic retry and SSL verification disabled."""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "PUT", "POST", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.verify = False
        return session

    def connect(self) -> None:
        """Authenticate and open a session."""
        self._authenticate()
        self._create_session()

    def _authenticate(self) -> None:
        xml = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<alm-authentication>"
            f"<user>{self._xml_escape(self.username)}</user>"
            f"<password>{self._xml_escape(self.password)}</password>"
            "</alm-authentication>"
        )
        url = f"{self.base_url}/authentication-point/alm-authenticate"
        resp = self._session.post(
            url,
            headers={"Accept": "application/xml"},
            data=xml,
            auth=HTTPBasicAuth(self.username, self.password),
            verify=False,
            timeout=60,
        )
        if resp.status_code != 200:
            raise ALMError(
                f"Authentication failed: HTTP {resp.status_code}"
            )
        self.lwsso_cookie = self._trim_cookie(resp.headers.get("Set-Cookie", ""))
        logger.info("ALM authentication successful")

    def _create_session(self) -> None:
        self._sleep()
        url = f"{self.base_url}/rest/site-session"
        resp = self._session.post(
            url,
            headers={"Cookie": self.lwsso_cookie, "Accept": "application/xml"},
            auth=HTTPBasicAuth(self.username, self.password),
            verify=False,
            timeout=60,
        )
        qc_session = self._trim_cookie(resp.headers.get("Set-Cookie", ""))
        self.cookie = f"{self.lwsso_cookie};{qc_session}"

        # Extract XSRF token from cookies or headers
        for cookie in resp.cookies:
            if cookie.name == "XSRF-TOKEN":
                self.xsrf_token = cookie.value
                break
        if not self.xsrf_token:
            self.xsrf_token = resp.headers.get("X-XSRF-TOKEN")

        # Prevent session cookie accumulation — auth is managed via manual Cookie headers
        self._session.cookies.clear()

        if not self.xsrf_token:
            logger.warning("XSRF token not found — mutating requests may fail")

        logger.info("ALM session created")

    def update_session(self) -> None:
        """Heartbeat PUT to keep the session alive. Raises ALMError on auth failure."""
        url = f"{self.base_url}/rest/site-session"
        resp = self._session.put(
            url,
            headers=self._headers(),
            auth=HTTPBasicAuth(self.username, self.password),
            verify=False,
            timeout=60,
        )
        if resp.status_code == 401:
            raise ALMError(f"Session expired: HTTP 401 — {resp.text[:200]}")
        if resp.status_code not in (200, 201):
            logger.warning("Session update returned HTTP %s", resp.status_code)

    def logout(self) -> None:
        try:
            self._session.get(
                f"{self.base_url}/authentication-point/logout",
                headers=self._headers(),
                verify=False,
                timeout=30,
            )
            logger.info("ALM logout successful")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Logout error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sleep(self) -> None:
        if self._delay > 0:
            time.sleep(self._delay)

    def _auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.username, self.password)

    def _headers(
        self,
        content_type: str = "application/json",
        accept: str = "application/json",
    ) -> dict:
        h: dict = {
            "Content-Type": content_type,
            "Accept": accept,
            "Cookie": self.cookie or "",
        }
        if self.xsrf_token:
            h["X-XSRF-TOKEN"] = self.xsrf_token
        return h

    def _project_url(self) -> str:
        return (
            f"{self.base_url}/rest/domains/{self.domain}/projects/{self.project}"
        )

    @staticmethod
    def _trim_cookie(cookie_str: str) -> str:
        parts = [seg.split(";")[0] for seg in cookie_str.split(",")]
        return ";".join(parts)

    def _check(self, resp: requests.Response, operation: str) -> None:
        if resp.status_code == 401:
            raise ALMSessionExpired(
                f"{operation} failed: HTTP 401 — session expired. {resp.text[:200]}"
            )
        if resp.status_code not in (200, 201):
            raise ALMError(
                f"{operation} failed: HTTP {resp.status_code} — {resp.text[:500]}"
            )

    @staticmethod
    def _field(entity: dict, name: str) -> str:
        """Extract a named field value from an ALM entity dict."""
        for f in entity.get("Fields", []):
            if f.get("Name") == name:
                vals = f.get("values", [])
                return vals[0].get("value", "") if vals else ""
        raise ALMError(f"Field '{name}' not found in ALM response")

    @staticmethod
    def _field_safe(entity: dict, name: str) -> Optional[str]:
        try:
            return ALMClient._field(entity, name)
        except ALMError:
            return None

    @staticmethod
    def _entity_to_dict(entity: dict) -> dict:
        """Flatten all ALM entity Fields into a plain {name: value} dict."""
        result: dict = {}
        for f in entity.get("Fields", []):
            name = f.get("Name", "")
            vals = f.get("values", [])
            result[name] = vals[0].get("value") if vals else None
        return result

    @staticmethod
    def _xml_escape(value: str) -> str:
        return html.escape(str(value))

    def _build_entity_xml(self, entity_type: str, fields: dict) -> str:
        """Build an ALM XML entity payload from a field dict, safely escaping values."""
        parts = [f'<Entity Type="{entity_type}"><Fields>']
        for name, value in fields.items():
            escaped = self._xml_escape(value)
            parts.append(f'<Field Name="{name}"><Value>{escaped}</Value></Field>')
        parts.append("</Fields></Entity>")
        return "".join(parts)

    def _list_entities(
        self,
        collection: str,
        query: str = "",
        fields: str = "id,name",
        page_size: int = 100,
    ) -> list[dict]:
        """Generic paginated GET for any ALM entity collection under the project URL."""
        self._sleep()
        url = f"{self._project_url()}/{collection}?fields={fields}&page-size={page_size}"
        if query:
            url += f"&query={{{query}}}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, f"list {collection}")
        return [self._entity_to_dict(e) for e in resp.json().get("entities", [])]

    # ------------------------------------------------------------------
    # Test Plan — folders
    # ------------------------------------------------------------------

    def get_test_folder_id(self, folder_name: str) -> Optional[str]:
        self._sleep()
        url = f"{self._project_url()}/test-folders?fields=id&query={{name[{folder_name}]}}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test folder")
        data = resp.json()
        if data.get("TotalResults", 0) > 0:
            for entity in data.get("entities", []):
                return self._field(entity, "id")
        return None

    def create_test_plan_folder(self, folder_name: str, parent_id: str = "2") -> str:
        xml = (
            '<Entity Type="test-folder"><Fields>'
            f'<Field Name="parent-id"><Value>{parent_id}</Value></Field>'
            f'<Field Name="name"><Value>{self._xml_escape(folder_name)}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/test-folders"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test plan folder")
        return self._field(resp.json(), "id")

    def get_sub_folder_id(self, parent_id: str, folder_name: str) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-folders"
            f"?fields=name,id&query={{parent-id[{parent_id}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "list sub folders")
        for entity in resp.json().get("entities", []):
            if self._field_safe(entity, "name") == folder_name:
                return self._field(entity, "id")
        return None

    def ensure_folder_path(self, folder_path: str) -> str:
        """Ensure all folders in a '/' separated path exist; return leaf folder ID."""
        parts = folder_path.split("/")
        parent_id = self.get_test_folder_id(parts[0])
        if not parent_id:
            parent_id = self.create_test_plan_folder(parts[0])
        for sub in parts[1:]:
            existing = self.get_sub_folder_id(parent_id, sub)
            parent_id = existing if existing else self.create_test_plan_folder(sub, parent_id)
        return parent_id

    # ------------------------------------------------------------------
    # Test Plan — test cases
    # ------------------------------------------------------------------

    def list_tests_in_folder(self, folder_id: str) -> list[dict]:
        self._sleep()
        url = (
            f"{self._project_url()}/tests"
            f"?fields=id,name,status,owner&query={{parent-id[{folder_id}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "list tests")
        return [
            {
                "id": self._field(e, "id"),
                "name": self._field_safe(e, "name"),
                "status": self._field_safe(e, "status"),
                "owner": self._field_safe(e, "owner"),
            }
            for e in resp.json().get("entities", [])
        ]

    def get_test_id_by_name(self, test_name: str) -> Optional[str]:
        self._sleep()
        url = f"{self._project_url()}/tests?fields=id&query={{name[{test_name}]}}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test id by name")
        data = resp.json()
        if data.get("TotalResults", 0) > 0:
            return self._field(data["entities"][0], "id")
        return None

    def create_test_case(self, folder_id: str, test_name: str) -> str:
        xml = (
            "<Entity><Fields>"
            f'<Field Name="name"><Value>{self._xml_escape(test_name)}</Value></Field>'
            '<Field Name="subtype-id"><Value>MANUAL</Value></Field>'
            '<Field Name="status"><Value>Ready for Peer Review</Value></Field>'
            f'<Field Name="parent-id"><Value>{folder_id}</Value></Field>'
            f'<Field Name="owner"><Value>{self._xml_escape(self.username)}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/tests"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test case")
        test_id = self._field(resp.json(), "id")
        logger.info("Created test case '%s' with ID %s", test_name, test_id)
        return test_id

    # ------------------------------------------------------------------
    # Test Plan — version control
    # ------------------------------------------------------------------

    def check_test_version_status(self, test_id: str) -> Optional[str]:
        self._sleep()
        url = f"{self._project_url()}/tests/{test_id}/?fields=vc-status"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "check test version status")
        for field in resp.json().get("Fields", []):
            if field.get("Name") == "vc-status":
                vals = field.get("values", [])
                return vals[0].get("value") if vals else None
        return None

    def check_out_test(self, test_id: str) -> Optional[str]:
        xml = (
            "<CheckOutParameters>"
            "<Comment>Checked out via ALM MCP Server</Comment>"
            "</CheckOutParameters>"
        )
        self._sleep()
        url = f"{self._project_url()}/tests/{test_id}/versions/check-out"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml", "application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "check out test")
        return self.check_test_version_status(test_id)

    def check_in_test(self, test_id: str) -> Optional[str]:
        xml = (
            "<CheckInParameters>"
            "<Comment>Checked in via ALM MCP Server</Comment>"
            "</CheckInParameters>"
        )
        self._sleep()
        url = f"{self._project_url()}/tests/{test_id}/versions/check-in"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml", "application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "check in test")
        return self.check_test_version_status(test_id)

    # ------------------------------------------------------------------
    # Test Plan — design steps
    # ------------------------------------------------------------------

    def create_design_step(
        self,
        test_id: str,
        step_name: str,
        description: str,
        expected: str,
    ) -> None:
        xml = (
            '<Entity Type="design-step"><Fields>'
            f'<Field Name="expected"><Value>{self._xml_escape(expected)}</Value></Field>'
            f'<Field Name="name"><Value>{self._xml_escape(step_name)}</Value></Field>'
            f'<Field Name="description"><Value>{self._xml_escape(description)}</Value></Field>'
            f'<Field Name="parent-id"><Value>{test_id}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/design-steps"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml", "application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create design step")

    def delete_design_steps(self, test_id: str) -> None:
        self._sleep()
        check_url = (
            f"{self._project_url()}/design-steps?query={{parent-id[{test_id}]}}"
        )
        resp = self._session.get(
            check_url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60
        )
        self._check(resp, "check design steps")
        if resp.json().get("TotalResults", 0) > 0:
            del_url = (
                f"{self._project_url()}/design-steps?query={{parent-id[{test_id}]}}"
            )
            resp = self._session.delete(
                del_url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60
            )
            self._check(resp, "delete design steps")

    # ------------------------------------------------------------------
    # Test Lab — test-set folders
    # ------------------------------------------------------------------

    def get_test_set_folder_id(self, folder_name: str) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-set-folders"
            f"?fields=id&query={{name[{folder_name}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test set folder")
        data = resp.json()
        if data.get("TotalResults", 0) > 0:
            return self._field(data["entities"][0], "id")
        return None

    def _get_test_set_sub_folder_id(
        self, parent_id: str, folder_name: str
    ) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-set-folders"
            f"?fields=name,id&query={{parent-id[{parent_id}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "list test-set sub folders")
        for entity in resp.json().get("entities", []):
            if self._field_safe(entity, "name") == folder_name:
                return self._field(entity, "id")
        return None

    def create_test_set_folder(self, folder_name: str, parent_id: str) -> str:
        xml = (
            '<Entity Type="test-set-folder"><Fields>'
            f'<Field Name="parent-id"><Value>{parent_id}</Value></Field>'
            f'<Field Name="name"><Value>{self._xml_escape(folder_name)}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/test-set-folders"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test set folder")
        return self._field(resp.json(), "id")

    def ensure_test_set_folder_path(self, folder_path: str) -> str:
        """Ensure all test-lab folders in a '/' path exist; return leaf folder ID."""
        parts = folder_path.split("/")
        parent_id = self.get_test_set_folder_id(parts[0])
        if not parent_id:
            parent_id = self.create_test_set_folder(parts[0], "0")
        for sub in parts[1:]:
            existing = self._get_test_set_sub_folder_id(parent_id, sub)
            parent_id = (
                existing if existing else self.create_test_set_folder(sub, parent_id)
            )
        return parent_id

    # ------------------------------------------------------------------
    # Test Lab — test sets
    # ------------------------------------------------------------------

    def get_test_set_id(self, test_set_name: str) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-sets"
            f"?fields=id&query={{name[{test_set_name}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test set id")
        data = resp.json()
        if data.get("TotalResults", 0) > 0:
            return self._field(data["entities"][0], "id")
        return None

    def create_test_set(self, parent_folder_id: str, test_set_name: str) -> str:
        xml = (
            "<Entity><Fields>"
            f'<Field Name="parent-id"><Value>{parent_folder_id}</Value></Field>'
            f'<Field Name="name"><Value>{self._xml_escape(test_set_name)}</Value></Field>'
            '<Field Name="subtype-id"><Value>hp.qc.test-set.default</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/test-sets"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test set")
        test_set_id = self._field(resp.json(), "id")
        logger.info("Created test set '%s' with ID %s", test_set_name, test_set_id)
        return test_set_id

    # ------------------------------------------------------------------
    # Test Lab — test instances (pulling tests into a test set)
    # ------------------------------------------------------------------

    def get_test_config_id(self, test_id: str) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-configs"
            f"?fields=id&query={{parent-id[{test_id}]}}"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test config id")
        data = resp.json()
        if data.get("TotalResults", 0) > 0:
            return self._field(data["entities"][0], "id")
        return None

    def create_test_instance(
        self,
        test_set_id: str,
        test_id: str,
        test_config_id: str,
        status: str = "No Run",
    ) -> str:
        xml = (
            "<Entity><Fields>"
            f'<Field Name="cycle-id"><Value>{test_set_id}</Value></Field>'
            f'<Field Name="test-config-id"><Value>{test_config_id}</Value></Field>'
            f'<Field Name="test-id"><Value>{test_id}</Value></Field>'
            f'<Field Name="owner"><Value>{self._xml_escape(self.username)}</Value></Field>'
            f'<Field Name="status"><Value>{self._xml_escape(status)}</Value></Field>'
            '<Field Name="subtype-id"><Value>hp.qc.test-instance.manual</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/test-instances"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test instance")
        return self._field(resp.json(), "id")

    def get_test_instance_id(
        self, test_set_id: str, test_case_name: str
    ) -> Optional[str]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-instances"
            f"?fields=id,name&query={{cycle-id[{test_set_id}]}}&page-size=5000"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test instance id")
        for entity in resp.json().get("entities", []):
            raw_name = self._field_safe(entity, "name") or ""
            # ALM may format name as "TestName [1]", match on base part
            base_name = raw_name.split("[")[0].strip()
            if test_case_name in base_name:
                return self._field(entity, "id")
        return None

    def list_test_instances(self, test_set_id: str) -> list[dict]:
        self._sleep()
        url = (
            f"{self._project_url()}/test-instances"
            f"?fields=id,name,status&query={{cycle-id[{test_set_id}]}}&page-size=5000"
        )
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "list test instances")
        return [
            {
                "id": self._field(e, "id"),
                "name": self._field_safe(e, "name"),
                "status": self._field_safe(e, "status"),
            }
            for e in resp.json().get("entities", [])
        ]

    # ------------------------------------------------------------------
    # Test Lab — runs
    # ------------------------------------------------------------------

    def create_test_run(
        self,
        test_config_id: str,
        test_set_id: str,
        test_id: str,
        test_instance_id: str,
        test_name: str,
        host: str,
        status: str = "Not Completed",
        peer_reviewer: str = "",
    ) -> str:
        peer_reviewer_xml = (
            f'<Field Name="user-template-06"><Value>{self._xml_escape(peer_reviewer)}</Value></Field>'
            if peer_reviewer else ""
        )
        xml = (
            '<Entity Type="run"><Fields>'
            f'<Field Name="test-config-id"><Value>{test_config_id}</Value></Field>'
            f'<Field Name="cycle-id"><Value>{test_set_id}</Value></Field>'
            f'<Field Name="test-id"><Value>{test_id}</Value></Field>'
            f'<Field Name="testcycl-id"><Value>{test_instance_id}</Value></Field>'
            f'<Field Name="name"><Value>{self._xml_escape(test_name)}</Value></Field>'
            f'<Field Name="host"><Value>{self._xml_escape(host)}</Value></Field>'
            f'<Field Name="owner"><Value>{self._xml_escape(self.username)}</Value></Field>'
            '<Field Name="subtype-id"><Value>hp.qc.run.MANUAL</Value></Field>'
            '<Field Name="duration"><Value>0</Value></Field>'
            f'<Field Name="status"><Value>{self._xml_escape(status)}</Value></Field>'
            f'{peer_reviewer_xml}'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/runs"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create test run")
        run_id = self._field(resp.json(), "id")
        logger.info("Created test run ID %s for '%s' with status '%s'", run_id, test_name, status)
        return run_id

    def update_run_status(self, test_run_id: str, status: str) -> None:
        xml = (
            '<Entity Type="run"><Fields>'
            f'<Field Name="owner"><Value>{self._xml_escape(self.username)}</Value></Field>'
            '<Field Name="subtype-id"><Value>hp.qc.run.MANUAL</Value></Field>'
            f'<Field Name="status"><Value>{self._xml_escape(status)}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/runs/{test_run_id}"
        resp = self._session.put(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "update run status")

    def get_run_steps(self, test_run_id: str) -> list[dict]:
        """Return run steps sorted by step-order, each with 'id', 'step_order', 'name'."""
        self._sleep()
        url = f"{self._project_url()}/runs/{test_run_id}/run-steps?fields=id,step-order,name&page-size=500"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get run steps")
        steps = [
            {
                "id": self._field(e, "id"),
                "step_order": int(self._field_safe(e, "step-order") or 0),
                "name": self._field_safe(e, "name") or "",
            }
            for e in resp.json().get("entities", [])
        ]
        return sorted(steps, key=lambda s: s["step_order"])

    def update_run_step(
        self,
        test_run_id: str,
        run_step_id: str,
        status: str,
        comments: str,
    ) -> None:
        xml = (
            '<Entity Type="run-step"><Fields>'
            f'<Field Name="status"><Value>{self._xml_escape(status)}</Value></Field>'
            f'<Field Name="actual"><Value>{self._xml_escape(comments)}</Value></Field>'
            "</Fields></Entity>"
        )
        self._sleep()
        url = f"{self._project_url()}/runs/{test_run_id}/run-steps/{run_step_id}"
        resp = self._session.put(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "update run step")

    # ------------------------------------------------------------------
    # Test Case — details & field update
    # ------------------------------------------------------------------

    def get_test_case_details(self, test_id: str) -> dict:
        self._sleep()
        url = f"{self._project_url()}/tests/{test_id}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get test case details")
        return self._entity_to_dict(resp.json())

    def update_test_case_fields(self, test_id: str, fields: dict) -> None:
        """Update arbitrary fields on a test case (must be checked out first)."""
        xml = self._build_entity_xml("test", fields)
        self._sleep()
        url = f"{self._project_url()}/tests/{test_id}"
        resp = self._session.put(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "update test case fields")

    # ------------------------------------------------------------------
    # Defects
    # ------------------------------------------------------------------

    def list_defects(
        self,
        query: str = "",
        fields: str = "id,name,status,severity,priority,owner,detected-by,creation-time,description",
        page_size: int = 100,
    ) -> list[dict]:
        """List defects. query uses ALM HPQL e.g. 'status[Open];priority[4-Very High]'."""
        return self._list_entities("defects", query=query, fields=fields, page_size=page_size)

    def get_defect(self, defect_id: str) -> dict:
        self._sleep()
        url = f"{self._project_url()}/defects/{defect_id}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get defect")
        return self._entity_to_dict(resp.json())

    def create_defect(self, fields: dict) -> str:
        """Create a defect. Provide fields dict e.g. {'name': '...', 'severity': '2-Medium', 'status': 'New'}."""
        # Ensure mandatory detected-by is set
        if "detected-by" not in fields:
            fields = {**fields, "detected-by": self.username}
        xml = self._build_entity_xml("defect", fields)
        self._sleep()
        url = f"{self._project_url()}/defects"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create defect")
        defect_id = self._field(resp.json(), "id")
        logger.info("Created defect ID %s", defect_id)
        return defect_id

    def update_defect(self, defect_id: str, fields: dict) -> None:
        """Update any writable fields on an existing defect."""
        xml = self._build_entity_xml("defect", fields)
        self._sleep()
        url = f"{self._project_url()}/defects/{defect_id}"
        resp = self._session.put(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "update defect")
        logger.info("Updated defect ID %s — fields: %s", defect_id, list(fields))

    # ------------------------------------------------------------------
    # Requirements
    # ------------------------------------------------------------------

    def list_requirements(
        self,
        query: str = "",
        fields: str = "id,name,status,type-id,owner,priority",
        page_size: int = 100,
    ) -> list[dict]:
        """List requirements. query uses ALM HPQL."""
        return self._list_entities("requirements", query=query, fields=fields, page_size=page_size)

    def get_requirement(self, req_id: str) -> dict:
        self._sleep()
        url = f"{self._project_url()}/requirements/{req_id}"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "get requirement")
        return self._entity_to_dict(resp.json())

    def create_requirement(self, fields: dict) -> str:
        """Create a requirement. Provide fields dict e.g. {'name': '...', 'type-id': '...'}."""
        xml = self._build_entity_xml("requirement", fields)
        self._sleep()
        url = f"{self._project_url()}/requirements"
        resp = self._session.post(
            url,
            headers=self._headers("application/xml"),
            data=xml,
            auth=self._auth(),
            verify=False,
            timeout=60,
        )
        self._check(resp, "create requirement")
        req_id = self._field(resp.json(), "id")
        logger.info("Created requirement ID %s", req_id)
        return req_id

    # ------------------------------------------------------------------
    # Generic search (any entity type with HPQL)
    # ------------------------------------------------------------------

    def search_entities(
        self,
        entity_type: str,
        query: str = "",
        fields: str = "id,name",
        page_size: int = 100,
    ) -> list[dict]:
        """Generic HPQL search across any ALM entity collection.

        entity_type examples: 'defects', 'tests', 'requirements',
                               'test-sets', 'test-instances', 'runs'
        query example: 'status[Open];priority[4-Very High]'
        """
        return self._list_entities(entity_type, query=query, fields=fields, page_size=page_size)

    # ------------------------------------------------------------------
    # Domains & Projects discovery
    # ------------------------------------------------------------------

    def list_domains_and_projects(self) -> list[dict]:
        """Return all domains and their projects the current user can access."""
        self._sleep()
        url = f"{self.base_url}/rest/domains"
        resp = self._session.get(url, headers=self._headers(), auth=self._auth(), verify=False, timeout=60)
        self._check(resp, "list domains")
        data = resp.json()
        # ALM REST API returns different JSON key casing depending on server version:
        # { "Domain": [...] }  or  { "domains": { "domain": [...] } }
        domain_list: list = []
        if isinstance(data.get("Domain"), list):
            domain_list = data["Domain"]
        elif isinstance(data.get("domains"), list):
            domain_list = data["domains"]
        elif isinstance(data.get("domains"), dict):
            domain_list = data["domains"].get("domain", data["domains"].get("Domain", []))
        results: list[dict] = []
        for domain in domain_list:
            domain_name = domain.get("Name", "") or domain.get("name", "")
            # projects may be nested as {"Project": [...]} or {"project": [...]}
            proj_wrapper = domain.get("Projects") or domain.get("projects") or {}
            if isinstance(proj_wrapper, list):
                proj_list = proj_wrapper
            elif isinstance(proj_wrapper, dict):
                proj_list = proj_wrapper.get("Project") or proj_wrapper.get("project") or []
            else:
                proj_list = []
            projects = [p.get("Name", "") or p.get("name", "") for p in proj_list]
            results.append({"domain": domain_name, "projects": projects})
        return results

    # ------------------------------------------------------------------
    # Attachments — generic (runs, defects, tests, test-sets …)
    # ------------------------------------------------------------------

    def attach_to_entity(
        self,
        entity_collection: str,
        entity_id: str,
        file_path: str,
    ) -> None:
        """Upload a file attachment to any ALM entity.

        entity_collection: 'runs', 'defects', 'tests', 'test-sets', 'requirements'
        entity_id: numeric ID of the entity
        file_path: absolute local path to the file

        Automatically reconnects once if the session has expired (HTTP 401).
        """
        filename = file_path.replace("\\", "/").split("/")[-1]
        url = f"{self._project_url()}/{entity_collection}/{entity_id}/attachments"

        def _do_upload() -> requests.Response:
            with open(file_path, "rb") as fh:
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Accept": "application/json",
                    "Cookie": self.cookie or "",
                    "slug": filename,
                }
                if self.xsrf_token:
                    headers["X-XSRF-TOKEN"] = self.xsrf_token
                self._sleep()
                return self._session.post(
                    url, headers=headers, data=fh, auth=self._auth(), verify=False, timeout=120
                )

        resp = _do_upload()
        if resp.status_code == 401:
            logger.warning(
                "Session expired during attach — reconnecting and retrying once."
            )
            self.connect()
            resp = _do_upload()

        self._check(resp, f"attach to {entity_collection}/{entity_id}")
        logger.info("Attached '%s' to %s/%s", filename, entity_collection, entity_id)

    # Keep backward-compat alias used by old tests
    def upload_attachment(self, run_id: str, file_path: str) -> None:
        self.attach_to_entity("runs", run_id, file_path)

