"""Operating forecast with lease treatment (methodology §2, §3, §8)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from erp_engine.core.errors import EngineError
from erp_engine.core.numbers import ZERO
from erp_engine.core.trace import Trace
from erp_engine.core.validation import Severity, ValidationReport
from erp_engine.inputs import (
    Leases,
    LeaseStandard,
    LeaseTreatment,
    LeaseYear,
    Operating,
    SbcTreatment,
)

MODULE = "forecast"


@dataclass(frozen=True)
class ForecastYear:
    revenue: Decimal
    ebit: Decimal  # lease-adjusted per the run's treatment
    da: Decimal  # lease-adjusted
    capex: Decimal
    nwc: Decimal
    delta_nwc: Decimal
    tax_rate: Decimal
    sbc: Decimal
    sbc_add_back: bool

    @property
    def ebitda(self) -> Decimal:
        return self.ebit + self.da

    @property
    def taxes(self) -> Decimal:
        return self.tax_rate * max(self.ebit, ZERO)

    @property
    def nopat(self) -> Decimal:
        return self.ebit - self.taxes

    @property
    def fcff(self) -> Decimal:
        add_back = self.sbc if self.sbc_add_back else ZERO
        return self.nopat + self.da - self.capex - self.delta_nwc + add_back


def lease_basis(leases: Leases) -> str | None:
    if leases.standard is LeaseStandard.NONE:
        return None
    return "pre_lease" if leases.treatment is LeaseTreatment.AS_DEBT else "post_lease"


def _required(year: LeaseYear, names: tuple[str, ...], k: int) -> list[Decimal]:
    values = [getattr(year, n) for n in names]
    missing = [n for n, v in zip(names, values, strict=True) if v is None]
    if missing:
        raise EngineError(
            "lease_inputs_missing", f"Year {k}: lease treatment requires {', '.join(missing)}."
        )
    return [v for v in values if v is not None]


def _lease_adjust(ebit: Decimal, da: Decimal, leases: Leases, k: int) -> tuple[Decimal, Decimal]:
    """Returns lease-adjusted (EBIT, D&A) for forecast year k (1-based)."""
    std, treat = leases.standard, leases.treatment
    if std is LeaseStandard.NONE:
        return ebit, da
    if (std, treat) in {
        (LeaseStandard.IFRS16, LeaseTreatment.AS_DEBT),
        (LeaseStandard.ASC842, LeaseTreatment.AS_OPERATING),
    }:
        return ebit, da  # already on the treatment's basis
    if len(leases.years) < k:
        raise EngineError("lease_inputs_missing", f"Lease amounts missing for year {k}.")
    year = leases.years[k - 1]
    if std is LeaseStandard.ASC842:  # as_debt: add back lease cost, use ROU-equivalent D&A
        cost, rou = _required(year, ("operating_lease_cost", "rou_depreciation"), k)
        return ebit + cost - rou, da + rou
    # IFRS 16, as_operating: replace ROU depreciation with full lease cash cost
    interest, principal, rou = _required(
        year, ("lease_interest", "lease_principal", "rou_depreciation"), k
    )
    return ebit - interest - principal + rou, da - rou


def project(
    op: Operating, leases: Leases, report: ValidationReport, trace: Trace
) -> list[ForecastYear]:
    if leases.capex_includes_rou_additions:
        raise EngineError(
            "rou_double_count",
            "Capex must exclude right-of-use additions (they are financed by the lease "
            "liability or captured in lease cost).",
        )
    _check_sbc(op, report)
    revenue, nwc_prev = op.revenue_0, op.nwc_0
    out: list[ForecastYear] = []
    for k, y in enumerate(op.years, start=1):
        revenue = revenue * (1 + y.growth)
        ebit, da = _lease_adjust(revenue * y.ebit_margin, revenue * y.da_pct, leases, k)
        nwc = revenue * y.nwc_pct
        fy = ForecastYear(
            revenue=revenue,
            ebit=ebit,
            da=da,
            capex=revenue * y.capex_pct,
            nwc=nwc,
            delta_nwc=nwc - nwc_prev,
            tax_rate=y.tax_rate,
            sbc=revenue * y.sbc_pct,
            sbc_add_back=op.sbc_treatment is SbcTreatment.ADD_BACK,
        )
        nwc_prev = nwc
        out.append(fy)
        trace.record(
            f"forecast.revenue[{k}]",
            f"Revenue, year {k}",
            fy.revenue,
            unit="currency",
            rule="Revenue(k−1) × (1 + growth)",
            module=MODULE,
        )
        trace.record(
            f"forecast.ebit[{k}]",
            f"EBIT, year {k}",
            fy.ebit,
            unit="currency",
            rule="Revenue × EBIT margin (lease-adjusted per treatment)",
            module=MODULE,
            inputs=(f"forecast.revenue[{k}]",),
        )
        trace.record(
            f"forecast.nopat[{k}]",
            f"NOPAT, year {k}",
            fy.nopat,
            unit="currency",
            rule="EBIT − tax rate × max(EBIT, 0)",
            module=MODULE,
            inputs=(f"forecast.ebit[{k}]",),
        )
        trace.record(
            f"forecast.fcff[{k}]",
            f"FCFF, year {k}",
            fy.fcff,
            unit="currency",
            rule="NOPAT + D&A − Capex − ΔNWC (+ SBC if add-back)",
            module=MODULE,
            inputs=(f"forecast.nopat[{k}]",),
        )
        if fy.ebit < 0:
            report.add(
                "negative_ebit_no_tax_credit",
                Severity.INFO,
                f"Year {k}: negative EBIT; no tax credit applied (R1, no NOLs).",
            )
    return out


def _check_sbc(op: Operating, report: ValidationReport) -> None:
    if op.sbc_treatment is SbcTreatment.EXPENSE and op.future_dilution_modelled:
        report.add(
            "sbc_double_count",
            Severity.WARNING,
            "SBC is expensed in FCFF and future dilution is also modelled.",
            blocks_save=True,
        )
    if op.sbc_treatment is SbcTreatment.ADD_BACK and not op.future_dilution_modelled:
        report.add(
            "sbc_dilution_omitted",
            Severity.WARNING,
            "SBC is added back to FCFF but future dilution is not modelled.",
            blocks_save=True,
        )
