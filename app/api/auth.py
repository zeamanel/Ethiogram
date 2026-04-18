# app/api/auth.py
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])

# TODO: implement login, register, refresh, logout endpoints
