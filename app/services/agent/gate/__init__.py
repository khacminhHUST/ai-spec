"""G0. Gate: phân tuyến lookup | agent."""
from app.services.agent.gate.gate import run_gate
from app.services.agent.gate.schema import GateOutput

__all__ = ["GateOutput", "run_gate"]
