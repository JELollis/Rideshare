# app/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    car = Column(String(100), nullable=True)
    platform = Column(String(50), nullable=True)

    trips = relationship("Trip", back_populates="driver", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="driver", cascade="all, delete-orphan")
    daily_logs = relationship("DailyLog", back_populates="driver", cascade="all, delete-orphan")
    fuel_logs = relationship("FuelLog", back_populates="driver", cascade="all, delete-orphan")
    users = relationship("User", back_populates="driver", uselist=True)


class Trip(Base):
    __tablename__ = "trips"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    platform = Column(String(50), nullable=True)
    fare = Column(Float, default=0.0)
    tip = Column(Float, default=0.0)
    bonus = Column(Float, default=0.0)
    miles = Column(Float, default=0.0)
    duration_minutes = Column(Float, nullable=True)
    driver = relationship("Driver", back_populates="trips")

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(Date, nullable=False)          # <-- DATE (matches dump)
    category = Column(String(100), nullable=False)
    amount = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)          # <-- TEXT (matches dump)
    driver = relationship("Driver", back_populates="expenses")

class DailyLog(Base):
    __tablename__ = "daily_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(Date, default=datetime.utcnow)
    odo_start = Column(Float, nullable=False)
    odo_end = Column(Float, nullable=False)
    minutes_driven = Column(Integer, nullable=True)
    total_earned = Column(Float, nullable=False)
    platform = Column(String(50), nullable=True)
    trips_count = Column(Integer, nullable=True)
    driver = relationship("Driver", back_populates="daily_logs")


class FuelLog(Base):
    __tablename__ = "fuel_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    odometer = Column(Float, nullable=False)
    gallons = Column(Float, nullable=False)
    total_paid = Column(Float, nullable=False)
    station = Column(String(128), nullable=True)
    driver = relationship("Driver", back_populates="fuel_logs")


# Simple user tied to a driver
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)  # <-- NEW

    driver = relationship("Driver", back_populates="users", uselist=False)

class State(Base):
    __tablename__ = "states"
    id = Column(Integer, primary_key=True)
    code = Column(String(2), unique=True, nullable=False)
    name = Column(String(100), nullable=False)

    brackets = relationship("StateTaxBracket", back_populates="state", cascade="all, delete-orphan")
    profiles = relationship("StateTaxProfile", back_populates="state")

class StateTaxProfile(Base):
    __tablename__ = "state_tax_profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=False)
    year = Column(Integer, nullable=False, index=True)

    # calculation strategy
    strategy = Column(String(16), nullable=False, default="bracket")  # "flat" or "bracket"

    # flat strategy
    flat_rate = Column(Float, nullable=True)  # e.g., 0.0499 for 4.99%

    # shared adjustments
    filing_status = Column(String(24), nullable=False, default="single")  # single/mfj/mfs/hoh
    standard_deduction = Column(Float, nullable=True)  # per year/state; optional
    personal_exemptions = Column(Float, nullable=True) # optional flat exemption amount
    local_rate = Column(Float, nullable=True)          # extra local % (e.g., city) as decimal

    # relationships
    user = relationship("User", back_populates="tax_profiles")
    state = relationship("State", back_populates="profiles")

    __table_args__ = (
        UniqueConstraint("user_id", "year", name="uq_state_profile_user_year"),
    )

class StateTaxBracket(Base):
    __tablename__ = "state_tax_brackets"
    id = Column(Integer, primary_key=True)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    filing_status = Column(String(24), nullable=False, default="single")
    bracket_min = Column(Float, nullable=False)  # inclusive
    bracket_max = Column(Float, nullable=True)   # None/NULL => no ceiling
    rate = Column(Float, nullable=False)         # 0.0499 => 4.99%

    state = relationship("State", back_populates="brackets")

    __table_args__ = (
        UniqueConstraint("state_id", "year", "filing_status", "bracket_min", name="uq_bracket_unique"),
    )