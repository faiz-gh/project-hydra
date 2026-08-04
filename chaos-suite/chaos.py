import os
import time
import json
import requests
import asyncssh
import logging

logger = logging.getLogger("chaos")

class HCPTerraformController:
    def __init__(self, org: str, workspace: str, token: str):
        self.base_url = "https://app.terraform.io/api/v2"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json"
        }
        self.org = org
        self.workspace_name = workspace
        self.ws_id = self._get_workspace_id()

    def _get_workspace_id(self):
        url = f"{self.base_url}/organizations/{self.org}/workspaces/{self.workspace_name}"
        res = requests.get(url, headers=self.headers).json()
        return res["data"]["id"]

    def get_variable(self, var_name: str):
        url = f"{self.base_url}/workspaces/{self.ws_id}/vars"
        res = requests.get(url, headers=self.headers).json()
        for var in res["data"]:
            if var["attributes"]["key"] == var_name:
                return var
        raise ValueError(f"Variable {var_name} not found.")

    def update_variable(self, var_id: str, payload_dict: dict, hcl: bool = True):
        url = f"{self.base_url}/workspaces/{self.ws_id}/vars/{var_id}"
        payload = {
            "data": {
                "id": var_id,
                "attributes": {
                    "value": json.dumps(payload_dict) if not hcl else self._dict_to_hcl(payload_dict),
                    "hcl": hcl
                },
                "type": "vars"
            }
        }
        requests.patch(url, headers=self.headers, json=payload)

    def trigger_run(self, message: str) -> str:
        url = f"{self.base_url}/runs"
        payload = {
            "data": {
                "attributes": {"message": message},
                "type": "runs",
                "relationships": {
                    "workspace": {"data": {"type": "workspaces", "id": self.ws_id}}
                }
            }
        }
        res = requests.post(url, headers=self.headers, json=payload).json()
        return res["data"]["id"]

    def wait_for_run(self, run_id: str):
        url = f"{self.base_url}/runs/{run_id}"
        logger.info(f"Waiting for Terraform Run {run_id} to apply...")
        while True:
            res = requests.get(url, headers=self.headers).json()
            status = res["data"]["attributes"]["status"]
            if status == "applied":
                logger.info("Terraform apply completed successfully.")
                break
            if status in ["errored", "canceled", "discarded"]:
                raise RuntimeError(f"Terraform run failed with status: {status}")
            time.sleep(10)

    def _dict_to_hcl(self, d: dict) -> str:
        # Simplistic conversion for the specific object structure we have
        return json.dumps(d) # HCP TF handles JSON values for HCL map variables perfectly


class SSHMeshController:
    @staticmethod
    async def toggle_tailscale(ip: str, key_path: str, user: str, enable: bool):
        action = "up" if enable else "down"
        logger.info(f"Executing 'tailscale {action}' on {ip} via SSH...")
        async with asyncssh.connect(ip, username=user, client_keys=[key_path], known_hosts=None) as conn:
            result = await conn.run(f"sudo tailscale {action}", check=True)
            logger.info(f"Node {ip} mesh status: {action}. {result.stdout}")