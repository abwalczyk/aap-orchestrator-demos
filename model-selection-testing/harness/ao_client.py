"""AO API client for triggering workflow tests and retrieving results."""

import copy
import time

import httpx


TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed", "cancelled"}


class AOClient:
    """Client for the Automation Orchestrator REST API."""

    def __init__(self, base_url: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self.client = httpx.Client(base_url=self.base_url, verify=verify_ssl, timeout=120)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make an HTTP request with automatic token refresh on 401."""
        resp = self.client.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401 and self._username and self._password:
            self.login(self._username, self._password)
            resp = self.client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    def login(self, username: str, password: str) -> str:
        """Authenticate and store JWT token."""
        self._username = username
        self._password = password
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data["access_token"]
        return self.token

    def get_workflow(self, workflow_id: str) -> dict:
        """Fetch a workflow definition by ID."""
        resp = self._request("GET", f"/api/v1/workflows/{workflow_id}")
        return resp.json()

    def swap_model(self, workflow_id: str, node_id: str, model_name: str) -> dict:
        """Update a specific agentic node's model in the workflow definition.

        Returns the original workflow definition for restoration.
        """
        workflow = self.get_workflow(workflow_id)
        original_definition = copy.deepcopy(workflow["version"]["workflow_definition"])

        definition = workflow["version"]["workflow_definition"]
        nodes = definition.get("nodes", [])
        node_found = False
        for node in nodes:
            if node.get("id") == node_id:
                if "config" not in node:
                    node["config"] = {}
                node["config"]["model"] = model_name
                node_found = True
                break

        if not node_found:
            raise ValueError(f"Node '{node_id}' not found in workflow '{workflow_id}'")

        self._request("PATCH", f"/api/v1/workflows/{workflow_id}",
                       json={"workflow_definition": definition})
        return original_definition

    def swap_prompt(self, workflow_id: str, node_id: str, prompt_text: str) -> dict:
        """Update a node's system prompt. Returns the original definition."""
        workflow = self.get_workflow(workflow_id)
        original_definition = copy.deepcopy(workflow["version"]["workflow_definition"])

        definition = workflow["version"]["workflow_definition"]
        for node in definition.get("nodes", []):
            if node.get("id") == node_id:
                if "config" not in node:
                    node["config"] = {}
                node["config"]["system_prompt"] = prompt_text
                break
        else:
            raise ValueError(f"Node '{node_id}' not found in workflow '{workflow_id}'")

        self._request("PATCH", f"/api/v1/workflows/{workflow_id}",
                       json={"workflow_definition": definition})
        return original_definition

    def restore_workflow(self, workflow_id: str, original_definition: dict) -> None:
        """Restore a workflow definition to its original state after testing."""
        self._request("PATCH", f"/api/v1/workflows/{workflow_id}",
                       json={"workflow_definition": original_definition})

    def test_node(
        self,
        workflow_id: str,
        target_node_id: str,
        trigger_inputs: dict | None = None,
        pre_resolved_nodes: dict | None = None,
        execute_target: bool = True,
    ) -> dict:
        """Test a single node in a workflow.

        Uses POST /api/v1/workflows/{workflow_id}/test to execute a specific
        node with mocked predecessor outputs.
        """
        body = {
            "target_node_id": target_node_id,
            "trigger_inputs": trigger_inputs or {},
            "pre_resolved_nodes": pre_resolved_nodes or {},
            "execute_target": execute_target,
        }

        start = time.monotonic()
        resp = self._request("POST", f"/api/v1/workflows/{workflow_id}/test", json=body)
        latency_ms = (time.monotonic() - start) * 1000

        result = resp.json()
        result["_latency_ms"] = latency_ms
        return result

    def get_execution(self, execution_id: str, include_activities: bool = True) -> dict:
        """Get execution details, optionally including activity data."""
        params = {}
        if include_activities:
            params["include"] = "activities"

        resp = self._request("GET", f"/api/v1/executions/{execution_id}", params=params)
        return resp.json()

    def poll_execution(
        self,
        execution_id: str,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
    ) -> dict:
        """Poll an execution until it reaches a terminal status."""
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed > max_wait:
                raise TimeoutError(
                    f"Execution {execution_id} did not complete within {max_wait}s"
                )

            execution = self.get_execution(execution_id)
            status = execution.get("status", "")

            if status in TERMINAL_STATUSES:
                execution["_wall_clock_ms"] = (time.monotonic() - start) * 1000
                return execution

            time.sleep(poll_interval)

    def run_workflow(
        self,
        workflow_id: str,
        input_data: dict,
        mode: str = "test",
    ) -> dict:
        """Run a full workflow execution (all nodes).

        Returns the execution response with an ID that can be polled.
        """
        body = {
            "workflow_id": workflow_id,
            "input_data": input_data,
            "mode": mode,
        }
        start = time.monotonic()
        resp = self._request("POST", "/api/v1/executions", json=body)
        latency_ms = (time.monotonic() - start) * 1000
        result = resp.json()
        result["_latency_ms"] = latency_ms
        return result

    def swap_all_agentic_models(
        self,
        workflow_id: str,
        model_name: str,
    ) -> dict:
        """Set every agentic node in a workflow to the same model.

        Returns the original workflow definition for restoration.
        """
        workflow = self.get_workflow(workflow_id)
        original_definition = copy.deepcopy(workflow["version"]["workflow_definition"])

        definition = workflow["version"]["workflow_definition"]
        for node in definition.get("nodes", []):
            if node.get("type") == "agentic":
                node.setdefault("config", {})["model"] = model_name

        self._request("PATCH", f"/api/v1/workflows/{workflow_id}",
                       json={"workflow_definition": definition})
        return original_definition

    def close(self):
        self.client.close()
