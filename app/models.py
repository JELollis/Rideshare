# app/models.py
from __future__ import annotations
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from app.db import Base
from datetime import datetime

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    car = Column(String(200))  # legacy single-car label (kept for back-compat)
    platform = Column(String(200))  # legacy single platform (kept for back-compat)
    platforms_csv = Column(Text, default="")  # NEW: comma-separated platforms

    # relationships
    users = relationship("User", back_populates="driver", cascade="all,delete-orphan")
    vehicles = relationship("Vehicle", back_populates="driver", cascade="all,delete-orphan")
    trips = relationship("Trip", back_populates="driver", cascade="all,delete-orphan")
    daily_logs = relationship("DailyLog", back_populates="driver", cascade="all,delete-orphan")
    fuels = relationship("Fuel", back_populates="driver", cascade="all,delete-orphan")

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    name = Column(String(200), nullable=False)  # e.g., "Jeep", "Civic"
    make = Column(String(100))
    model = Column(String(100))
    year = Column(String(10))
    plate = Column(String(32))
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver", back_populates="vehicles")
    trips = relationship("Trip", back_populates="vehicle")
    fuels = relationship("Fuel", back_populates="vehicle")
    daily_logs = relationship("DailyLog", back_populates="vehicle")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    is_admin = Column(Boolean, default=False)

    driver = relationship("Driver", back_populates="users")

class Trip(Base):
    __tablename__ = "trips"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)  # NEW
    date = Column(DateTime, nullable=False)
    platform = Column(String(100))
    fare = Column(Float, default=0.0)
    tip = Column(Float, default=0.0)
    bonus = Column(Float, default=0.0)
    miles = Column(Float, default=0.0)
    duration_minutes = Column(Integer, default=0)

    driver = relationship("Driver", back_populates="trips")
    vehicle = relationship("Vehicle", back_populates="trips")

class DailyLog(Base):
    __tablename__ = "daily_logs"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)  # NEW
    date = Column(Date, nullable=False)
    odo_start = Column(Float, default=0.0)
    odo_end = Column(Float, default=0.0)
    minutes_driven = Column(Integer, default=0)
    total_earned = Column(Float, default=0.0)
    platform = Column(String(100))
    trips_count = Column(Integer)

    driver = relationship("Driver", back_populates="daily_logs")
    vehicle = relationship("Vehicle", back_populates="daily_logs")

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    category = Column(String(100), nullable=False)
    amount = Column(Float, default=0.0)
    notes = Column(String(500))
    driver = relationship("Driver")

class Fuel(Base):
    __tablename__ = "fuels"
    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)  # NEW
    date = Column(DateTime, nullable=False)
    odometer = Column(Float, nullable=False)  # at time of fill
    gallons = Column(Float, nullable=False)
    total_paid = Column(Float, nullable=False)
    vendor = Column(String(200))
    notes = Column(String(500))

    driver = relationship("Driver", back_populates="fuels")
    vehicle = relationship("Vehicle", back_populates="fuels")
