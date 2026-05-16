import time
from typing import Optional

from curl_cffi import requests


class YesCaptchaSolverError(Exception):
    pass


class YesCaptchaSolver:
    def __init__(
        self,
        api_base_url: str = "https://api.yescaptcha.com",
        client_key: str = "",
        max_retries: int = 20,
        retry_interval: int = 3,
        timeout: int = 60,
        advanced: bool = False,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.create_task_url = f"{self.api_base_url}/createTask"
        self.get_result_url = f"{self.api_base_url}/getTaskResult"
        self.client_key = client_key
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.advanced = advanced

    def solve(
        self,
        url: str,
        sitekey: str,
        user_agent: Optional[str] = None,
        verbose: bool = False,
    ) -> str:
        task_id = self._create_task(url, sitekey, user_agent, verbose)
        if not task_id:
            raise YesCaptchaSolverError("Failed to create the YesCaptcha task")

        token = self._get_task_result(task_id, verbose)
        if not token:
            raise YesCaptchaSolverError("Failed to fetch the YesCaptcha result")
        return token

    def _create_task(
        self,
        url: str,
        sitekey: str,
        user_agent: Optional[str] = None,
        verbose: bool = False,
    ) -> str:
        task_type = (
            "TurnstileTaskProxylessM1" if self.advanced else "TurnstileTaskProxyless"
        )
        data = {
            "clientKey": self.client_key,
            "task": {
                "type": task_type,
                "websiteURL": url,
                "websiteKey": sitekey,
            },
            "softID": "62709",
        }
        if user_agent:
            data["task"]["userAgent"] = user_agent

        try:
            response = requests.post(
                self.create_task_url,
                json=data,
                timeout=self.timeout,
                impersonate="chrome110",
            )
            result = response.json()
        except Exception as e:
            raise YesCaptchaSolverError(f"Failed to create the YesCaptcha task: {e}") from e

        if result.get("errorId") == 0 and result.get("taskId"):
            if verbose:
                print(f"YesCaptcha task created: {result['taskId']}")
            return result["taskId"]

        raise YesCaptchaSolverError(
            result.get("errorDescription") or "YesCaptcha createTask failed"
        )

    def _get_task_result(self, task_id: str, verbose: bool = False) -> str:
        data = {
            "clientKey": self.client_key,
            "taskId": task_id,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    self.get_result_url,
                    json=data,
                    timeout=self.timeout,
                    impersonate="chrome110",
                )
                result = response.json()
            except Exception as e:
                raise YesCaptchaSolverError(f"Failed to query the YesCaptcha result: {e}") from e

            if result.get("errorId", 0) > 0:
                raise YesCaptchaSolverError(
                    result.get("errorDescription")
                    or "YesCaptcha getTaskResult failed"
                )

            if result.get("status") == "ready":
                token = result.get("solution", {}).get("token")
                if token:
                    return token
                raise YesCaptchaSolverError("YesCaptcha returned ready without a token")

            if verbose:
                print(f"YesCaptcha processing {attempt}/{self.max_retries}")
            time.sleep(self.retry_interval)

        raise YesCaptchaSolverError("Timed out while waiting for YesCaptcha")
