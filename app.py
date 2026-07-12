from flask import Flask, render_template, request, redirect, url_for, flash
import csv
import json
import os
import tempfile
import logging
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "3GOjZcWEkK7WhZi0e2hruXiyQyP_22m5zDkokcu46HEDFS8ZM")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BINDER_FILE = os.environ.get("BINDER_FILE", "binder.json")

SET_NAMES = {
    "001": "The-First-Chapter",
    "002": "Rise-of-the-Floodborn",
    "003": "Into-the-Inklands",
    "004": "Ursulas-Return",
    "005": "Shimmering-Skies",
    "006": "Illumineers-Quest",
    "007": "Archazia's-Island",
    "008": "Azurite-Sea",
    "009": "Chapter-9",
    "010": "Chapter-10",
}

_price_cache = {}


# -----------------------------
# Load & Save Binder
# -----------------------------
def load_binder():
    if not os.path.exists(BINDER_FILE):
        return []
    try:
        with open(BINDER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s: %s", BINDER_FILE, exc)
        return []


def save_binder(binder):
    directory = os.path.dirname(os.path.abspath(BINDER_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".binder_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(binder, f, indent=4)
        os.replace(tmp_path, BINDER_FILE)
    except OSError as exc:
        logger.error("Failed to save %s: %s", BINDER_FILE, exc)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# -----------------------------
# Lorcana API Helpers
# -----------------------------
def get_lorcana_image(set_number, card_number, name):
    try:
        url = (
            "https://api.lorcana-api.com/cards/fetch"
            f"?displayonly=Image;Set_Num;Card_Num;Name"
            f"&search=Set_Num={set_number},Card_Num={card_number}"
        )
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("Image", "https://cdn.disneylorcana.com/images/lorcana-logo.png")
    except Exception as e:
        logger.warning("Image fetch failed: %s", e)
    return "https://cdn.disneylorcana.com/images/lorcana-logo.png"


def get_cardmarket_set_name(set_number):
    return SET_NAMES.get(set_number, "Unknown-Set")


def make_card_id(set_number, card_number, variant):
    return f"{set_number}-{card_number}-{variant}"


def fetch_cardmarket_price(cardmarket_id, name):
    cache_key = (cardmarket_id, name)
    if cache_key in _price_cache:
        return _price_cache[cache_key]
    eur = round((len(name) % 7) + 0.5, 2)
    _price_cache[cache_key] = eur
    return eur


def convert_eur_to_gbp(eur):
    return round(eur * 0.85, 2)


# -----------------------------
# Helpers
# -----------------------------
def merge_or_add_card(binder, new_card):
    for card in binder:
        if card["id"] == new_card["id"]:
            card["quantity"] += new_card["quantity"]
            return
    binder.append(new_card)


def parse_positive_int(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number.")
    if parsed < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return parsed


def parse_non_negative_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number.")
    if parsed < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return round(parsed, 2)


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def binder_view():
    binder = load_binder()
    collection_total = 0.0

    for card in binder:
        eur = fetch_cardmarket_price(card.get("cardmarket_id"), card.get("name", ""))
        card["cardmarket_price_eur"] = eur
        card["cardmarket_price_gbp"] = convert_eur_to_gbp(eur)
        card.setdefault("wishlist", False)
        collection_total += card.get("quantity", 0) * card.get("my_price_gbp", 0)

    name_filter = request.args.get("name", "").strip().lower()
    set_filter = request.args.get("set", "").strip()
    wishlist_only = request.args.get("wishlist", "") == "on"

    filtered = [
        card for card in binder
        if (not name_filter or name_filter in card.get("name", "").lower())
        and (not set_filter or set_filter == card.get("set"))
        and (not wishlist_only or card.get("wishlist"))
    ]

    sort_by = request.args.get("sort", "name")
    reverse = request.args.get("order", "asc") == "desc"
    sort_keys = {
        "price": lambda c: c.get("my_price_gbp", 0),
        "quantity": lambda c: c.get("quantity", 0),
        "name": lambda c: c.get("name", "").lower(),
    }
    filtered.sort(key=sort_keys.get(sort_by, sort_keys["name"]), reverse=reverse)

    return render_template("binder.html", cards=filtered, collection_total=round(collection_total, 2))


@app.route("/add", methods=["GET", "POST"])
def add_card():
    if request.method == "POST":
        set_number = request.form.get("set", "").strip()
        card_number = request.form.get("card_number", "").strip()
        name = request.form.get("name", "").strip()

        if not set_number or not card_number or not name:
            flash("Set, card number, and name are all required.", "error")
            return render_template("add_card.html", form=request.form)

        try:
            quantity = parse_positive_int(request.form.get("quantity"), "Quantity")
            my_price_gbp = parse_non_negative_float(request.form.get("my_price_gbp"), "Price")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("add_card.html", form=request.form)

        binder = load_binder()
        card_id = make_card_id(set_number, card_number, "normal")

        new_card = {
            "id": card_id,
            "name": name,
            "set": set_number,
            "image_url": get_lorcana_image(set_number, card_number, name),
            "quantity": quantity,
            "my_price_gbp": my_price_gbp,
            "cardmarket_id": get_cardmarket_set_name(set_number),
            "wishlist": False,
        }

        merge_or_add_card(binder, new_card)
        save_binder(binder)
        flash(f"Added {name} to your binder.", "success")
        return redirect(url_for("binder_view"))

    return render_template("add_card.html", form={})


@app.route("/edit/<card_id>", methods=["GET", "POST"])
def edit_card(card_id):
    binder = load_binder()
    card = next((c for c in binder if c["id"] == card_id), None)

    if not card:
        flash("That card could not be found.", "error")
        return redirect(url_for("binder_view"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        set_number = request.form.get("set", "").strip()

        if not name or not set_number:
            flash("Name and set are required.", "error")
            return render_template("edit_card.html", card=card)

        try:
            quantity = parse_positive_int(request.form.get("quantity"), "Quantity")
            my_price_gbp = parse_non_negative_float(request.form.get("my_price_gbp"), "Price")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("edit_card.html", card=card)

        card["name"] = name
        card["set"] = set_number
        card["quantity"] = quantity
        card["my_price_gbp"] = my_price_gbp
        save_binder(binder)
        flash(f"Updated {name}.", "success")
        return redirect(url_for("binder_view"))

    return render_template("edit_card.html", card=card)


@app.route("/delete/<card_id>", methods=["POST"])
def delete_card(card_id):
    binder = load_binder()
    remaining = [c for c in binder if c["id"] != card_id]

    if len(remaining) == len(binder):
        flash("That card could not be found.", "error")
    else:
        save_binder(remaining)
        flash("Card deleted.", "success")

    return redirect(url_for("binder_view"))


@app.route("/wishlist/<card_id>", methods=["POST"])
def toggle_wishlist(card_id):
    binder = load_binder()
    for card in binder:
        if card["id"] == card_id:
            card["wishlist"] = not card.get("wishlist", False)
            save_binder(binder)
            return redirect(url_for("binder_view"))

    flash("That card could not be found.", "error")
    return redirect(url_for("binder_view"))


@app.route("/remove", methods=["GET", "POST"])
def remove_cards():
    binder = load_binder()
    if request.method == "POST":
        ids_to_remove = set(request.form.getlist("remove_ids"))
        if not ids_to_remove:
            flash("No cards were selected.", "error")
            return render_template("remove.html", cards=binder)

        remaining = [c for c in binder if c["id"] not in ids_to_remove]
        removed_count = len(binder) - len(remaining)
        save_binder(remaining)
        flash(f"Removed {removed_count} card(s).", "success")
        return redirect(url_for("binder_view"))

    return render_template("remove.html", cards=binder)


REQUIRED_CSV_COLUMNS = {"Set Number", "Card Number", "Variant", "Name", "Count"}

@app.route("/upload", methods=["GET", "POST"])
def upload_csv():
    if request.method == "POST":
        file = request.files.get("csvfile")
        if not file or not file.filename:
            flash("Please choose a CSV file to upload.", "error")
            return render_template("upload.html")

        if not file.filename.lower().endswith(".csv"):
            flash("File must be a .csv file.", "error")
            return render_template("upload.html")

        try:
            csv_data = file.read().decode("utf-8-sig").splitlines()
        except UnicodeDecodeError:
            flash("Could not read that file — make sure it's a UTF-8 CSV.", "error")
            return render_template("upload.html")

        reader = csv.DictReader(csv_data)
        if reader.fieldnames is None or not REQUIRED_CSV_COLUMNS.issubset(set(reader.fieldnames)):
            missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
            flash(f"CSV is missing required column(s): {', '.join(sorted(missing))}", "error")
            return render_template("upload.html")

        binder = load_binder()
        added, skipped = 0, 0

        for row_num, row in enumerate(reader, start=2):
            try:
                set_number = row["Set Number"].strip()
                card_number = row["Card Number"].strip()
                variant = row["Variant"].strip()
                name = row["Name"].strip()
                count = parse_positive_int(row["Count"], "Count")

                if not (set_number and card_number and variant and name):
                    raise ValueError("one or more fields were empty")

                card_id = make_card_id(set_number, card_number, variant)
                new_card = {
                    "id": card_id,
                    "name": name,
                    "set": set_number,
                    "image_url": get_lorcana_image(set_number, card_number, name),
                    "quantity": count,
                    "my_price_gbp": 0.00,
                    "cardmarket_id": get_cardmarket_set_name(set_number),
                    "wishlist": False,
                }
                merge_or_add_card(binder, new_card)
                added += 1
            except (ValueError, KeyError) as exc:
                logger.warning("Skipping CSV row %d: %s", row_num, exc)
                skipped += 1

        save_binder(binder)
        flash(f"Imported {added} card(s); skipped {skipped} invalid row(s).", "warning" if skipped else "success")
        return redirect(url_for("binder_view"))

    return render_template("upload.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=True)