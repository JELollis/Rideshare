# app/schemas.py
from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict

# ---------- Driver ----------
class DriverBase(BaseModel):
    name: str
    car: Optional[str] = None
    platform: Optional[str] = None

class DriverCreate(DriverBase):
    pass

class DriverUpdate(BaseModel):
    name: Optional[str] = None
    car: Optional[str] = None
    platform: Optional[str] = None

class Driver(DriverBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ---------- Trip ----------
class TripBase(BaseModel):
    driver_id: int
    date: datetime
    platform: Optional[str] = None
    fare: float
    tip: float = 0.0
    bonus: float = 0.0
    miles: float
    duration_minutes: Optional[int] = None  # integer minutes for consistency

class TripCreate(TripBase):
    pass

class TripUpdate(BaseModel):
    driver_id: Optional[int] = None
    date: Optional[datetime] = None
    platform: Optional[str] = None
    fare: Optional[float] = None
    tip: Optional[float] = None
    bonus: Optional[float] = None
    miles: Optional[float] = None
    duration_minutes: Optional[int] = None

class Trip(TripBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ---------- Expense ----------
class ExpenseBase(BaseModel):
    driver_id: int
    date: datetime
    category: str
    amount: float
    notes: Optional[str] = None

class ExpenseCreate(ExpenseBase):
    pass

class ExpenseUpdate(BaseModel):
    driver_id: Optional[int] = None
    date: Optional[datetime] = None
    category: Optional[str] = None
    amount: Optional[float] = None
    notes: Optional[str] = None

class Expense(ExpenseBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ---------- DailyLog ----------
class DailyLogBase(BaseModel):
    driver_id: int
    date: date
    odo_start: Optional[float] = None
    odo_end: Optional[float] = None
    minutes_driven: Optional[int] = None
    total_earned: float = 0.0
    platform: Optional[str] = None
    trips_count: Optional[int] = None

class DailyLogCreate(DailyLogBase):
    pass

class DailyLogUpdate(BaseModel):
    driver_id: Optional[int] = None
    date: Optional[date] = None
    odo_start: Optional[float] = None
    odo_end: Optional[float] = None
    minutes_driven: Optional[int] = None
    total_earned: Optional[float] = None
    platform: Optional[str] = None
    trips_count: Optional[int] = None

class DailyLog(DailyLogBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ---------- Fuel ----------
class FuelBase(BaseModel):
    driver_id: int
    date: datetime
    odometer: Optional[float] = None
    gallons: Optional[float] = None
    total_paid: Optional[float] = None
    vendor: Optional[str] = None
    notes: Optional[str] = None

class FuelCreate(FuelBase):
    pass

class FuelUpdate(BaseModel):
    driver_id: Optional[int] = None
    date: Optional[datetime] = None
    odometer: Optional[float] = None
    gallons: Optional[float] = None
    total_paid: Optional[float] = None
    vendor: Optional[str] = None
    notes: Optional[str] = None

class Fuel(FuelBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ---------- User ----------
class UserBase(BaseModel):
    username: str
    driver_id: Optional[int] = None  # <-- made optional/nullable
    is_admin: bool = False

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    driver_id: Optional[int] = None
    is_admin: Optional[bool] = None

class User(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)
