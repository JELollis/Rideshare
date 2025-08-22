# app/models.py
from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text, Float, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

# ---------- USER ----------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    is_admin = Column(Boolean, default=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)

    driver = relationship("Driver", back_populates="users", foreign_keys=[driver_id])
    tax_profiles = []  # reserved for future state-tax feature

# ---------- DRIVER ----------
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
    users = relationship("User", back_populates="driver")

# ---------- TRIP ----------
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
    duration_minutes = Column(Integer, nullable=True)
    driver = relationship("Driver", back_populates="trips")

# ---------- EXPENSE ----------
class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    category = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    driver = relationship("Driver", back_populates="expenses")

# ---------- DAILY LOG ----------
class DailyLog(Base):
    __tablename__ = "daily_logs"
    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(Date, nullable=False)
    odo_start = Column(Float, nullable=True)
    odo_end = Column(Float, nullable=True)
    minutes_driven = Column(Integer, nullable=True)
    total_earned = Column(Float, default=0.0)
    platform = Column(String(50), nullable=True)
    trips_count = Column(Integer, nullable=True)
    driver = relationship("Driver", back_populates="daily_logs")

# ---------- FUEL LOG ----------
class FuelLog(Base):
    __tablename__ = "fuel_logs"
    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    odometer = Column(Float, nullable=True)
    gallons = Column(Float, nullable=True)
    total_paid = Column(Float, nullable=True)
    vendor = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    driver = relationship("Driver", back_populates="fuel_logs")
