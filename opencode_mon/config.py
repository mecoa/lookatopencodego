"""Configuration loading for the opencode-go usage monitor."""

import json
import os
from typing import Any

DEFAULTS = {
    "db_path": "~/.local/share/opencode/opencode.db",
    "host": "127.0.0.1",
    "port": 8932,
    "refresh_interval": 2,
    "reasoning_as_output": True,
    "count_title_requests": True,
    "active_window_seconds": 90,
    "series_days": 14,
    "go_providers": ["opencode-go"],
    "policy_file": "policy.json",
    "policy_default_file": "policy.default.json",
    "account": {
        "enabled": True,
        "api_key": "",
        "api_key_file": "",
        "auth_json_path": "~/.local/share/opencode/auth.json",
        "endpoint": "https://opencode.ai/zen/go/v1/usage",
        "refresh_seconds": 60,
        "timeout_seconds": 15,
    },
    "go_plan": {"limits": {"5h": 12.0, "week": 30.0, "month": 60.0}},
    "models": {},
}

PLAN_WINDOW_KEYS = {
    "5h": "5h",
    "week_rolling": "week",
    "week": "week",
    "month_rolling": "month",
    "month": "month",
}

WINDOW_ORDER = ("5h", "week_rolling", "week", "month_rolling", "month")


class Config:
    def __init__(self, path=None, data=None, overrides=None):
        self.path = path
        self.data = data if data is not None else self._load(path)
        merged = dict(DEFAULTS)
        merged.update(self.data)
        for key, value in overrides.items() if overrides else []:
            merged[key] = value
        self.data = merged
        self._load_policy()
        self.models_data = self.data.get("models", {})
        self._model_ids = {self._normalize(k): k for k in self.models_data}

    @staticmethod
    def _load(path):
        if not path or not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _load_policy(self):
        """Load plan limits + model pricing from policy file (mutable, gitignored).

        Priority: policy_file (user/generated) -> policy_default_file (committed).
        Writes always go to policy_file; policy_file path stays in self.policy_path.
        """
        primary = os.path.expanduser(self.data.get("policy_file", "policy.json"))
        self.policy_path = primary
        self.policy_source = primary
        data = self._load(primary)
        if not data:
            fallback = os.path.expanduser(
                self.data.get("policy_default_file", "policy.default.json"))
            data = self._load(fallback)
            if data:
                self.policy_source = fallback
        if data:
            limits = (data.get("go_plan") or {}).get("limits")
            if limits:
                self.data.setdefault("go_plan", {})["limits"] = limits
            models = data.get("models")
            if models:
                self.data["models"] = models

    @staticmethod
    def _normalize(model):
        if not model:
            return ""
        return model.lower().split("/")[-1].split(":")[0].split("@")[0].strip()

    @property
    def db_path(self):
        return os.path.expanduser(self.data.get("db_path", DEFAULTS["db_path"]))

    def get(self, key, default=None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def plan_limits(self):
        return self.data.get("go_plan", {}).get("limits", DEFAULTS["go_plan"]["limits"])

    def plan_limit(self, window_key):
        key = PLAN_WINDOW_KEYS.get(window_key, window_key)
        return self.plan_limits().get(key)

    def model_ids(self):
        return sorted(self.models_data.keys())

    def go_providers(self):
        return self.data.get("go_providers", DEFAULTS["go_providers"])

    def is_go(self, provider):
        return (provider or "") in self.go_providers()

    def account_cfg(self):
        base = DEFAULTS["account"]
        cfg = self.data.get("account") or {}
        merged = dict(base)
        merged.update(cfg)
        return merged

    def normalize(self, model):
        return self._normalize(model)

    def model_cfg(self, model):
        norm = self._normalize(model)
        return self.models_data.get(norm) or self.models_data.get(model)

    def priced(self, model):
        return self._normalize(model) in self._model_ids

    def model_requests_limit(self, model, window_key):
        cfg = self.model_cfg(model)
        if not cfg:
            return None
        key = PLAN_WINDOW_KEYS.get(window_key, window_key)
        req = (cfg.get("requests") or {}).get(key)
        return req

    def model_value_limit(self, model, window_key):
        cfg = self.model_cfg(model)
        if not cfg:
            return None
        key = PLAN_WINDOW_KEYS.get(window_key, window_key)
        if key == "month":
            return cfg.get("allowance_month")
        return self.plan_limit(key)

    def rate_for(self, model, total_tokens):
        cfg = self.model_cfg(model)
        if not cfg:
            return None
        tiers = cfg.get("tiers")
        if tiers:
            for tier in tiers:
                mx = tier.get("max_total")
                if mx is None or total_tokens <= mx:
                    return tier
            return tiers[-1]
        return cfg
