from __future__ import annotations

import csv
import ast
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seatplan import i18n
from seatplan.app import create_app
from seatplan.db import Database
from conftest import csrf, login, make_event, make_plan
from seatplan.domain import new_id


def write_catalog(path, messages):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "context", "en", "ro"], lineterminator="\n")
        writer.writeheader()
        for source, romanian in messages.items():
            writer.writerow({"source": source, "context": "Test message", "en": source, "ro": romanian})


def test_catalog_completeness_and_numbered_placeholders(tmp_path, monkeypatch):
    path = tmp_path / "messages.csv"
    monkeypatch.setattr(i18n, "CATALOG_PATH", path)
    write_catalog(path, {"Hello {0}!": "Salut, {0}!", "Goodbye": ""})
    assert i18n.catalog().languages == ("en",)
    write_catalog(path, {"Hello {0}!": "Salut, {0}!", "Goodbye": "La revedere"})
    assert i18n.catalog().languages == ("en", "ro")
    assert i18n.catalog().translate("Hello Ada!", "ro") == "Salut, Ada!"
    assert i18n.catalog().translate("Hello {0}!", "ro", "Ada") == "Salut, Ada!"
    write_catalog(path, {"Hello {0}!": "Salut!"})
    with pytest.raises(ValueError, match="placeholders"):
        i18n.catalog()


def test_new_complete_language_column_becomes_available(tmp_path, monkeypatch):
    path = tmp_path / "messages.csv"
    monkeypatch.setattr(i18n, "CATALOG_PATH", path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "context", "en", "fr"], lineterminator="\n")
        writer.writeheader()
        writer.writerow({"source": "Book seats", "context": "Navigation", "en": "Book seats", "fr": "Réserver des places"})
    assert i18n.catalog().languages == ("en", "fr")
    assert i18n.catalog().translate("Book seats", "fr") == "Réserver des places"


def test_language_selection_localizes_email_and_errors(tmp_path, monkeypatch, settings):
    path = tmp_path / "messages.csv"
    monkeypatch.setattr(i18n, "CATALOG_PATH", path)
    write_catalog(path, {
        "Your Seatplan sign-in link": "Legătura de autentificare Seatplan",
        "Open this link to sign in:": "Deschideți această legătură pentru autentificare:",
        "It expires in 15 minutes and can be used once.": "Expiră în 15 minute și poate fi folosită o singură dată.",
        "Do not forward this email.": "Nu redirecționați acest mesaj.",
        "If you did not request it, ignore this message.": "Dacă nu l-ați cerut, ignorați mesajul.",
        "A sign-in email has been queued. Check your inbox and spam folder.": "Mesajul de autentificare a fost pus în coadă.",
        "Event not found.": "Evenimentul nu a fost găsit.",
        "This language is not available yet.": "Această limbă nu este disponibilă încă.",
    })
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/i18n").json()["languages"] == ["en", "ro"]
        assert client.get("/api/events/missing", headers={"Accept-Language": "en-US,en;q=0.9,ro;q=0.8"}).json()["detail"] == "Event not found."
        assert client.get("/api/events/missing", headers={"Accept-Language": "ro-RO, en;q=0.8"}).json()["detail"] == "Evenimentul nu a fost găsit."
        client.cookies.set("seatplan_lang", "ro")
        headers = csrf(client)
        response = client.post("/api/auth/request", json={"email": "person@example.org"}, headers=headers)
        assert response.json()["message"] == "Mesajul de autentificare a fost pus în coadă."
        with sqlite3.connect(settings.db_path) as connection:
            subject, body = connection.execute("SELECT subject,body FROM outbox ORDER BY created DESC LIMIT 1").fetchone()
        assert subject == "Legătura de autentificare Seatplan"
        assert "Deschideți această legătură" in body
        token = re.search(r"#confirm=([A-Za-z0-9_-]+)", body).group(1)
        assert client.post("/api/auth/confirm", json={"token": token}, headers=headers).status_code == 200
        with sqlite3.connect(settings.db_path) as connection:
            assert connection.execute("SELECT locale FROM users WHERE email='person@example.org'").fetchone()[0] == "ro"
        assert client.get("/api/events/missing").json()["detail"] == "Evenimentul nu a fost găsit."
        assert client.post("/api/me/language", json={"language": "en"}, headers=csrf(client)).json()["language"] == "en"
        with sqlite3.connect(settings.db_path) as connection:
            assert connection.execute("SELECT locale FROM users WHERE email='person@example.org'").fetchone()[0] == "en"


def test_existing_database_gets_locale_without_losing_users(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users(id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created REAL NOT NULL)")
        connection.execute("INSERT INTO users VALUES('old-user','old@example.org',1)")
        connection.execute("PRAGMA user_version = 1")
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT email,locale FROM users WHERE id='old-user'").fetchone() == ("old@example.org", "en")


def test_booking_email_uses_saved_language(tmp_path, monkeypatch, settings):
    path = tmp_path / "messages.csv"
    monkeypatch.setattr(i18n, "CATALOG_PATH", path)
    write_catalog(path, {
        "Reservation confirmed": "Rezervare confirmată",
        "Current seats in this reservation:": "Locurile din această rezervare:",
        "Reference: {0}": "Referință: {0}",
        "Sign in to view or cancel:": "Autentificați-vă pentru vizualizare sau anulare:",
        "Keep this message private. This is a reservation, not proof of identity.": "Păstrați acest mesaj privat.",
    })
    app = create_app(settings)
    plan_id, seats = make_plan(app.state.db, settings)
    event_id = make_event(app.state.db, plan_id)
    with TestClient(app) as client:
        client.cookies.set("seatplan_lang", "ro")
        headers = login(client, app)
        result = client.post(f"/api/events/{event_id}/book", json={"seats": [seats[0]["id"]], "request_key": new_id()}, headers=headers)
        assert result.status_code == 200, result.text
    with app.state.db.read() as connection:
        subject, body = connection.execute("SELECT subject,body FROM outbox WHERE subject LIKE 'Rezervare confirmată:%'").fetchone()
    assert subject == "Rezervare confirmată: Concert"
    assert "Locurile din această rezervare:" in body
    assert "Referință: " in body


def test_explicit_translation_calls_have_csv_rows():
    root = Path(__file__).resolve().parents[1]
    sources = set(i18n.catalog().rows)
    requested = set()
    for path in (root / "seatplan").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"tr", "translate"} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                requested.add(node.args[0].value)
    for path in (root / "seatplan/static").glob("*.js"):
        for pair in re.findall(r'(?:translate|confirmTranslated)\((?:"([^"\\]*(?:\\.[^"\\]*)*)"|\x27([^\x27\\]*(?:\\.[^\x27\\]*)*)\x27)', path.read_text()):
            requested.add(pair[0] or pair[1])
    requested.update(re.findall(r"tr\('([^']+)'", (root / "seatplan/templates/print.html").read_text()))
    assert requested <= sources


def test_browser_switches_existing_and_new_text_without_reloading():
    executable = shutil.which("chromium") or shutil.which("chromium-browser")
    if executable is None:
        pytest.skip("Chromium is not installed")
    from playwright.sync_api import sync_playwright

    source = (Path(__file__).resolve().parents[1] / "seatplan/static/i18n.js").read_text()
    source = source.replace("export async function", "async function").replace("export function", "function")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content('<html><head><title>Seatplan · Reservations</title></head><body data-base=""><label hidden><select id="language-select"></select></label><button id="booking" aria-label="Book seats">Book seats</button><span id="event-name" translate="no">Book seats</span><div id="dynamic"></div></body></html>')
        page.evaluate("""window.fetch = async () => ({ok: true, json: async () => ({
            languages: ['en', 'ro'], messages: {
                'Book seats': {ro: 'Rezervă locuri'},
                'Reserve {0} seats': {ro: 'Rezervă {0} locuri'},
                'Seatplan · Reservations': {ro: 'Seatplan · Rezervări'}
            }
        })});""")
        page.add_script_tag(content=source)
        page.evaluate("initializeTranslations('')")
        page.locator("#language-select").select_option("ro")
        assert page.locator("#booking").inner_text() == "Rezervă locuri"
        assert page.locator("#booking").get_attribute("aria-label") == "Rezervă locuri"
        assert page.locator("#event-name").inner_text() == "Book seats"
        page.evaluate('document.querySelector("#dynamic").innerHTML = "<button>Reserve 2 seats</button>"')
        page.get_by_role("button", name="Rezervă 2 locuri").wait_for()
        assert page.title() == "Seatplan · Rezervări"
        page.locator("#language-select").select_option("en")
        assert page.locator("#booking").inner_text() == "Book seats"
        assert page.locator("#dynamic button").inner_text() == "Reserve 2 seats"
        browser.close()
