from typing import Optional

from verification.rule_checker import RuleChecker
from verification.vision_checker import VisionChecker


class MultiVerifier:
    def __init__(self) -> None:
        self.rule_checker = RuleChecker()
        self.vision_checker = VisionChecker()

    def check_conditions(self, conditions: list[dict], params: Optional[dict] = None) -> tuple[bool, list[dict]]:
        if not conditions:
            return True, [{"condition": "empty", "passed": True}]
        details = []
        all_passed = True
        for condition in conditions:
            resolved = self._substitute_params(condition, params or {})
            fallback = resolved.get("fallback", False)
            passed, detail = self._check_single(resolved)
            details.append({"condition": resolved, "passed": passed, "detail": detail, "is_fallback": fallback})
            if not passed and not fallback:
                all_passed = False
        return all_passed, details

    def _check_single(self, condition: dict) -> tuple[bool, str]:
        ctype = condition["type"]
        if ctype == "vision_check":
            return self.vision_checker.check(condition["query"], condition.get("expected", True))
        if ctype == "composite_check":
            results = [self._check_single(item)[0] for item in condition.get("checks", [])]
            return all(results), f"composite={results}"
        return self.rule_checker.check(condition)

    def _substitute_params(self, obj, params: dict):
        if isinstance(obj, str):
            for key, value in params.items():
                obj = obj.replace(f"{{{key}}}", str(value))
            return obj
        if isinstance(obj, dict):
            return {key: self._substitute_params(value, params) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._substitute_params(item, params) for item in obj]
        return obj
