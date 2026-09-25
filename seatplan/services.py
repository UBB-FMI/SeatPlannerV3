from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter

from fastapi import HTTPException

from .config import Settings
from .db import Database, json_dump
from .domain import BookRequest, EventEdit, OverrideRequest, PlanSave, excluded, new_id, validate_rotated_bounds
from .i18n import catalog
from .mail import enqueue
from .security import Identity, ensure_user


def audit(connection: sqlite3.Connection, actor: str, action: str, object_id: str, details: object) -> None:
    connection.execute("INSERT INTO audit(actor,action,object_id,details,created) VALUES(?,?,?,?,?)", (actor, action, object_id, json_dump(details), time.time()))


def get_plan(connection: sqlite3.Connection, plan_id: str) -> dict:
    row = connection.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Seat plan not found.")
    plan = dict(row)
    plan["pages"] = json.loads(plan["pages"])
    plan["zones"] = json.loads(plan["zones"])
    return plan


def plan_seats(connection: sqlite3.Connection, plan_id: str) -> list[dict]:
    return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM seats WHERE plan_id=? ORDER BY section,row_name,label,id", (plan_id,))]


def effective_blocked(seat: dict, zones: list[dict]) -> bool:
    return seat["blocked"] or excluded(seat, zones)


def seat_name(seat: dict) -> str:
    return " / ".join(value for value in [seat["section"], seat["row"], seat["label"]] if value)


def plan_summary(connection: sqlite3.Connection, plan: dict) -> dict:
    seats = plan_seats(connection, plan["id"])
    return {
        "total": len(seats), "unreviewed": sum(not seat["reviewed"] for seat in seats),
        "blocked": sum(effective_blocked(seat, plan["zones"]) for seat in seats),
    }


def save_plan(db: Database, plan_id: str, request: PlanSave, actor: str) -> dict:
    with db.transaction() as connection:
        plan = get_plan(connection, plan_id)
        if plan["state"] != "draft":
            raise HTTPException(409, "Only draft plans can be edited. Duplicate a published plan to revise it.")
        if plan["revision"] != request.revision:
            raise HTTPException(409, "Another administrator changed this plan. Export your work before reloading.")
        seats = [seat.model_dump() for seat in request.seats]
        zones = [zone.model_dump() for zone in request.zones]
        identities = [(seat["section"].casefold(), seat["row"].casefold(), seat["label"].casefold()) for seat in seats]
        if len(set(identities)) != len(identities):
            duplicates = [" / ".join(key) for key, count in Counter(identities).items() if count > 1]
            raise HTTPException(422, "Duplicate section / row / label: " + "; ".join(duplicates[:5]))
        if len({seat["id"] for seat in seats}) != len(seats):
            raise HTTPException(422, "Duplicate internal seat IDs.")
        page_count = len(plan["pages"])
        if any(item["page"] >= page_count for item in seats + zones):
            raise HTTPException(422, "A seat or exclusion references a page that is not in the PDF.")
        for seat in seats:
            page = plan["pages"][seat["page"]]
            try:
                validate_rotated_bounds(seat, page["width"], page["height"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        old_ids = {row[0] for row in connection.execute("SELECT id FROM seats WHERE plan_id=?", (plan_id,))}
        for seat in seats:
            if seat["id"] not in old_ids and connection.execute("SELECT 1 FROM seats WHERE id=?", (seat["id"],)).fetchone():
                raise HTTPException(422, "An imported seat ID belongs to another plan. Use the import function to assign new IDs.")
        connection.execute("DELETE FROM seats WHERE plan_id=?", (plan_id,))
        connection.executemany("INSERT INTO seats(id,plan_id,section,row_name,label,payload) VALUES(?,?,?,?,?,?)", [(seat["id"], plan_id, seat["section"], seat["row"], seat["label"], json_dump(seat)) for seat in seats])
        connection.execute("UPDATE plans SET name=?,notes=?,zones=?,revision=revision+1 WHERE id=?", (request.name, request.notes, json_dump(zones), plan_id))
        audit(connection, actor, "plan.save", plan_id, {"previous_revision": request.revision, "seats": len(seats), "zones": len(zones)})
        return {"revision": request.revision + 1, "saved": len(seats)}


def publish_plan(db: Database, plan_id: str, revision: int, actor: str) -> dict:
    with db.transaction() as connection:
        plan = get_plan(connection, plan_id)
        if plan["state"] != "draft" or plan["revision"] != revision:
            raise HTTPException(409, "The draft has changed or is already published. Reload it.")
        summary = plan_summary(connection, plan)
        if summary["total"] == 0 or summary["unreviewed"] > 0:
            raise HTTPException(422, f"Review every candidate before publishing. Unreviewed: {summary['unreviewed']}.")
        if summary["blocked"] == summary["total"]:
            raise HTTPException(422, "There must be at least one reservable seat.")
        connection.execute("UPDATE plans SET state='published',published=?,revision=revision+1 WHERE id=?", (time.time(), plan_id))
        audit(connection, actor, "plan.publish", plan_id, summary)
        return {"state": "published", **summary}


def clone_plan(db: Database, plan_id: str, actor: str) -> dict:
    with db.transaction() as connection:
        source = get_plan(connection, plan_id)
        if source["state"] not in {"draft", "published"}:
            raise HTTPException(409, "The PDF must finish processing first.")
        clone_id = new_id()
        connection.execute("INSERT INTO plans(id,name,pdf_path,sha256,pages,zones,state,notes,created,parent_id) VALUES(?,?,?,?,?,?,'draft',?,?,?)", (clone_id, source["name"] + " (revision)", source["pdf_path"], source["sha256"], json_dump(source["pages"]), json_dump(source["zones"]), source["notes"], time.time(), plan_id))
        for seat in plan_seats(connection, plan_id):
            seat["id"] = new_id()
            connection.execute("INSERT INTO seats VALUES(?,?,?,?,?,?)", (seat["id"], clone_id, seat["section"], seat["row"], seat["label"], json_dump(seat)))
        audit(connection, actor, "plan.clone", clone_id, {"source": plan_id})
        return {"id": clone_id}


def get_event(connection: sqlite3.Connection, event_id: str) -> dict:
    row = connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Event not found.")
    return dict(row)


def event_snapshot(connection: sqlite3.Connection, event_id: str, identity: Identity | None = None, admin: bool = False) -> dict:
    event = get_event(connection, event_id)
    plan = get_plan(connection, event["plan_id"])
    seats = plan_seats(connection, plan["id"])
    allocation_rows = connection.execute("SELECT a.*,b.email,b.user_id FROM allocations a LEFT JOIN bookings b ON b.id=a.booking_id WHERE a.event_id=?", (event_id,)).fetchall()
    allocations = {row["seat_id"]: dict(row) for row in allocation_rows}
    for seat in seats:
        allocation = allocations.get(seat["id"])
        seat["status"] = "blocked" if effective_blocked(seat, plan["zones"]) else "free"
        seat["mine"] = False
        if allocation:
            seat["status"] = allocation["status"]
            seat["mine"] = identity is not None and identity.user_id is not None and allocation["user_id"] == identity.user_id
            if admin:
                seat["allocation"] = allocation
        if admin is False:
            # Notes can contain internal information. Public API never emits allocations or emails.
            seat.pop("note", None)
            seat.pop("score", None)
    return {"event": event, "plan": {"id": plan["id"], "name": plan["name"], "pages": plan["pages"]}, "seats": seats}


def booking_notice(connection: sqlite3.Connection, settings: Settings, booking_id: str, heading: str) -> None:
    booking = connection.execute("SELECT b.*,e.title,e.starts_at FROM bookings b JOIN events e ON e.id=b.event_id WHERE b.id=?", (booking_id,)).fetchone()
    if booking is None or not booking["email"]:
        return
    seats = [json.loads(row[0]) for row in connection.execute("SELECT s.payload FROM allocations a JOIN seats s ON s.id=a.seat_id WHERE a.booking_id=? ORDER BY s.section,s.row_name,s.label", (booking_id,))]
    user = connection.execute("SELECT locale FROM users WHERE id=?", (booking["user_id"],)).fetchone() if booking["user_id"] else None
    language = user["locale"] if user else "en"
    tr = lambda source, *values: catalog().translate(source, language, *values)
    seat_lines = "\n".join("  " + seat_name(seat) for seat in seats) or "  " + tr("No seats remain in this reservation.")
    body = f"{tr(heading)}\n\n{booking['title']}\n{booking['starts_at']}\n{tr('Reference: {0}', booking_id)}\n\n{tr('Current seats in this reservation:')}\n{seat_lines}\n\n{tr('Sign in to view or cancel:')}\n{settings.public_url}/?event={booking['event_id']}\n\n{tr('Keep this message private. This is a reservation, not proof of identity.')}\n"
    enqueue(connection, booking["email"], f"{tr(heading)}: {booking['title']}", body)


def book(db: Database, settings: Settings, event_id: str, request: BookRequest, identity: Identity) -> dict:
    seat_ids = sorted(set(request.seats))
    if len(seat_ids) != len(request.seats):
        raise HTTPException(422, "A seat was selected twice.")
    fingerprint = hashlib.sha256(json_dump(seat_ids).encode()).hexdigest()
    with db.transaction() as connection:
        previous = connection.execute("SELECT id,request_hash FROM bookings WHERE user_id=? AND event_id=? AND request_key=?", (identity.user_id, event_id, request.request_key)).fetchone()
        if previous:
            if previous["request_hash"] != fingerprint:
                raise HTTPException(409, "That request key was already used for a different selection.")
            return {"id": previous["id"], "replayed": True}
        event = get_event(connection, event_id)
        if event["status"] != "open":
            raise HTTPException(409, "Booking is closed for this event.")
        plan = get_plan(connection, event["plan_id"])
        if plan["state"] != "published":
            raise HTTPException(409, "This seating plan is not published.")
        seats = {seat["id"]: seat for seat in plan_seats(connection, plan["id"])}
        for sid in seat_ids:
            if sid not in seats:
                raise HTTPException(422, "A selected seat does not belong to this event.")
            if effective_blocked(seats[sid], plan["zones"]) or seats[sid]["reviewed"] is False:
                raise HTTPException(409, f"Not reservable: {seat_name(seats[sid])}.")
            if connection.execute("SELECT 1 FROM allocations WHERE event_id=? AND seat_id=?", (event_id, sid)).fetchone():
                raise HTTPException(409, f"Already reserved or blocked: {seat_name(seats[sid])}. Nothing was booked.")
        count = connection.execute("SELECT COUNT(*) FROM allocations a JOIN bookings b ON b.id=a.booking_id WHERE a.event_id=? AND b.user_id=?", (event_id, identity.user_id)).fetchone()[0]
        if event["max_per_user"] > 0 and count + len(seat_ids) > event["max_per_user"]:
            raise HTTPException(409, f"This event allows {event['max_per_user']} seats per email. You already have {count}.")
        bid = new_id()
        connection.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,?)", (bid, event_id, identity.user_id, identity.email, time.time(), request.request_key, fingerprint))
        connection.executemany("INSERT INTO allocations VALUES(?,?,'reserved',?,'',?)", [(event_id, sid, bid, time.time()) for sid in seat_ids])
        connection.execute("UPDATE events SET revision=revision+1 WHERE id=?", (event_id,))
        audit(connection, identity.email or "", "booking.create", bid, {"event": event_id, "seats": seat_ids})
        booking_notice(connection, settings, bid, "Reservation confirmed")
        return {"id": bid, "replayed": False}


def cancel_booking(db: Database, settings: Settings, booking_id: str, seat_ids: list[str], identity: Identity) -> dict:
    with db.transaction() as connection:
        booking = connection.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if booking is None or booking["user_id"] != identity.user_id:
            raise HTTPException(404, "Reservation not found.")
        current = {row[0] for row in connection.execute("SELECT seat_id FROM allocations WHERE booking_id=?", (booking_id,))}
        requested = set(seat_ids) if seat_ids else current
        if not requested.issubset(current):
            raise HTTPException(409, "This reservation has changed. Refresh your reservations.")
        if requested:
            connection.executemany("DELETE FROM allocations WHERE booking_id=? AND seat_id=?", [(booking_id, sid) for sid in requested])
            connection.execute("UPDATE events SET revision=revision+1 WHERE id=?", (booking["event_id"],))
            audit(connection, identity.email or "", "booking.cancel", booking_id, {"seats": sorted(requested)})
            booking_notice(connection, settings, booking_id, "Reservation updated")
        return {"cancelled": len(requested)}


def override(db: Database, settings: Settings, event_id: str, request: OverrideRequest, identity: Identity) -> dict:
    with db.transaction() as connection:
        event = get_event(connection, event_id)
        if event["revision"] != request.revision:
            raise HTTPException(409, "The live seating state changed. Refresh and review your override again.")
        plan = get_plan(connection, event["plan_id"])
        seats = {seat["id"]: seat for seat in plan_seats(connection, event["plan_id"])}
        requested = sorted(set(request.seats))
        if any(sid not in seats for sid in requested):
            raise HTTPException(422, "A selected seat does not belong to this event.")
        if request.action == "reserved" and request.allow_excluded is False and any(effective_blocked(seats[sid], plan["zones"]) for sid in requested):
            raise HTTPException(422, "The selection includes plan-excluded seats. Explicitly allow an excluded-seat override to continue.")
        affected = set()
        before = []
        for sid in requested:
            row = connection.execute("SELECT * FROM allocations WHERE event_id=? AND seat_id=?", (event_id, sid)).fetchone()
            if row:
                before.append(dict(row))
                if row["booking_id"]:
                    affected.add(row["booking_id"])
            connection.execute("DELETE FROM allocations WHERE event_id=? AND seat_id=?", (event_id, sid))
        bid = None
        if request.action == "reserved":
            bid = new_id()
            user_id = ensure_user(connection, request.email) if request.email else None
            connection.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,?)", (bid, event_id, user_id, request.email, time.time(), new_id(), "admin"))
        if request.action != "free":
            connection.executemany("INSERT INTO allocations VALUES(?,?,?,?,?,?)", [(event_id, sid, request.action, bid, request.reason, time.time()) for sid in requested])
        connection.execute("UPDATE events SET revision=revision+1 WHERE id=?", (event_id,))
        audit(connection, identity.email or "", "seats.override", event_id, {"before": before, "action": request.action, "seats": requested, "reason": request.reason, "email": request.email, "allow_excluded": request.allow_excluded})
        for old_bid in affected:
            booking_notice(connection, settings, old_bid, "Reservation changed by the organizer")
        if bid is not None:
            booking_notice(connection, settings, bid, "Reservation confirmed by the organizer")
        return {"changed": len(requested), "revision": request.revision + 1}


def save_event(db: Database, settings: Settings, request: EventEdit, actor: str, event_id: str | None = None) -> dict:
    with db.transaction() as connection:
        plan = get_plan(connection, request.plan_id)
        if plan["state"] != "published":
            raise HTTPException(422, "Choose a published seating plan.")
        eid = event_id or new_id()
        if event_id is None:
            connection.execute("INSERT INTO events(id,title,starts_at,description,plan_id,status,max_per_user,created) VALUES(?,?,?,?,?,?,?,?)", (eid, request.title, request.starts_at, request.description, request.plan_id, request.status, request.max_per_user, time.time()))
        else:
            event = get_event(connection, event_id)
            if event["revision"] != request.revision:
                raise HTTPException(409, "The event has changed. Reload before saving.")
            if event["plan_id"] != request.plan_id:
                # Never silently map by proximity or row order. Only exact stable seat names.
                new_seats = {(seat["section"], seat["row"], seat["label"]): seat for seat in plan_seats(connection, request.plan_id)}
                allocations = connection.execute("SELECT a.*,s.payload FROM allocations a JOIN seats s ON s.id=a.seat_id WHERE a.event_id=?", (event_id,)).fetchall()
                mapping = []
                for allocation in allocations:
                    old_seat = json.loads(allocation["payload"])
                    new_seat = new_seats.get((old_seat["section"], old_seat["row"], old_seat["label"]))
                    if new_seat is None or (allocation["status"] == "reserved" and effective_blocked(new_seat, plan["zones"])):
                        raise HTTPException(409, f"Cannot migrate occupied seat {seat_name(old_seat)}. Keep its exact name and availability in the new plan, or explicitly resolve this allocation first.")
                    mapping.append((allocation, new_seat["id"]))
                connection.execute("DELETE FROM allocations WHERE event_id=?", (event_id,))
                for old, new_sid in mapping:
                    connection.execute("INSERT INTO allocations VALUES(?,?,?,?,?,?)", (event_id, new_sid, old["status"], old["booking_id"], old["note"], time.time()))
                audit(connection, actor, "event.plan_migrate", event_id, {"from": event["plan_id"], "to": request.plan_id, "allocations": len(mapping)})
            connection.execute("UPDATE events SET title=?,starts_at=?,description=?,plan_id=?,status=?,max_per_user=?,revision=revision+1 WHERE id=?", (request.title, request.starts_at, request.description, request.plan_id, request.status, request.max_per_user, event_id))
            # Inform existing attendees of changed event details / plan, not merely open/close/quota changes.
            if any(event[key] != getattr(request, key) for key in ("title", "starts_at", "description", "plan_id")):
                for row in connection.execute("SELECT DISTINCT booking_id FROM allocations WHERE event_id=? AND booking_id IS NOT NULL", (event_id,)):
                    booking_notice(connection, settings, row[0], "Event details updated")
        audit(connection, actor, "event.save", eid, request.model_dump())
        return {"id": eid}
