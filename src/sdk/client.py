"""Orchestrator API client SDK."""

import json
import os
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class OrchestratorClient:
    def __init__(self, base_url: str = None, api_key: str = None):
        self.base_url = base_url or os.getenv("AO_API_URL", "https://api.agent-orchestrator.io")
        self.api_key = api_key or os.getenv("AO_API_KEY", "")
        self._session = None

    def _request(self, method: str, path: str, data: Dict = None) -> Dict:
        url = f"{self.base_url}/api/v2{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            return {"error": e.code, "message": e.reason}

    def register_agent(self, name: str, agent_type: str, config: Dict = None) -> Dict:
        trimmed = name.strip() if name else ""
        if not trimmed:
            raise ValueError("Agent name must not be blank or whitespace-only")
        return self._request("POST", "/agents", {
            "name": trimmed,
            "agent_type": agent_type,
            "config": config or {},
        })

    def list_agents(self, status: str = None) -> Dict:
        path = "/agents"
        if status:
            path += f"?status={status}"
        return self._request("GET", path)

    def get_agent(self, agent_id: str) -> Dict:
        return self._request("GET", f"/agents/{agent_id}")

    def delete_agent(self, agent_id: str) -> Dict:
        return self._request("DELETE", f"/agents/{agent_id}")

    def start_agent(self, agent_id: str) -> Dict:
        return self._request("POST", f"/agents/{agent_id}/start")

    def stop_agent(self, agent_id: str) -> Dict:
        return self._request("POST", f"/agents/{agent_id}/stop")

# 2019-01-22T18:13:52 update

# 2019-04-10T16:03:03 update

# 2019-06-26T09:36:49 update

# 2019-08-16T09:00:05 update

# 2019-08-26T19:43:11 update

# 2019-09-23T14:45:30 update

# 2019-10-21T11:37:53 update

# 2020-01-10T10:26:07 update

# 2020-02-12T09:30:49 update

# 2020-03-08T08:00:29 update

# 2020-03-16T19:59:51 update

# 2020-03-30T17:37:46 update

# 2021-02-05T19:46:37 update

# 2021-02-22T16:54:35 update

# 2021-03-19T15:58:33 update

# 2021-04-15T08:14:13 update

# 2021-05-31T14:33:37 update

# 2021-07-15T18:08:40 update

# 2021-08-24T11:47:00 update

# 2021-12-30T12:02:52 update

# 2022-01-20T13:18:43 update

# 2022-06-17T10:50:38 update

# 2022-11-15T19:15:06 update

# 2023-05-15T18:16:27 update

# 2023-06-22T14:34:00 update

# 2023-07-13T18:44:28 update

# 2023-08-23T19:53:34 update

# 2023-11-17T08:37:45 update

# 2024-01-31T16:24:31 update

# 2024-01-31T08:14:03 update

# 2024-02-01T09:10:23 update

# 2024-07-22T19:04:53 update

# 2024-09-03T09:17:20 update

# 2024-11-13T11:27:07 update

# 2025-01-16T20:56:50 update

# 2025-04-14T16:10:30 update

# 2025-04-16T08:42:38 update

# 2025-05-02T08:11:40 update

# 2025-07-04T18:10:30 update

# 2025-07-23T09:21:21 update

# 2025-09-05T17:05:59 update

# 2025-09-09T15:51:23 update

# 2025-11-14T11:34:48 update

# 2025-12-18T18:16:23 update

# 2025-12-18T14:56:18 update

# 2025-12-18T12:41:47 update

# 2026-02-17T20:41:29 update

# 2026-03-18T12:20:20 update

# 2026-03-20T13:32:14 update

# 2026-03-31T16:25:41 update

# 2026-04-07T11:14:09 update

# 2026-05-11T08:44:28 update

# 2026-05-14T13:49:57 update
