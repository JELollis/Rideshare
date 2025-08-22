# app/models.py
from __future__ import annotations

from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Float, Boolean, ForeignKey, Text, Index
)
from sqlalchemy.orm import relationship

from app.db import Base


# ---------- Driver ----------
class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    car = Column(String(200), nullable=True)
    platform = Column(String(100), nullable=True)

    trips = relationship("Trip", back_populates="driver", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="driver", cascade="all, delete-orphan")
    daily_logs = relationship("DailyLog", back_populates="driver", cascade="all, delete-orphan")
    fuels = relationship("Fuel", back_populates="driver", cascade="all, delete-orphan")

    users = relationship("User", back_populates="driver")

    def __repr__(self) -> str:
        return f"<Driver id={self.id} name={self.name!r}>"


# ---------- Trip (per-ride) ----------
class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)

    date = Column(DateTime, nullable=False, index=True)  # full timestamp
    platform = Column(String(100), nullable=True)

    fare = Column(Float, nullable=False, default=0.0)
    tip = Column(Float, nullable=False, default=0.0)
    bonus = Column(Float, nullable=False, default=0.0)

    miles = Column(Float, nullable=False, default=0.0)
    duration_minutes = Column(Float, nullable=True)  # may be null

    driver = relationship("Driver", back_populates="trips")

Index("ix_trips_driver_date", Trip.driver_id, Trip.date)


# ---------- Expense (non-fuel operating / other) ----------
class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)

    date = Column(DateTime, nullable=False, index=True)
    category = Column(String(100), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    notes = Column(Text, nullable=True)

    driver = relationship("Driver", back_populates="expenses")

Index("ix_expenses_driver_date", Expense.driver_id, Expense.date)
Index("ix_expenses_driver_category", Expense.driver_id, Expense.category)


# ---------- DailyLog (per-day summary) ----------
class DailyLog(Base):
    __tablename__ = "daily_logs"

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)

    date = Column(Date, nullable=False, index=True)
    odo_start = Column(Float, nullable=True)
    odo_end = Column(Float, nullable=True)

    minutes_driven = Column(Integer, nullable=True)  # total minutes for the day
    total_earned = Column(Float, nullable=False, default=0.0)
    platform = Column(String(100), nullable=True)
    trips_count = Column(Integer, nullable=True)

    driver = relationship("Driver", back_populates="daily_logs")

Index("ix_daily_driver_date", DailyLog.driver_id, DailyLog.date)


# ---------- Fuel (separate log) ----------
class Fuel(Base):
    __tablename__ = "fuel_logs"

    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False, index=True)

    date = Column(DateTime, nullable=False, index=True)
    odometer = Column(Float, nullable=True)
    gallons = Column(Float, nullable=True)
    total_paid = Column(Float, nullable=True)

    vendor = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)

    driver = relationship("Driver", back_populates="fuels")

Index("ix_fuel_driver_date", Fuel.driver_id, Fuel.date)


# ---------- User (auth) ----------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(150), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)

    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True, index=True)
    is_admin = Column(Boolean, nullable=False, default=False)

    driver = relationship("Driver", back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} admin={self.is_admin}>"
