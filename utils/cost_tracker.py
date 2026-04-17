from dataclasses import dataclass


@dataclass
class CostTracker:
    total_tokens: int = 0
    total_calls: int = 0

    def add_usage(self, tokens: int) -> None:
        self.total_calls += 1
        self.total_tokens += max(tokens, 0)

    @property
    def estimated_cost_usd(self) -> float:
        return self.total_tokens * 0.00001
