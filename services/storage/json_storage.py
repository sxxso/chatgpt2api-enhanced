from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from services.storage.base import StorageBackend
from utils.atomic_json import JSONDataError, read_json, write_json_atomic


class JSONStorageBackend(StorageBackend):
    """本地 JSON 文件存储后端"""

    def __init__(self, file_path: Path, auth_keys_path: Path | None = None):
        self.file_path = file_path
        self.auth_keys_path = auth_keys_path or file_path.with_name("auth_keys.json")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_keys_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    @staticmethod
    def _load_json_list(file_path: Path) -> list[dict[str, Any]]:
        if not file_path.exists():
            return []
        data = read_json(file_path)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise JSONDataError(f"Invalid list of records in {file_path.name}; the original file was preserved.")
        return data

    @staticmethod
    def _save_json_list(file_path: Path, items: list[dict[str, Any]]) -> None:
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise JSONDataError("Account records must be a list of objects.")
        JSONStorageBackend._load_json_list(file_path)
        write_json_atomic(file_path, items)

    def load_accounts(self) -> list[dict[str, Any]]:
        """从 JSON 文件加载账号数据"""
        with self._lock:
            return self._load_json_list(self.file_path)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存账号数据到 JSON 文件"""
        with self._lock:
            self._save_json_list(self.file_path, accounts)

    def load_auth_keys(self) -> list[dict[str, Any]]:
        """从 JSON 文件加载鉴权密钥数据"""
        if not self.auth_keys_path.exists():
            return []
        data = read_json(self.auth_keys_path)
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise JSONDataError(f"Invalid key records in {self.auth_keys_path.name}; the original file was preserved.")
        return data

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存鉴权密钥数据到 JSON 文件"""
        with self._lock:
            self.load_auth_keys()
            if not isinstance(auth_keys, list) or any(not isinstance(item, dict) for item in auth_keys):
                raise JSONDataError("Key records must be a list of objects.")
            write_json_atomic(self.auth_keys_path, {"items": auth_keys})

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        try:
            self.load_accounts()
            self.load_auth_keys()
            return {
                "status": "healthy",
                "backend": "json",
                "file_exists": self.file_path.exists(),
                "file_path": str(self.file_path),
                "auth_keys_file_exists": self.auth_keys_path.exists(),
                "auth_keys_file_path": str(self.auth_keys_path),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "json",
                "error": str(e),
            }

    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        return {
            "type": "json",
            "description": "本地 JSON 文件存储",
            "file_path": str(self.file_path),
            "file_exists": self.file_path.exists(),
            "auth_keys_file_path": str(self.auth_keys_path),
            "auth_keys_file_exists": self.auth_keys_path.exists(),
        }
