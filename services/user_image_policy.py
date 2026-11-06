"""Single-process per-key image limits. Only successful results consume daily use."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
import uuid

from services.auth_service import auth_service
from services.config import DATA_DIR, config
from utils.atomic_json import JSONDataError, read_json, write_json_atomic


class UserImagePolicyError(RuntimeError):
    def __init__(self, message: str, status_code: int = 429):
        super().__init__(message)
        self.status_code = status_code


class UserImagePolicy:
    def __init__(self, path: Path, policy_getter=None, day_getter=None):
        self.path = path
        self._policy = policy_getter or auth_service.image_policy
        self._day = day_getter or (lambda: datetime.now(timezone(timedelta(hours=8))).date().isoformat())
        self._lock = RLock()
        self._active: dict[str, tuple[str, str, str]] = {}
        raw = read_json(path) if path.exists() else {"users": {}}
        self._users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(self._users, dict) or any(
            not isinstance(value, dict) or not isinstance(value.get("results", {}), dict)
            or any(not isinstance(day, str) for day in value.get("results", {}).values())
            for value in self._users.values()
        ):
            raise JSONDataError("Invalid user image usage; original file was preserved")
        for value in self._users.values():
            for day in value.get("results", {}).values():
                try:
                    if datetime.fromisoformat(day).date().isoformat() != day:
                        raise ValueError()
                except ValueError:
                    raise JSONDataError("Invalid user image usage date; original file was preserved") from None

    def check(self, identity: dict | None, model: str) -> dict | None:
        if not identity or identity.get("role") != "user":
            return None
        item = self._policy(str(identity.get("id") or ""))
        if not item or not item.get("enabled", True) or item.get("expired"):
            raise UserImagePolicyError("用户密钥已禁用、删除或到期", 403)
        models = item.get("allowed_image_models")
        if models is not None and model not in models:
            raise UserImagePolicyError("此用户密钥无权使用该图片模型", 403)
        return item

    def concurrency(self, identity: dict | None) -> int:
        if not identity or identity.get("role") != "user":
            return config.image_global_concurrency
        item = self._policy(str(identity.get("id") or "")) or {}
        return int(item.get("max_image_concurrency") or config.image_global_concurrency)

    def usage(self, key_id: str) -> dict:
        with self._lock:
            day = self._day()
            results = self._users.get(key_id, {}).get("results", {})
            return {"day": day, "used": sum(date == day for date in results.values()),
                    "active": sum(user == key_id for user, _, _ in self._active.values())}

    @contextmanager
    def acquire(self, identity: dict | None, model: str, result_id: str):
        policy = self.check(identity, model)
        if policy is None:
            yield lambda: None
            return
        key_id = str(identity["id"])
        ticket = uuid.uuid4().hex
        with self._lock:
            day = self._day()
            results = self._users.get(key_id, {}).get("results", {})
            if any(user == key_id and result == result_id for user, _, result in self._active.values()):
                raise UserImagePolicyError("该图片任务正在处理中，请等待原任务")
            active = sum(user == key_id for user, _, _ in self._active.values())
            if active >= self.concurrency(identity):
                raise UserImagePolicyError("已达到此用户的图片并发上限，请稍后重试")
            reserved_today = sum(user == key_id and date == day for user, date, _ in self._active.values())
            limit = int(policy.get("daily_image_limit") or 0)
            if result_id not in results and limit and sum(date == day for date in results.values()) + reserved_today >= limit:
                raise UserImagePolicyError("此用户今日图片额度已用完（北京时间每日零点重置）")
            self._active[ticket] = (key_id, day, result_id)

        def complete():
            with self._lock:
                if result_id in self._users.get(key_id, {}).get("results", {}):
                    return
                users = deepcopy(self._users)
                # Keep deduplication evidence for at least the task retention period.
                cutoff = (datetime.fromisoformat(self._day()) - timedelta(days=max(32, config.image_retention_days + 1))).date().isoformat()
                results = {key: date for key, date in users.get(key_id, {}).get("results", {}).items() if date >= cutoff}
                results[result_id] = day
                users[key_id] = {"results": results}
                write_json_atomic(self.path, {"users": users})
                self._users = users
        try:
            yield complete
        finally:
            with self._lock:
                self._active.pop(ticket, None)


user_image_policy = UserImagePolicy(DATA_DIR / "user_image_usage.json")
