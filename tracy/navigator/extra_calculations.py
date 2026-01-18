from dataclasses import dataclass


@dataclass(frozen=True)
class ExtraCalculationSpec:
    key: str
    label: str
    action_attr: str
    toggle_handler: str
    has_popup: bool
    checks_existing: bool
    supports_segments: bool
