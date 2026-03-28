"""
Optional Arena webhook receiver.

Receives change notifications from Arena PLM and triggers
incremental sync for affected items.
"""

import logging

from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhook", tags=["webhook"])


@router.post("/arena")
async def arena_webhook(request: Request) -> dict:
    """Receive an Arena PLM change notification.

    Expected payload structure (Arena-specific, may vary):
    {
        "event": "item.updated",
        "item": {"guid": "...", "number": "..."},
        "changes": [{"field": "...", "oldValue": "...", "newValue": "..."}]
    }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    item = payload.get("item", {})
    guid = item.get("guid", "")

    logger.info("Webhook received: event=%s, item=%s", event, guid)

    # TODO: Trigger incremental sync for the affected item
    # This requires access to the sync engine via app state

    return {"status": "received", "event": event, "guid": guid}
