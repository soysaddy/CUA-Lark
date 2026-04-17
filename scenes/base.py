from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Tuple


class StateStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    TIMEOUT = auto()


@dataclass
class State:
    name: str
    description: str
    entry_actions: list[dict[str, Any]] = field(default_factory=list)
    success_conditions: list[dict[str, Any]] = field(default_factory=list)
    recovery_actions: list[dict[str, Any]] = field(default_factory=list)
    next_state: str = ""
    fallback_state: str = ""
    max_retries: int = 3
    timeout: float = 15.0


@dataclass
class TaskTemplate:
    name: str
    description: str
    parameters: list[str]
    states: dict[str, State] = field(default_factory=dict)
    initial_state: str = ""

    def add_state(self, state: State) -> None:
        self.states[state.name] = state
        if not self.initial_state:
            self.initial_state = state.name


@dataclass
class UIAnchor:
    name: str
    description: str
    ax_role: str = ""
    ax_identifier: str = ""
    ax_title: str = ""
    visual_description: str = ""
    relative_position: Tuple[float, float] = ()


@dataclass
class PageObject:
    name: str
    description: str
    page_indicators: list[dict[str, Any]] = field(default_factory=list)
    anchors: dict[str, UIAnchor] = field(default_factory=dict)

    def add_anchor(self, anchor: UIAnchor) -> None:
        self.anchors[anchor.name] = anchor
