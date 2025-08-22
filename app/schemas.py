# app/schemas.py
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict

# -------- Driver --------
class DriverBase(BaseModel):
    name: str
    car: Optional[str] = None
    platform: Optional[str] = None

class DriverCreate(DriverBase):
    pass

class Driver(DriverBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# -------- Trip --------
class TripBase(BaseModel):
    driver_id: int
    date: datetime
    platform: Optional[str] = None
    fare: float
    tip: float = 0.0
    bonus: float = 0.0
    miles: float
    duration_minutes: Optional[float] = None

class TripCreate(TripBase):
    pass

class Trip(TripBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# -------- Expense --------
class ExpenseBase(BaseModel):
    driver_id: int
    date: date
    category: str
    amount: float
    notes: Optional[str] = None

class ExpenseCreate(ExpenseBase):
    pass

class Expense(ExpenseBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# -------- Daily --------
class DailyLogBase(BaseModel):
    driver_id: int
    date: date
    odo_start: float
    odo_end: float
    minutes_driven: Optional[int] = None
    total_earned: float
    platform: Optional[str] = None
    trips_count: Optional[int] = None

class DailyLogCreate(DailyLogBase):
    pass

class DailyLog(DailyLogBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# -------- Fuel --------
class FuelLogBase(BaseModel):
    driver_id: int
    date: datetime
    odometer: float
    gallons: float
    total_paid: float
    station: Optional[str] = None

class FuelLogCreate(FuelLogBase):
    pass

class FuelLog(FuelLogBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# -------- User --------
class UserBase(BaseModel):
    username: str
    driver_id: int
    is_admin: bool = False

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None  # plain text for update; we’ll hash
    driver_id: Optional[int] = None
    is_admin: Optional[bool] = None

class User(BaseModel):
    id: int
    username: str
    driver_id: int
    is_admin: bool
    model_config = ConfigDict(from_attributes=True)
