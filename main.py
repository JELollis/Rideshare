# main.py (project root)
from datetime import datetime, date
from pathlib import Path
import hashlib
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI, Depends, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine, Base
from app import models, crud
from app.config import settings

# ---------- DB bootstrap ----------
Base.metadata.create_all(bind=engine)

# IMPORTANT: use a name that's NOT "app" to avoid "app.main:app" confusion
api = FastAPI(title="Rideshare Profit Tracker")
api.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="rs_session", max_age=60*60*24*30)
templates = Jinja2Templates(directory="app/templates")

# ---------- session helpers ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_pw(p: str) -> str:
    return hashlib.sha256(p.encode("utf-8")).hexdigest()

def get_current_user(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("uid")
    if not uid:
        return None
    return db.get(models.User, uid)

def require_login(request: Request, db: Session) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401)
    return user

# ---------- health ----------
@api.get("/__ping", response_class=PlainTextResponse)
def ping():
    return "pong"

@api.get("/health")
def health():
    return {"ok": True}

# ---------- DASHBOARD (de-dupes daily vs trip) ----------
@api.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    q_drivers = db.query(models.Driver)
    if user and not user.is_admin and user.driver_id:
        q_drivers = q_drivers.filter(models.Driver.id == user.driver_id)
    drivers = q_drivers.all()

    # collect daily logs and trips; de-dupe trips that fall on a date with a daily log for the same driver
    dls = db.query(models.DailyLog).all()
    trps = db.query(models.Trip).all()
    daily_dates = {(dl.driver_id, dl.date.isoformat()) for dl in dls}
    kept_trips = [t for t in trps if (t.driver_id, t.date.date().isoformat()) not in daily_dates]

    gross = sum((dl.total_earned or 0.0) for dl in dls) + sum(((t.fare or 0)+(t.tip or 0)+(t.bonus or 0)) for t in kept_trips)
    miles = sum(max(0.0, (dl.odo_end or 0)-(dl.odo_start or 0)) for dl in dls) + sum((t.miles or 0.0) for t in kept_trips)
    expenses = sum((e.amount or 0.0) for e in db.query(models.Expense).all())
    net = gross - expenses

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user,
        "gross": gross, "expenses": expenses, "net": net, "miles": miles
    })

# ---------- AUTH / SETUP ----------
@api.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@api.post("/login")
def login_post(request: Request, db: Session = Depends(get_db), username: str = Form(...), password: str = Form(...)):
    u = db.query(models.User).filter(models.User.username == username).first()
    if not u or u.password_hash != hash_pw(password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=400)
    request.session["uid"] = u.id
    return RedirectResponse("/", status_code=303)

@api.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@api.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_db)):
    if db.query(models.User).count() > 0:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request})

@api.post("/setup")
def setup_post(
    request: Request, db: Session = Depends(get_db),
    username: str = Form(...), password: str = Form(...), driver_name: str = Form(...)
):
    if db.query(models.User).count() > 0:
        return RedirectResponse("/", status_code=303)
    driver = models.Driver(name=driver_name)
    db.add(driver); db.commit(); db.refresh(driver)
    user = models.User(username=username, password_hash=hash_pw(password), is_admin=True, driver_id=driver.id)
    db.add(user); db.commit(); db.refresh(user)
    request.session["uid"] = user.id
    return RedirectResponse("/", status_code=303)

# ---------- USERS (admin; regular sees self) ----------
@api.get("/users/ui", response_class=HTMLResponse)
def users_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.is_admin:
        users = db.query(models.User).order_by(models.User.username).all()
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    else:
        users = [user]
        drivers = db.query(models.Driver).filter(models.Driver.id == user.driver_id).all()
    return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": users, "drivers": drivers, "error": None})

@api.post("/users/ui")
def users_create(
    request: Request, db: Session = Depends(get_db),
    username: str = Form(...), password: str = Form(...),
    driver_id: Optional[int] = Form(None), is_admin: Optional[bool] = Form(False)
):
    user = require_login(request, db)
    if not user.is_admin:
        raise HTTPException(403)
    if db.query(models.User).filter(models.User.username == username).first():
        return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": db.query(models.User).all(), "drivers": db.query(models.Driver).all(), "error": "Username exists"}, status_code=400)
    u = models.User(username=username, password_hash=hash_pw(password), is_admin=bool(is_admin), driver_id=driver_id)
    db.add(u); db.commit()
    return RedirectResponse("/users/ui", status_code=303)

@api.get("/users/edit/{uid}", response_class=HTMLResponse)
def users_edit_form(uid: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    target = db.get(models.User, uid)
    if not target: raise HTTPException(404)
    if not user.is_admin and user.id != target.id: raise HTTPException(403)
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    return templates.TemplateResponse("users_edit.html", {"request": request, "user": user, "target": target, "drivers": drivers})

@api.post("/users/edit/{uid}")
def users_edit(uid: int, request: Request, db: Session = Depends(get_db),
               username: str = Form(...), new_password: str = Form(""),
               driver_id: Optional[int] = Form(None), is_admin: Optional[bool] = Form(False)):
    user = require_login(request, db)
    target = db.get(models.User, uid)
    if not target: raise HTTPException(404)
    if not user.is_admin and user.id != target.id: raise HTTPException(403)
    target.username = username
    if new_password.strip():
        target.password_hash = hash_pw(new_password.strip())
    if user.is_admin:
        target.is_admin = bool(is_admin)
        target.driver_id = driver_id
    db.commit()
    return RedirectResponse("/users/ui", status_code=303)

# ---------- DRIVERS (list/create/edit) ----------
@api.get("/drivers/ui", response_class=HTMLResponse)
def drivers_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    items = db.query(models.Driver).order_by(models.Driver.name).all() if user.is_admin else db.query(models.Driver).filter(models.Driver.id==user.driver_id).all()
    return templates.TemplateResponse("drivers.html", {"request": request, "user": user, "drivers": items})

@api.post("/drivers/ui")
def drivers_create(request: Request, db: Session = Depends(get_db), name: str = Form(...), car: str = Form(""), platform: str = Form("")):
    user = require_login(request, db)
    if not user.is_admin:
        raise HTTPException(403)
    crud.create_driver(db, type("X", (), {"name": name, "car": (car or None), "platform": (platform or None)})())
    return RedirectResponse("/drivers/ui", status_code=303)

@api.get("/drivers/edit/{did}", response_class=HTMLResponse)
def drivers_edit_form(did: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    d = db.get(models.Driver, did)
    if not d: raise HTTPException(404)
    if not user.is_admin and user.driver_id != d.id: raise HTTPException(403)
    return templates.TemplateResponse("drivers.html", {"request": request, "user": user, "drivers": [d], "edit_driver": d})

@api.post("/drivers/edit/{did}")
def drivers_edit(did: int, request: Request, db: Session = Depends(get_db), name: str = Form(...), car: str = Form(""), platform: str = Form("")):
    user = require_login(request, db)
    d = db.get(models.Driver, did)
    if not d: raise HTTPException(404)
    if not user.is_admin and user.driver_id != d.id: raise HTTPException(403)
    d.name, d.car, d.platform = name, (car or None), (platform or None)
    db.commit()
    return RedirectResponse("/drivers/ui", status_code=303)

# ---------- TRIPS ----------
@api.get("/trips/ui", response_class=HTMLResponse)
def trips_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
        trips = db.query(models.Trip).order_by(models.Trip.date.desc()).limit(200).all()
    else:
        drivers = db.query(models.Driver).filter(models.Driver.id == user.driver_id).all()
        trips = db.query(models.Trip).filter(models.Trip.driver_id == user.driver_id).order_by(models.Trip.date.desc()).limit(200).all()
    return templates.TemplateResponse("trips.html", {"request": request, "user": user, "drivers": drivers, "trips": trips})

@api.post("/trips/ui")
def trips_create(request: Request, db: Session = Depends(get_db),
                 driver_id: int = Form(...), date: str = Form(...), platform: str = Form(""),
                 fare: float = Form(...), tip: float = Form(0.0), bonus: float = Form(0.0),
                 miles: float = Form(...), duration_minutes: Optional[int] = Form(None)):
    user = require_login(request, db)
    if not user.is_admin and user.driver_id != driver_id:
        raise HTTPException(403)
    dt = datetime.fromisoformat(date)
    t = models.Trip(driver_id=driver_id, date=dt, platform=(platform or None),
                    fare=fare, tip=tip, bonus=bonus, miles=miles, duration_minutes=duration_minutes)
    db.add(t); db.commit()
    return RedirectResponse("/trips/ui", status_code=303)

@api.get("/trips/edit/{tid}", response_class=HTMLResponse)
def trips_edit_form(tid: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    t = db.get(models.Trip, tid)
    if not t: raise HTTPException(404)
    if not user.is_admin and user.driver_id != t.driver_id: raise HTTPException(403)
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    return templates.TemplateResponse("trip_edit.html", {"request": request, "user": user, "trip": t, "drivers": drivers})

@api.post("/trips/edit/{tid}")
def trips_edit(tid: int, request: Request, db: Session = Depends(get_db),
               driver_id: int = Form(...), date: str = Form(...), platform: str = Form(""),
               fare: float = Form(...), tip: float = Form(0.0), bonus: float = Form(0.0),
               miles: float = Form(...), duration_minutes: Optional[int] = Form(None)):
    user = require_login(request, db)
    t = db.get(models.Trip, tid)
    if not t: raise HTTPException(404)
    if not user.is_admin and user.driver_id != t.driver_id: raise HTTPException(403)
    t.driver_id = driver_id
    t.date = datetime.fromisoformat(date)
    t.platform = platform or None
    t.fare, t.tip, t.bonus, t.miles, t.duration_minutes = fare, tip, bonus, miles, duration_minutes
    db.commit()
    return RedirectResponse("/trips/ui", status_code=303)

# ---------- EXPENSES ----------
@api.get("/expenses/ui", response_class=HTMLResponse)
def expenses_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
        expenses = db.query(models.Expense).order_by(models.Expense.date.desc()).limit(200).all()
    else:
        drivers = db.query(models.Driver).filter(models.Driver.id == user.driver_id).all()
        expenses = db.query(models.Expense).filter(models.Expense.driver_id == user.driver_id).order_by(models.Expense.date.desc()).limit(200).all()
    return templates.TemplateResponse("expenses.html", {"request": request, "user": user, "drivers": drivers, "expenses": expenses})

@api.post("/expenses/ui")
def expenses_create(request: Request, db: Session = Depends(get_db),
                    driver_id: int = Form(...), date: str = Form(...), category: str = Form(...),
                    amount: float = Form(...), notes: str = Form("")):
    user = require_login(request, db)
    if not user.is_admin and user.driver_id != driver_id:
        raise HTTPException(403)
    e = models.Expense(driver_id=driver_id, date=datetime.fromisoformat(date), category=category, amount=amount, notes=(notes or None))
    db.add(e); db.commit()
    return RedirectResponse("/expenses/ui", status_code=303)

@api.get("/expenses/edit/{eid}", response_class=HTMLResponse)
def expenses_edit_form(eid: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    e = db.get(models.Expense, eid)
    if not e: raise HTTPException(404)
    if not user.is_admin and user.driver_id != e.driver_id: raise HTTPException(403)
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    return templates.TemplateResponse("expense_edit.html", {"request": request, "user": user, "expense": e, "drivers": drivers})

@api.post("/expenses/edit/{eid}")
def expenses_edit(eid: int, request: Request, db: Session = Depends(get_db),
                  driver_id: int = Form(...), date: str = Form(...), category: str = Form(...),
                  amount: float = Form(...), notes: str = Form("")):
    user = require_login(request, db)
    e = db.get(models.Expense, eid)
    if not e: raise HTTPException(404)
    if not user.is_admin and user.driver_id != e.driver_id: raise HTTPException(403)
    e.driver_id = driver_id
    e.date = datetime.fromisoformat(date)
    e.category = category
    e.amount = amount
    e.notes = notes or None
    db.commit()
    return RedirectResponse("/expenses/ui", status_code=303)

# ---------- DAILY ----------
@api.get("/daily/ui", response_class=HTMLResponse)
def daily_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
        logs = db.query(models.DailyLog).order_by(models.DailyLog.date.desc()).limit(200).all()
    else:
        drivers = db.query(models.Driver).filter(models.Driver.id == user.driver_id).all()
        logs = db.query(models.DailyLog).filter(models.DailyLog.driver_id == user.driver_id).order_by(models.DailyLog.date.desc()).limit(200).all()
    return templates.TemplateResponse("daily.html", {"request": request, "user": user, "drivers": drivers, "logs": logs})

@api.post("/daily/ui")
def daily_create(request: Request, db: Session = Depends(get_db),
                 driver_id: int = Form(...), date: str = Form(...),
                 odo_start: Optional[float] = Form(None), odo_end: Optional[float] = Form(None),
                 hours: Optional[int] = Form(0), mins: Optional[int] = Form(0),
                 total_earned: float = Form(...), platform: str = Form(""), trips_count: Optional[int] = Form(None)):
    user = require_login(request, db)
    if not user.is_admin and user.driver_id != driver_id: raise HTTPException(403)
    minutes = (hours or 0) * 60 + (mins or 0)
    dl = models.DailyLog(driver_id=driver_id, date=datetime.fromisoformat(date).date(),
                         odo_start=odo_start, odo_end=odo_end, minutes_driven=minutes,
                         total_earned=total_earned, platform=(platform or None), trips_count=trips_count)
    db.add(dl); db.commit()
    return RedirectResponse("/daily/ui", status_code=303)

@api.get("/daily/edit/{lid}", response_class=HTMLResponse)
def daily_edit_form(lid: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    l = db.get(models.DailyLog, lid)
    if not l: raise HTTPException(404)
    if not user.is_admin and user.driver_id != l.driver_id: raise HTTPException(403)
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    return templates.TemplateResponse("daily_edit.html", {"request": request, "user": user, "log": l, "drivers": drivers})

@api.post("/daily/edit/{lid}")
def daily_edit(lid: int, request: Request, db: Session = Depends(get_db),
               driver_id: int = Form(...), date: str = Form(...),
               odo_start: Optional[float] = Form(None), odo_end: Optional[float] = Form(None),
               hours: Optional[int] = Form(0), mins: Optional[int] = Form(0),
               total_earned: float = Form(...), platform: str = Form(""), trips_count: Optional[int] = Form(None)):
    user = require_login(request, db)
    l = db.get(models.DailyLog, lid)
    if not l: raise HTTPException(404)
    if not user.is_admin and user.driver_id != l.driver_id: raise HTTPException(403)
    l.driver_id = driver_id
    l.date = datetime.fromisoformat(date).date()
    l.odo_start, l.odo_end = odo_start, odo_end
    l.minutes_driven = (hours or 0) * 60 + (mins or 0)
    l.total_earned, l.platform, l.trips_count = total_earned, (platform or None), trips_count
    db.commit()
    return RedirectResponse("/daily/ui", status_code=303)

# ---------- FUEL ----------
@api.get("/fuel/ui", response_class=HTMLResponse)
def fuel_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
        fuels = db.query(models.FuelLog).order_by(models.FuelLog.date.desc()).limit(200).all()
    else:
        drivers = db.query(models.Driver).filter(models.Driver.id == user.driver_id).all()
        fuels = db.query(models.FuelLog).filter(models.FuelLog.driver_id == user.driver_id).order_by(models.FuelLog.date.desc()).limit(200).all()
    return templates.TemplateResponse("fuel.html", {"request": request, "user": user, "drivers": drivers, "fuels": fuels})

@api.post("/fuel/ui")
def fuel_create(request: Request, db: Session = Depends(get_db),
                driver_id: int = Form(...), date: str = Form(...),
                odometer: Optional[float] = Form(None), gallons: Optional[float] = Form(None),
                total_paid: Optional[float] = Form(None), vendor: str = Form(""), notes: str = Form("")):
    user = require_login(request, db)
    if not user.is_admin and user.driver_id != driver_id: raise HTTPException(403)
    f = models.FuelLog(driver_id=driver_id, date=datetime.fromisoformat(date),
                       odometer=odometer, gallons=gallons, total_paid=total_paid,
                       vendor=(vendor or None), notes=(notes or None))
    db.add(f); db.commit()
    return RedirectResponse("/fuel/ui", status_code=303)

@api.get("/fuel/edit/{fid}", response_class=HTMLResponse)
def fuel_edit_form(fid: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    f = db.get(models.FuelLog, fid)
    if not f: raise HTTPException(404)
    if not user.is_admin and user.driver_id != f.driver_id: raise HTTPException(403)
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    return templates.TemplateResponse("fuel_edit.html", {"request": request, "user": user, "fuel": f, "drivers": drivers})

@api.post("/fuel/edit/{fid}")
def fuel_edit(fid: int, request: Request, db: Session = Depends(get_db),
              driver_id: int = Form(...), date: str = Form(...),
              odometer: Optional[float] = Form(None), gallons: Optional[float] = Form(None),
              total_paid: Optional[float] = Form(None), vendor: str = Form(""), notes: str = Form("")):
    user = require_login(request, db)
    f = db.get(models.FuelLog, fid)
    if not f: raise HTTPException(404)
    if not user.is_admin and user.driver_id != f.driver_id: raise HTTPException(403)
    f.driver_id = driver_id
    f.date = datetime.fromisoformat(date)
    f.odometer, f.gallons, f.total_paid = odometer, gallons, total_paid
    f.vendor, f.notes = (vendor or None), (notes or None)
    db.commit()
    return RedirectResponse("/fuel/ui", status_code=303)

# ---------- REPORTS (de-dupe: trips on dates with a DailyLog are skipped) ----------
def _miles_from_daily(l: models.DailyLog) -> float:
    if l.odo_start is None or l.odo_end is None: return 0.0
    return max(0.0, (l.odo_end or 0) - (l.odo_start or 0))

def _trip_total(t: models.Trip) -> float:
    return float(t.fare or 0) + float(t.tip or 0) + float(t.bonus or 0)

@api.get("/reports/ui", response_class=HTMLResponse)
def reports_ui(
    request: Request, db: Session = Depends(get_db),
    start: Optional[date] = Query(None), end: Optional[date] = Query(None),
    driver_id: Optional[int] = Query(None)
):
    user = require_login(request, db)
    today = date.today()
    if not start: start = date(today.year, 1, 1)
    if not end:   end   = date(today.year, 12, 31)

    # scope drivers
    if user.is_admin and driver_id:
        driver_ids = [driver_id]
    elif user.driver_id:
        driver_ids = [user.driver_id]
    else:
        driver_ids = []

    # load
    dls = db.query(models.DailyLog).filter(models.DailyLog.driver_id.in_(driver_ids),
                                           models.DailyLog.date >= start, models.DailyLog.date <= end).all()
    trps = db.query(models.Trip).filter(models.Trip.driver_id.in_(driver_ids),
                                        models.Trip.date >= datetime.combine(start, datetime.min.time()),
                                        models.Trip.date <= datetime.combine(end, datetime.max.time())).all()

    # de-dupe: skip trips on dates that have a DailyLog
    daily_dates = {(dl.driver_id, dl.date.isoformat()) for dl in dls}
    kept = [t for t in trps if (t.driver_id, t.date.date().isoformat()) not in daily_dates]

    gross = sum((dl.total_earned or 0.0) for dl in dls) + sum(_trip_total(t) for t in kept)
    miles = sum(_miles_from_daily(dl) for dl in dls) + sum(float(t.miles or 0) for t in kept)
    mins  = sum(int(dl.minutes_driven or 0) for dl in dls) + sum(int(t.duration_minutes or 0) for t in kept)
    plat  = defaultdict(float)
    for dl in dls:
        plat[(dl.platform or "Unspecified")] += float(dl.total_earned or 0)
    for t in kept:
        plat[(t.platform or "Unspecified")] += _trip_total(t)

    return templates.TemplateResponse("reports.html", {
        "request": request, "user": user,
        "start": start, "end": end,
        "gross": gross, "miles": miles, "minutes": mins, "platform_income": dict(plat),
        "driver_id": (driver_ids[0] if driver_ids else None),
        "drivers": db.query(models.Driver).order_by(models.Driver.name).all() if user.is_admin else []
    })
