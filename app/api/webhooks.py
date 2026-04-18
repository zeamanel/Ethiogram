# app/api/webhooks.py
from fastapi import APIRouter

router = APIRouter(tags=["webhooks"])

# TODO: implement POST /webhook/{token_hash} message gateway
