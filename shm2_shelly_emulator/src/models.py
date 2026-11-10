"""Data models for electrical measurements and Shelly Pro 3EM representation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PhaseMeasurement:
    """Electrical measurements for an individual phase (A/L1, B/L2, C/L3)."""

    current: float = 0.0       # Amperes [A]
    voltage: float = 230.0     # Volts [V]
    act_power: float = 0.0     # Active power [W] (positive = import, negative = export)
    aprt_power: float = 0.0    # Apparent power [VA]
    pf: float = 1.0            # Power factor [-1.0 .. 1.0]
    freq: float = 50.0         # Frequency [Hz]
    energy_act: float = 0.0    # Active energy consumed/imported [Wh]
    energy_act_ret: float = 0.0  # Active energy returned/exported [Wh]

    def to_em_status_dict(self, prefix: str) -> Dict[str, Any]:
        """Convert to keys for EM.GetStatus (e.g. a_current, a_voltage, ...)."""
        return {
            f"{prefix}_current": round(self.current, 3),
            f"{prefix}_voltage": round(self.voltage, 1),
            f"{prefix}_act_power": round(self.act_power, 1),
            f"{prefix}_aprt_power": round(self.aprt_power, 1),
            f"{prefix}_pf": round(self.pf, 2),
            f"{prefix}_freq": round(self.freq, 1),
        }

    def invert_sign(self) -> PhaseMeasurement:
        """Return a copy with inverted power signs and swapped energy counters."""
        return PhaseMeasurement(
            current=self.current,
            voltage=self.voltage,
            act_power=-self.act_power,
            aprt_power=self.aprt_power,
            pf=self.pf,
            freq=self.freq,
            energy_act=self.energy_act_ret,
            energy_act_ret=self.energy_act,
        )


@dataclass
class MeterData:
    """Aggregated three-phase grid meter data."""

    timestamp: float = field(default_factory=time.time)
    source: str = "none"  # "speedwire", "homeassistant", or "none"
    serial: str = ""

    a: PhaseMeasurement = field(default_factory=PhaseMeasurement)
    b: PhaseMeasurement = field(default_factory=PhaseMeasurement)
    c: PhaseMeasurement = field(default_factory=PhaseMeasurement)

    # Aggregates across all three phases
    total_current: float = 0.0
    total_act_power: float = 0.0
    total_aprt_power: float = 0.0
    total_act_energy: float = 0.0
    total_act_ret_energy: float = 0.0

    def is_stale(self, timeout_seconds: float) -> bool:
        """Check if measurements have not been updated within timeout_seconds."""
        if self.timestamp <= 0:
            return True
        return (time.time() - self.timestamp) > timeout_seconds

    def apply_inversion(self) -> MeterData:
        """Return a copy of the meter data with inverted power sign and swapped energy counters."""
        inv_a = self.a.invert_sign()
        inv_b = self.b.invert_sign()
        inv_c = self.c.invert_sign()
        return MeterData(
            timestamp=self.timestamp,
            source=self.source,
            serial=self.serial,
            a=inv_a,
            b=inv_b,
            c=inv_c,
            total_current=self.total_current,
            total_act_power=-self.total_act_power,
            total_aprt_power=self.total_aprt_power,
            total_act_energy=self.total_act_ret_energy,
            total_act_ret_energy=self.total_act_energy,
        )

    def to_em_status(self, is_stale_flag: bool = False) -> Dict[str, Any]:
        """Format as Shelly Pro 3EM EM.GetStatus payload (id: 0)."""
        res: Dict[str, Any] = {"id": 0}
        res.update(self.a.to_em_status_dict("a"))
        res.update(self.b.to_em_status_dict("b"))
        res.update(self.c.to_em_status_dict("c"))
        res["n_current"] = None
        res["total_current"] = round(self.total_current, 3)
        res["total_act_power"] = round(self.total_act_power, 1)
        res["total_aprt_power"] = round(self.total_aprt_power, 1)
        res["user_calibrated_phase"] = []
        errors: List[str] = []
        if is_stale_flag:
            errors.extend(["power_meter_failure", "data_stale"])
        res["errors"] = errors
        return res

    def to_emdata_status(self) -> Dict[str, Any]:
        """Format as Shelly Pro 3EM EMData.GetStatus payload (id: 0)."""
        return {
            "id": 0,
            "a_total_act_energy": round(self.a.energy_act, 2),
            "a_total_act_ret_energy": round(self.a.energy_act_ret, 2),
            "b_total_act_energy": round(self.b.energy_act, 2),
            "b_total_act_ret_energy": round(self.b.energy_act_ret, 2),
            "c_total_act_energy": round(self.c.energy_act, 2),
            "c_total_act_ret_energy": round(self.c.energy_act_ret, 2),
            "total_act": round(self.total_act_energy, 2),
            "total_act_ret": round(self.total_act_ret_energy, 2),
        }
