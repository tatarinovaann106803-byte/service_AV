"""Public inputs. Money is RUB; rates are percentages unless stated otherwise."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegative = Annotated[float, Field(ge=0, le=1e12)]
Percent = Annotated[float, Field(ge=0, le=100)]
Reduction = Annotated[float, Field(ge=-100, le=100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Capital(InputModel):
    method: Literal["direct", "wacc"] = "direct"
    discount_rate_pct: Annotated[float, Field(ge=0, le=100)] = 10
    equity_share_pct: Percent = 100
    cost_of_equity_pct: Percent = 10
    cost_of_debt_pct: Percent = 0
    tax_rate_pct: Percent = 0

    def rate(self) -> float:
        if self.method == "direct":
            return self.discount_rate_pct / 100
        equity = self.equity_share_pct / 100
        return (
            equity * self.cost_of_equity_pct + (1 - equity) * self.cost_of_debt_pct * (1 - self.tax_rate_pct / 100)
        ) / 100


class Water(InputModel):
    baseline_et_mm: NonNegative
    soil_evaporation_mm: NonNegative
    soil_evaporation_reduction_pct: Reduction = 0
    effective_rainfall_mm: NonNegative = 0
    effective_rainfall_av_mm: NonNegative | None = None
    available_soil_water_mm: NonNegative = 0
    irrigation_efficiency: Annotated[float, Field(gt=0, le=1)] = 0.8
    water_cost_rub_m3: NonNegative = 0

    @model_validator(mode="after")
    def components(self):
        if self.soil_evaporation_mm > self.baseline_et_mm:
            raise ValueError("Испарение почвы не может превышать суммарную ET")
        return self


class Hazard(InputModel):
    probability_pct: Percent = 0
    loss_fraction_pct: Percent = 0
    probability_reduction_pct: Reduction = 0
    severity_reduction_pct: Reduction = 0

    @model_validator(mode="after")
    def adjusted_probabilities(self):
        for base, reduction in [
            (self.probability_pct, self.probability_reduction_pct),
            (self.loss_fraction_pct, self.severity_reduction_pct),
        ]:
            if base * (1 - reduction / 100) > 100:
                raise ValueError("Итоговая вероятность или доля потери превышает 100%")
        return self


class Risks(InputModel):
    frost: Hazard = Field(default_factory=Hazard)
    drought: Hazard = Field(default_factory=Hazard)
    hail: Hazard = Field(default_factory=Hazard)
    events_are_mutually_exclusive: bool = False
    include_in_cash_flow: bool = False

    @model_validator(mode="after")
    def aggregation(self):
        hazards = [self.frost, self.drought, self.hail]
        if self.include_in_cash_flow and not self.events_are_mutually_exclusive:
            raise ValueError("Для учёта потерь в экономике нужны непересекающиеся классы событий")
        if self.events_are_mutually_exclusive:
            if sum(h.probability_pct for h in hazards) > 100:
                raise ValueError("Сумма вероятностей непересекающихся событий превышает 100%")
            if sum(h.probability_pct * (1 - h.probability_reduction_pct / 100) for h in hazards) > 100:
                raise ValueError("Сумма вероятностей после АВ превышает 100%")
        return self


class MicroclimateInputs(InputModel):
    air_temperature_open_c: Annotated[float, Field(ge=-60, le=60)]
    relative_humidity_open_pct: Percent
    ghi_kwh_m2_day: Annotated[float, Field(ge=0, le=15)]
    wind_speed_m_s: Annotated[float, Field(ge=0, le=70)]
    soil_moisture_open_pct: Percent


class YieldInputs(InputModel):
    growing_season_temperature_c: Annotated[float, Field(ge=-30, le=55)]
    growing_season_rainfall_mm: Annotated[float, Field(ge=0, le=10000)]
    irrigation_mm: Annotated[float, Field(ge=0, le=10000)]


class CalculationRequest(InputModel):
    sector: Literal["crop", "aqua", "forest"] = "crop"
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    area_ha: Annotated[float, Field(gt=0, le=1e6)]
    coverage: Annotated[float, Field(ge=0, le=1)]
    height: Annotated[float, Field(gt=0, le=50)]
    energy_price: NonNegative
    system_type: Literal["fixed", "tracking"] = "fixed"
    weather_year: Annotated[int, Field(ge=1984, le=2100)] = 2024
    radiation_annual: Annotated[float, Field(ge=0, le=4000)] | None = None
    radiation_monthly: Annotated[list[NonNegative], Field(min_length=12, max_length=12)] | None = None
    temp: Annotated[float, Field(ge=-70, le=70)] | None = None
    region: str | None = None
    country: str = "Russian Federation"
    crop_name: str = "Пшеница"
    crop_price: NonNegative | None = None
    base_yield: NonNegative | None = None
    fish_name: str = "Карп"
    fish_price: NonNegative | None = None
    base_fish_yield: NonNegative | None = None
    forest_name: str = "Сосна"
    wood_price: NonNegative | None = None
    base_wood_yield: NonNegative | None = None
    stocking_density: NonNegative = 10
    oxygen_level: NonNegative = 7
    pond_depth: Annotated[float, Field(gt=0, le=100)] = 3
    tree_height: NonNegative = 15
    canopy_density: Annotated[float, Field(ge=0, le=1)] = 0.6
    productivity_mode: Literal["scenario", "model"] = "scenario"
    yield_inputs: YieldInputs | None = None
    relative_yield_pct: Annotated[float, Field(ge=0, le=500)] = 100
    capital: Capital = Field(default_factory=Capital)
    project_years: Annotated[int, Field(ge=1, le=100)] = 25
    capex_per_kw: NonNegative | None = None
    fixed_capex_rub: NonNegative = 0
    opex_pct: Percent = 1.5
    annual_extra_cost_rub: NonNegative = 0
    degradation_pct: Percent = 0.5
    performance_ratio: Annotated[float, Field(gt=0, le=1)] = 0.85
    annual_cash_flow_tax_pct: Percent = 0
    water: Water | None = None
    risks: Risks | None = None
    microclimate_inputs: MicroclimateInputs | None = None

    @model_validator(mode="after")
    def coherent_inputs(self):
        if self.productivity_mode == "model":
            if self.sector != "crop":
                raise ValueError("Прогноз урожайности доступен только для растениеводства")
            if self.relative_yield_pct != 100:
                raise ValueError("В режиме модели не задавайте сценарную relative_yield_pct")
        if self.radiation_monthly is not None and self.radiation_annual is not None:
            if abs(sum(self.radiation_monthly) - self.radiation_annual) > 0.01:
                raise ValueError("Сумма месячной радиации не совпадает с годовой")
        if self.sector != "crop" and (self.water is not None or self.risks is not None):
            raise ValueError("Модули полива и рисков урожая применимы только к растениеводству")
        if self.coverage == 0:
            if self.relative_yield_pct != 100:
                raise ValueError("Без панелей относительная урожайность должна быть 100%")
            if self.water and (
                self.water.soil_evaporation_reduction_pct != 0
                or self.water.effective_rainfall_av_mm not in (None, self.water.effective_rainfall_mm)
            ):
                raise ValueError("Без панелей нельзя задавать эффект АВ на водный баланс")
            if self.risks and any(
                h.probability_reduction_pct or h.severity_reduction_pct
                for h in (self.risks.frost, self.risks.drought, self.risks.hail)
            ):
                raise ValueError("Без панелей нельзя задавать защитный эффект АВ")
        return self
