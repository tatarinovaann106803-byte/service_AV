"""Website contract: engineering configuration is deliberately not an input."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .schemas import Capital, InputModel, NonNegative, Risks


class PublicWater(InputModel):
    baseline_et_mm: NonNegative
    soil_evaporation_mm: NonNegative
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


class PublicRequest(InputModel):
    sector: Literal["crop", "aqua", "forest"] = "crop"
    product_name: Annotated[str, Field(min_length=1, max_length=200)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    area_ha: Annotated[float, Field(gt=0, le=1e6)]
    country: str = "Russian Federation"
    base_productivity: Annotated[float, Field(gt=0, le=1e9)] | None = None
    product_price: NonNegative
    energy_price: NonNegative
    capital: Capital = Field(default_factory=Capital)
    project_years: Annotated[int, Field(ge=1, le=100)] = 25
    budget_rub: Annotated[float, Field(gt=0, le=1e12)] | None = None
    radiation_annual: Annotated[float, Field(ge=0, le=4000)] | None = None
    weather_year: Annotated[int, Field(ge=1984, le=2100)] = 2024
    season_start_month: Annotated[int, Field(ge=1, le=12)] = 4
    season_end_month: Annotated[int, Field(ge=1, le=12)] = 9
    growing_season_temperature_c: Annotated[float, Field(ge=-20, le=50)] = 22
    growing_season_rainfall_mm: Annotated[float, Field(ge=0, le=10000)] = 300
    irrigation_mm: Annotated[float, Field(ge=0, le=10000)] = 250
    water: PublicWater | None = None
    risks: Risks | None = None
