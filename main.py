# main.py — Streamlit Curtain Fabric Calculator using MongoDB + GridFS for images
"""
Requirements:
  pip install streamlit pymongo dnspython reportlab

Environment variables:
  MONGO_URI  - required. e.g. "mongodb+srv://user:pass@cluster.mongodb.net"
  MONGO_DB   - optional, default: "fabric_app"
  USE_GRIDFS - optional; defaults to enabled (1). Set to "0" to disable.

This version forces GridFS by default (USE_GRIDFS=True), but respects USE_GRIDFS env var if explicitly "0".
"""

import os
import io
import uuid
import math
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import streamlit as st
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

# Mongo imports
try:
    from pymongo import MongoClient, DESCENDING
    from bson import ObjectId
    from gridfs import GridFS
except Exception:
    MongoClient = None
    GridFS = None
    ObjectId = None

# -------------------------
# Configuration
# -------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://kunal-qd:Password_5202@cluster0.zem6dyp.mongodb.net/?appName=Cluster0")
MONGO_DBNAME = os.getenv("MONGO_DB", "fabric_app")
# Default to GridFS enabled — if user explicitly sets USE_GRIDFS=0 then we disable
USE_GRIDFS = not (os.getenv("USE_GRIDFS", "").strip() in ("0", "false", "False"))

# local fallback for image storage (only used when GridFS disabled)
BASE_DIR = Path.cwd() / "saved_data"
IMAGES_DIR = BASE_DIR / "images"
BASE_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------
# Utility / calc helpers
# -------------------------
def is_number(x):
    return isinstance(x, (int, float))

def calculate_height_factor(height):
    return round((height + 14) / 39, 2)

def calculate_quantity(stitch, width, height):
    h = calculate_height_factor(height)
    if stitch == "Pleated":
        return round(width / 18) * h
    if stitch == "Ripple":
        return round(width / 20) * h
    if stitch == "Eyelet":
        return round(width / 24) * h
    if stitch == 'Roman Blinds 48"':
        panels = math.ceil(width / 44)
        return round(panels * h)
    if stitch == 'Roman Blinds 54"':
        panels = math.ceil(width / 50)
        return round(panels * h)
    return 0

def calculate_track_ft(width, stitch):
    if stitch.startswith("Roman") or stitch == "Blinds (Regular)":
        return None
    return math.ceil((width / 12) * 2) / 2

def ceil_to_half(value):
    return math.ceil(value * 2) / 2

def calculate_sqft_for_roman_or_regular(width, height, stitch):
    if stitch.startswith("Roman") or stitch == "Blinds (Regular)":
        w_ft = ceil_to_half(width / 12)
        h_ft = ceil_to_half(height / 12)
        return w_ft * h_ft
    return None

def calculate_panels(stitch, width):
    if stitch == "Pleated":
        return round(width / 18)
    if stitch == "Ripple":
        return round(width / 20)
    if stitch == "Eyelet":
        return round(width / 24)
    return None

# -------------------------
# MongoDB + GridFS helpers
# -------------------------
_db_client = None
_db = None
_fs = None

def get_mongo_client():
    if MongoClient is None:
        raise RuntimeError("Missing dependency: pymongo/gridfs. Install with `pip install pymongo dnspython`.")
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI environment variable is not set. Set it before running the app.")
    return MongoClient(MONGO_URI)

def ensure_db():
    """Initialize global _db and _fs (GridFS). Returns (db, fs_or_none)."""
    global _db_client, _db, _fs, USE_GRIDFS
    if _db is None:
        _db_client = get_mongo_client()
        _db = _db_client[MONGO_DBNAME]
        # ensure indexes
        try:
            _db.customers.create_index("phone")
            _db.customers.create_index("name")
            _db.orders.create_index([("customer_id", 1), ("created_at", -1)])
        except Exception:
            pass
        # GridFS
        if USE_GRIDFS:
            if GridFS is None:
                raise RuntimeError("GridFS not available. Ensure `gridfs` is importable (install pymongo).")
            _fs = GridFS(_db)
        else:
            _fs = None
    return _db, _fs

def save_images_gridfs(order_id, images_bytes, fs):
    """Save images into GridFS, return list of refs like 'gridfs:<id>'."""
    refs = []
    for b in images_bytes:
        if not b:
            continue
        fid = fs.put(b)
        refs.append(f"gridfs:{str(fid)}")
    return refs

def save_images_filesystem(order_id, images_bytes):
    """Fallback image saving to local filesystem. Return relative refs."""
    refs = []
    order_dir = IMAGES_DIR / order_id
    order_dir.mkdir(parents=True, exist_ok=True)
    for idx, b in enumerate(images_bytes):
        if not b:
            continue
        fname = f"{idx+1}.jpg"
        fpath = order_dir / fname
        with open(fpath, "wb") as f:
            f.write(b)
        refs.append(str(Path("images") / order_id / fname))
    return refs

def save_images_for_order(order_id, images_bytes, fs=None):
    if fs:
        return save_images_gridfs(order_id, images_bytes, fs)
    else:
        return save_images_filesystem(order_id, images_bytes)

def load_image_bytes_from_ref(ref, fs=None):
    """Load bytes for ref 'gridfs:<id>' or filesystem relative path."""
    if isinstance(ref, str) and ref.startswith("gridfs:"):
        if not fs:
            return None
        fid = ref.split("gridfs:")[1]
        try:
            return fs.get(ObjectId(fid)).read()
        except Exception:
            return None
    else:
        # assume relative path under BASE_DIR
        abs_path = BASE_DIR / (ref or "")
        if abs_path.exists():
            with open(abs_path, "rb") as f:
                return f.read()
        return None

# -------------------------
# Mongo CRUD equivalents
# -------------------------
def save_customer_if_new(name, phone, address, showroom=""):
    db, _ = ensure_db()
    phone_s = (phone or "").strip()
    name_s = (name or "").strip()
    if phone_s:
        doc = db.customers.find_one({"phone": phone_s})
        if doc:
            return str(doc["_id"])
    if name_s:
        doc = db.customers.find_one({"name": name_s})
        if doc:
            return str(doc["_id"])
    now = datetime.utcnow()
    res = db.customers.insert_one({
        "name": name_s,
        "phone": phone_s,
        "address": (address or "").strip(),
        "showroom": (showroom or "").strip(),
        "created_at": now
    })
    return str(res.inserted_id)

def update_customer_db(cid, name, phone, address, showroom=""):
    db, _ = ensure_db()
    try:
        db.customers.update_one({"_id": ObjectId(cid)}, {"$set": {
            "name": (name or "").strip(),
            "phone": (phone or "").strip(),
            "address": (address or "").strip(),
            "showroom": (showroom or "").strip()
        }})
    except Exception:
        # fallback when cid isn't an ObjectId (shouldn't normally happen)
        db.customers.update_one({"_id": cid}, {"$set": {
            "name": (name or "").strip(),
            "phone": (phone or "").strip(),
            "address": (address or "").strip(),
            "showroom": (showroom or "").strip()
        }})

def search_customers(term):
    db, _ = ensure_db()
    t = (term or "").strip()
    if t:
        regex = {"$regex": t, "$options": "i"}
        cursor = db.customers.find({"$or": [{"name": regex}, {"phone": regex}]})
    else:
        cursor = db.customers.find()
    cursor = cursor.sort("created_at", DESCENDING).limit(100)
    results = []
    for d in cursor:
        results.append({
            "id": str(d["_id"]),
            "name": d.get("name",""),
            "phone": d.get("phone",""),
            "address": d.get("address",""),
            "showroom": d.get("showroom",""),
            "created_at": d.get("created_at")
        })
    return results

def save_order(customer_id, entries):
    db, fs = ensure_db()
    order_id = str(uuid.uuid4())
    docs_entries = []
    for e in entries:
        images = e.get("Images", []) or []
        refs = save_images_for_order(order_id, images, fs=fs)
        docs_entries.append({
            "Window": e.get("Window"),
            "Stitch Type": e.get("Stitch Type"),
            "Width (inches)": e.get("Width (inches)"),
            "Height (inches)": e.get("Height (inches)"),
            "Quantity": e.get("Quantity"),
            "Track (ft)": e.get("Track (ft)"),
            "SQFT": e.get("SQFT"),
            "Panels": e.get("Panels"),
            "Images": refs
        })
    now = datetime.utcnow()
    db.orders.insert_one({
        "_id": order_id,
        "customer_id": customer_id,
        "created_at": now,
        "entries": docs_entries
    })
    return order_id

def get_orders_for_customer(cid):
    db, _ = ensure_db()
    docs = db.orders.find({"customer_id": cid}).sort("created_at", DESCENDING).limit(50)
    orders = []
    for d in docs:
        orders.append({
            "id": d.get("_id"),
            "created_at": d.get("created_at"),
            "entries": d.get("entries", [])
        })
    return orders

# -------------------------
# Load order into session (loads image bytes from GridFS)
# -------------------------
def load_order_into_session(order):
    db, fs = ensure_db()
    loaded = []
    for e in order.get("entries", []):
        stitch = e.get("Stitch Type")
        width = float(e.get("Width (inches)", 0) or 0)
        height = float(e.get("Height (inches)", 0) or 0)
        images_bytes = []
        for ref in e.get("Images", []) or []:
            b = load_image_bytes_from_ref(ref, fs=fs)
            if b:
                images_bytes.append(b)
        loaded.append({
            "Window": e.get("Window"),
            "Stitch Type": stitch,
            "Width (inches)": width,
            "Height (inches)": height,
            "Quantity": calculate_quantity(stitch, width, height),
            "Track (ft)": calculate_track_ft(width, stitch),
            "SQFT": calculate_sqft_for_roman_or_regular(width, height, stitch),
            "Panels": calculate_panels(stitch, width),
            "Images": images_bytes
        })
    st.session_state["entries"] = loaded

# -------------------------
# PDF generator
# -------------------------
def generate_pdf_bytes(customer, entries):
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b><font size=18>Order Form</font></b>", styles["Title"]))
    story.append(Spacer(1, 12))

    showroom_text = f"<b>Showroom:</b> {customer.get('showroom','')}" if customer.get("showroom") else ""

    story.append(Paragraph(f"""
        <b>Name:</b> {customer['name']}<br/>
        <b>Phone:</b> {customer['phone']}<br/>
        <b>Address:</b> {customer['address']}<br/>
        {showroom_text}<br/>
        <b>Date:</b> {datetime.now().strftime("%d-%m-%Y %H:%M")}
    """, styles["Normal"]))
    story.append(Spacer(1, 12))

    # group entries by base window name (before " - Layer")
    groups = defaultdict(list)
    for e in entries:
        win_full = e.get("Window", "Window")
        if " - Layer " in win_full:
            base = win_full.split(" - Layer ")[0]
        else:
            base = win_full
        groups[base].append(e)

    for base, rows in groups.items():
        story.append(Paragraph(f"<b>{base}</b>", styles["Heading2"]))
        # Lining: take from first row if present
        lining = rows[0].get("Lining", "No Lining")
        story.append(Paragraph(f"<b>Lining:</b> {lining}", styles["Normal"]))
        story.append(Spacer(1, 6))

        for r in sorted(rows, key=lambda x: x.get("Layer", 1) if isinstance(x.get("Layer", None), int) else 1):
            layer_label = f"Layer {r.get('Layer',1)}"
            story.append(Paragraph(f"<b>{layer_label}</b>", styles["Heading3"]))
            tbl = Table([
                ["Stitch Type", r.get("Stitch Type")],
                ["Width", r.get("Width (inches)")],
                ["Height", r.get("Height (inches)")],
                ["Quantity", r.get("Quantity")],
                ["Track (ft)", r.get("Track (ft)")],
                ["SQFT", r.get("SQFT")],
                ["Panels", r.get("Panels")]
            ], colWidths=[150, 300])
            tbl.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.5, colors.grey),
                                     ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke)]))
            story.append(tbl)
            story.append(Spacer(1, 6))

        # images: show first non-empty image set across rows (since images duplicated)
        shown = False
        for r in rows:
            imgs = r.get("Images", []) or []
            if imgs:
                for img in imgs:
                    try:
                        img_buff = io.BytesIO(img)
                        story.append(RLImage(img_buff, width=2.5*inch, height=2.5*inch))
                        story.append(Spacer(1, 6))
                        shown = True
                    except Exception:
                        pass
                if shown:
                    break

        story.append(Spacer(1, 10))

    pdf.build(story)
    buffer.seek(0)
    return buffer.read()

# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(layout="wide", page_title="Curtain Fabric Calculator (MongoDB GridFS)")

# session state defaults
if "entries" not in st.session_state:
    st.session_state["entries"] = []
if "current_customer" not in st.session_state:
    st.session_state["current_customer"] = None
if "edit_index" not in st.session_state:
    st.session_state["edit_index"] = None

st.title("Curtain Fabric Calculator — MongoDB + GridFS")

# Load existing customer UI
load_choice = st.radio(
    "Load existing customer?",
    ["No — Start a new order", "Yes — Load existing customer"],
    index=0,
    label_visibility="collapsed"
)

if load_choice.startswith("Yes"):
    st.subheader("Search Customer")
    term = st.text_input("Enter name or phone to search")

    if st.button("Search"):
        try:
            results = search_customers(term)
        except Exception as ex:
            st.error(f"Search failed: {ex}")
            results = []

        if not results:
            st.warning("No results found.")
        else:
            formatted = [f"{r['id']} — {r['name']} — {r['phone']} — {r.get('showroom','')}" for r in results]
            sel_index = st.selectbox("Select customer", list(range(len(formatted))), format_func=lambda i: formatted[i])
            sel = results[sel_index]
            cid = sel["id"]
            st.session_state["current_customer"] = {
                "id": cid,
                "name": sel.get("name",""),
                "phone": sel.get("phone",""),
                "address": sel.get("address",""),
                "showroom": sel.get("showroom","")
            }

            # load latest order if present
            try:
                orders = get_orders_for_customer(cid)
            except Exception as ex:
                st.error(f"Failed to load orders: {ex}")
                orders = []

            if orders:
                load_order_into_session(orders[0])
                st.success(f"Customer '{sel.get('name','')}' loaded and latest order loaded.")
            else:
                st.info(f"Customer '{sel.get('name','')}' loaded — no saved orders. Start adding windows.")

            st.session_state["edit_index"] = None
            st.rerun()

# Add/Edit Window form
st.header("Add / Edit Window Details")

# Helper to find paired layer indexes by base window name
def find_pair_indexes_by_base(base_name):
    """Return (idx_layer1, idx_layer2) if both present, else (idx_single, None) or (None,None)."""
    idx1 = idx2 = None
    for i, e in enumerate(st.session_state["entries"]):
        w = e.get("Window", "")
        if w == base_name:
            # exact match single-layer window
            idx1 = i
            return idx1, None
        if " - Layer " in w:
            base = w.split(" - Layer ")[0]
            if base == base_name:
                layer_num = w.split(" - Layer ")[1]
                if layer_num.startswith("1"):
                    idx1 = i
                elif layer_num.startswith("2"):
                    idx2 = i
    # If found both, return them (order can vary)
    return idx1, idx2

# Determine edit context
edit_idx = st.session_state.get("edit_index")
e_prefill = None
editing_pair_base = None
pair_indexes = (None, None)
if edit_idx is not None and 0 <= edit_idx < len(st.session_state["entries"]):
    # Determine base name for the selected row, and check whether it's part of a pair
    selected = st.session_state["entries"][edit_idx]
    sel_win = selected.get("Window", "")
    if " - Layer " in sel_win:
        base = sel_win.split(" - Layer ")[0]
    else:
        base = sel_win
    # find pair indexes (could be single)
    idx_a, idx_b = find_pair_indexes_by_base(base)
    pair_indexes = (idx_a, idx_b)
    # If both layers present, set e_prefill to a dict combining info for the window form
    if idx_a is not None and idx_b is not None:
        # We will load main = layer1 (prefer idx with Layer 1), sheer = layer2
        # find which index corresponds to layer1/layer2
        layer1_idx = layer2_idx = None
        for i in (idx_a, idx_b):
            if i is None:
                continue
            w = st.session_state["entries"][i].get("Window","")
            if " - Layer 1" in w:
                layer1_idx = i
            elif " - Layer 2" in w:
                layer2_idx = i
        # fallback: if layer1 not found, pick the lower index as main
        if layer1_idx is None and layer2_idx is not None:
            # pick other as layer1
            layer1_idx = layer2_idx
            layer2_idx = None
        if layer1_idx is not None:
            e_prefill = st.session_state["entries"][layer1_idx].copy()
            editing_pair_base = base
        else:
            e_prefill = st.session_state["entries"][edit_idx].copy()
    else:
        # single-row edit
        e_prefill = st.session_state["entries"][edit_idx].copy()

# Radio key per-window (new vs editing index)
radio_key = f"ui_double_radio_{edit_idx if edit_idx is not None else 'new'}"

# If a previous add requested reset for the new form, apply BEFORE widget creation (allowed)
if st.session_state.get("_reset_new_form", False) and radio_key == "ui_double_radio_new":
    st.session_state[radio_key] = "No"
    try:
        del st.session_state["_reset_new_form"]
    except Exception:
        pass

# If key not initialized, set default value (editing pair -> Yes else No)
if radio_key not in st.session_state:
    if editing_pair_base:
        st.session_state[radio_key] = "Yes"
    else:
        st.session_state[radio_key] = "No"

# Render the per-window radio outside the form so its state updates immediately
st.radio("Double layer?", ["No", "Yes"],
         index=0 if st.session_state.get(radio_key, "No") == "No" else 1,
         key=radio_key,
         horizontal=True)

# Build the form; it will read st.session_state[radio_key] for is_double
with st.form("win_form", clear_on_submit=False):
    # Window name — prefill from e_prefill if editing
    win_name = st.text_input("Window Name", value=e_prefill["Window"] if e_prefill else "")

    # Main layer inputs (prefill from e_prefill)
    st.subheader("Main Layer")
    main_stitch_default = e_prefill["Stitch Type"] if e_prefill else "Pleated"
    main_width_default = e_prefill["Width (inches)"] if e_prefill else 0.0
    main_height_default = e_prefill["Height (inches)"] if e_prefill else 0.0

    main_stitch = st.selectbox("Main layer stitch type", [
        "Pleated", "Ripple", "Eyelet",
        'Roman Blinds 48"', 'Roman Blinds 54"',
        "Blinds (Regular)"
    ], index=["Pleated", "Ripple", "Eyelet",'Roman Blinds 48"','Roman Blinds 54"',"Blinds (Regular)"].index(main_stitch_default) if main_stitch_default in ["Pleated","Ripple","Eyelet",'Roman Blinds 48"','Roman Blinds 54"',"Blinds (Regular)"] else 0, key="main_stitch")

    main_width = st.number_input("Main layer width (inches)", min_value=0.0, value=float(main_width_default or 0.0), step=0.5, key="main_w")
    main_height = st.number_input("Main layer height (inches)", min_value=0.0, value=float(main_height_default or 0.0), step=0.5, key="main_h")

    st.markdown("---")

    # Determine is_double from per-window radio key
    is_double = (st.session_state.get(radio_key, "No") == "Yes")

    # Sheer layer inputs (disabled when not double; mirrored on submit)
    if is_double:
        st.subheader("Sheer Layer (enabled)")
    else:
        st.subheader("Sheer Layer (disabled — will mirror Main layer)")

    # If editing a pair, prefill sheer fields from layer2 entry if present
    sheer_stitch_default = "Pleated"
    sheer_w_default = main_width if not is_double else 0.0
    sheer_h_default = main_height if not is_double else 0.0

    # If editing a pair, try to pull data for layer2
    if editing_pair_base and is_double:
        idx_layer1, idx_layer2 = pair_indexes
        layer2_idx = None
        if idx_layer1 is not None and idx_layer2 is not None:
            for i in (idx_layer1, idx_layer2):
                if i is None: continue
                w = st.session_state["entries"][i].get("Window","")
                if " - Layer 2" in w:
                    layer2_idx = i
                    break
        if layer2_idx is not None:
            sheer_entry = st.session_state["entries"][layer2_idx]
            sheer_stitch_default = sheer_entry.get("Stitch Type", sheer_stitch_default)
            sheer_w_default = sheer_entry.get("Width (inches)", sheer_w_default)
            sheer_h_default = sheer_entry.get("Height (inches)", sheer_h_default)

    sheer_stitch = st.selectbox("Sheer layer stitch type", [
        "Pleated", "Ripple", "Eyelet",
        'Roman Blinds 48"', 'Roman Blinds 54"',
        "Blinds (Regular)"
    ], index=["Pleated","Ripple","Eyelet",'Roman Blinds 48"','Roman Blinds 54"',"Blinds (Regular)"].index(sheer_stitch_default) if sheer_stitch_default in ["Pleated","Ripple","Eyelet",'Roman Blinds 48"','Roman Blinds 54"',"Blinds (Regular)"] else 0, key="sheer_stitch", disabled=not is_double)

    sheer_width = st.number_input("Sheer layer width (inches)", min_value=0.0, value=float(sheer_w_default or 0.0), step=0.5, key="sheer_w", disabled=not is_double)
    sheer_height = st.number_input("Sheer layer height (inches)", min_value=0.0, value=float(sheer_h_default or 0.0), step=0.5, key="sheer_h", disabled=not is_double)

    st.markdown("---")
    # Lining (per-window) inside the form
    lining = st.selectbox("Lining (per window)", ["100% B/o Lining", "Normal Lining", "No Lining"], index=2, key="ui_lining")

    # File uploader — keep the key "uploader" so reset logic remains unchanged
    files = st.file_uploader("Upload Images", accept_multiple_files=True, key="uploader")

    submit_label = "Update Window" if e_prefill else "Add Window"
    add = st.form_submit_button(submit_label)

# Handle submit: Option A - edit BOTH layers together
if add:
    # read images (if any)
    imgs = []
    if files:
        imgs = [f.read() for f in files]
    elif e_prefill:
        # if editing and no new files uploaded, preserve previous images for main
        imgs = e_prefill.get("Images", [])

    # If sheer inputs disabled (not double) mirror main values
    if not is_double:
        sheer_width_val = float(main_width)
        sheer_height_val = float(main_height)
        sheer_stitch_val = main_stitch
    else:
        sheer_width_val = float(st.session_state.get("sheer_w", 0.0)) if "sheer_w" in st.session_state else float(sheer_width)
        sheer_height_val = float(st.session_state.get("sheer_h", 0.0)) if "sheer_h" in st.session_state else float(sheer_height)
        sheer_stitch_val = st.session_state.get("sheer_stitch", sheer_stitch) if "sheer_stitch" in st.session_state else sheer_stitch

    # If editing a pair (Option A), update both rows (or create second if missing)
    if e_prefill and editing_pair_base:
        # find pair indexes again
        idx_a, idx_b = pair_indexes
        # find layer1 and layer2 indexes
        layer1_idx = layer2_idx = None
        for i in (idx_a, idx_b):
            if i is None: continue
            w = st.session_state["entries"][i].get("Window","")
            if " - Layer 1" in w:
                layer1_idx = i
            elif " - Layer 2" in w:
                layer2_idx = i
        # Update/create layer1 (main)
        entry_main = {
            "Window": f"{win_name.strip()} - Layer 1" if is_double else win_name.strip(),
            "Stitch Type": main_stitch,
            "Width (inches)": float(main_width),
            "Height (inches)": float(main_height),
            "Quantity": calculate_quantity(main_stitch, float(main_width), float(main_height)),
            "Track (ft)": calculate_track_ft(float(main_width), main_stitch),
            "SQFT": calculate_sqft_for_roman_or_regular(float(main_width), float(main_height), main_stitch),
            "Panels": calculate_panels(main_stitch, float(main_width)),
            "Lining": lining,
            "Images": imgs,
            "Layer": 1
        }
        if layer1_idx is not None:
            st.session_state["entries"][layer1_idx] = entry_main
        else:
            st.session_state["entries"].append(entry_main)
            layer1_idx = len(st.session_state["entries"]) - 1

        # Handle layer2
        if is_double:
            entry_sheer = {
                "Window": f"{win_name.strip()} - Layer 2",
                "Stitch Type": sheer_stitch_val,
                "Width (inches)": float(sheer_width_val),
                "Height (inches)": float(sheer_height_val),
                "Quantity": calculate_quantity(sheer_stitch_val, float(sheer_width_val), float(sheer_height_val)),
                "Track (ft)": calculate_track_ft(float(sheer_width_val), sheer_stitch_val),
                "SQFT": calculate_sqft_for_roman_or_regular(float(sheer_width_val), float(sheer_height_val), sheer_stitch_val),
                "Panels": calculate_panels(sheer_stitch_val, float(sheer_width_val)),
                "Lining": lining,
                "Images": imgs,
                "Layer": 2
            }
            if layer2_idx is not None:
                st.session_state["entries"][layer2_idx] = entry_sheer
            else:
                st.session_state["entries"].append(entry_sheer)
        else:
            # If user turned off double while editing, remove any existing layer2
            if layer2_idx is not None:
                st.session_state["entries"].pop(layer2_idx)
        st.session_state["edit_index"] = None
        st.success("Window (both layers) updated.")
    elif e_prefill and not editing_pair_base:
        # editing a single-row window (not part of a pair)
        entry = {
            "Window": win_name or e_prefill.get("Window", "Window"),
            "Stitch Type": main_stitch,
            "Width (inches)": float(main_width),
            "Height (inches)": float(main_height),
            "Quantity": calculate_quantity(main_stitch, float(main_width), float(main_height)),
            "Track (ft)": calculate_track_ft(float(main_width), main_stitch),
            "SQFT": calculate_sqft_for_roman_or_regular(float(main_width), float(main_height), main_stitch),
            "Panels": calculate_panels(main_stitch, float(main_width)),
            "Lining": lining,
            "Images": imgs
        }
        st.session_state["entries"][edit_idx] = entry
        st.session_state["edit_index"] = None
        st.success("Window updated.")
    else:
        # Not editing — adding new window (create 1 or 2 rows)
        row1 = {
            "Window": f"{win_name.strip()} - Layer 1" if is_double else win_name.strip(),
            "Stitch Type": main_stitch,
            "Width (inches)": float(main_width),
            "Height (inches)": float(main_height),
            "Quantity": calculate_quantity(main_stitch, float(main_width), float(main_height)),
            "Track (ft)": calculate_track_ft(float(main_width), main_stitch),
            "SQFT": calculate_sqft_for_roman_or_regular(float(main_width), float(main_height), main_stitch),
            "Panels": calculate_panels(main_stitch, float(main_width)),
            "Lining": lining,
            "Images": imgs,
            "Layer": 1,
            "BaseWindow": win_name.strip()
        }
        st.session_state["entries"].append(row1)

        if is_double:
            row2 = {
                "Window": f"{win_name.strip()} - Layer 2",
                "Stitch Type": sheer_stitch_val,
                "Width (inches)": float(sheer_width_val),
                "Height (inches)": float(sheer_height_val),
                "Quantity": calculate_quantity(sheer_stitch_val, float(sheer_width_val), float(sheer_height_val)),
                "Track (ft)": calculate_track_ft(float(sheer_width_val), sheer_stitch_val),
                "SQFT": calculate_sqft_for_roman_or_regular(float(sheer_width_val), float(sheer_height_val), sheer_stitch_val),
                "Panels": calculate_panels(sheer_stitch_val, float(sheer_width_val)),
                "Lining": lining,
                "Images": imgs,
                "Layer": 2,
                "BaseWindow": win_name.strip()
            }
            st.session_state["entries"].append(row2)
        st.success("Window added.")

        # -----------------------------
        # CLEAR form fields for NEW add
        # -----------------------------
        # Instead of modifying the radio widget key (which may already be instantiated),
        # set a reset flag that will be applied before widget creation on the next run.
        st.session_state["_reset_new_form"] = True

        # Clear other form widget state keys (safe to delete)
        form_keys_to_clear = [
            "main_w", "main_h", "main_stitch",
            "sheer_w", "sheer_h", "sheer_stitch",
            "ui_lining", "uploader"
        ]
        for k in form_keys_to_clear:
            if k in st.session_state:
                try:
                    del st.session_state[k]
                except Exception:
                    pass

    # reset uploader state to clear widget (redundant but safe)
    if "uploader" in st.session_state:
        try:
            del st.session_state["uploader"]
        except Exception:
            pass

    # rerun to refresh UI
    st.rerun()

# -------------------------
# DISPLAY WINDOWS — responsive & mobile-friendly
# -------------------------
st.header("Windows Added")

if st.session_state["entries"]:
    # Build a DataFrame for display (no Streamlit widget per table cell)
    df_display = []
    for idx, e in enumerate(st.session_state["entries"]):
        df_display.append({
            "Index": idx,
            "Window": e.get("Window", ""),
            "BaseWindow": e.get("BaseWindow", ""),
            "Layer": e.get("Layer", 1),
            "Stitch Type": e.get("Stitch Type", ""),
            "Lining": e.get("Lining", "No Lining"),
            "Width (inches)": e.get("Width (inches)", 0),
            "Height (inches)": e.get("Height (inches)", 0),
            "Quantity": round(e.get("Quantity", 0), 2) if is_number(e.get("Quantity", 0)) else e.get("Quantity"),
            "Track (ft)": e.get("Track (ft)"),
            "SQFT": e.get("SQFT"),
            "Panels": e.get("Panels"),
            "Images": len(e.get("Images", []))
        })

    # Show dataframe — this will be scrollable horizontally on small screens
    st.dataframe(pd.DataFrame(df_display).set_index("Index"), use_container_width=True)

    # Compact action area: choose a row to edit/delete using a single selectbox
    st.markdown("**Select a row to Edit / Delete**")
    row_options = [f"{r['Index']}: {r['Window']} — {r['Stitch Type']}" for r in df_display]
    selected_row = st.selectbox("Choose row", options=list(range(len(row_options))), format_func=lambda i: row_options[i])

    col1, col2, col3 = st.columns([1,1,8])
    if col1.button("Edit Selected"):
        st.session_state["edit_index"] = selected_row
        st.rerun()

    if col2.button("Delete Selected"):
        removed = st.session_state["entries"].pop(selected_row)
        # If we deleted a layer that belonged to a pair, edit_index may no longer be valid
        if st.session_state.get("edit_index") == selected_row:
            st.session_state["edit_index"] = None
        st.success(f"Deleted window: {removed.get('Window','(unknown)')}")
        st.rerun()

    # Totals
    total_qty = total_track = total_sqft = 0
    total_panels = 0
    for e in st.session_state["entries"]:
        q = e.get("Quantity") or 0
        if is_number(q): total_qty += q
        t = e.get("Track (ft)")
        if is_number(t): total_track += t
        s = e.get("SQFT") or 0
        if is_number(s): total_sqft += s
        p = e.get("Panels") or 0
        if is_number(p): total_panels += p

    colA, colB, colC, colD = st.columns(4)
    colA.metric("Total Quantity", round(total_qty,2))
    colB.metric("Total Track (ft)", round(total_track,2))
    colC.metric("Total SQFT", round(total_sqft,2) if total_sqft else "N/A")
    colD.metric("Total Panels", round(total_panels,2))

    if st.button("Reset All"):
        st.session_state["entries"] = []
        st.session_state["current_customer"] = None
        st.session_state["edit_index"] = None
        st.rerun()
else:
    st.info("No windows added yet. Add windows above.")

# Customer details + showroom + single Save Order
st.header("Customer Details & Save Order")
SHOWROOM_OPTIONS = ["", "Anna Nagar", "Valasaravakkam"]

if not st.session_state["current_customer"]:
    c_name = st.text_input("Customer Name")
    c_phone = st.text_input("Customer Phone")
    c_addr = st.text_area("Customer Address")
    c_showroom = st.selectbox("Showroom", SHOWROOM_OPTIONS, index=0)
else:
    cust = st.session_state["current_customer"]
    c_name = st.text_input("Customer Name", value=cust.get("name",""))
    c_phone = st.text_input("Customer Phone", value=cust.get("phone",""))
    c_addr = st.text_area("Customer Address", value=cust.get("address",""))
    current_showroom = cust.get("showroom","") if cust.get("showroom","") in SHOWROOM_OPTIONS else ""
    c_showroom = st.selectbox("Showroom", SHOWROOM_OPTIONS, index=SHOWROOM_OPTIONS.index(current_showroom))

if st.button("Save Order"):
    if not c_name and not c_phone:
        st.warning("Enter at least a name or phone to save order.")
    elif not st.session_state["entries"]:
        st.warning("Add at least one window before saving.")
    else:
        try:
            cid = save_customer_if_new(c_name or "", c_phone or "", c_addr or "", c_showroom or "")
            if st.session_state.get("current_customer") and st.session_state["current_customer"]["id"] == cid:
                update_customer_db(cid, c_name or "", c_phone or "", c_addr or "", c_showroom or "")
            oid = save_order(cid, st.session_state["entries"])
            st.session_state["current_customer"] = {"id": cid, "name": c_name, "phone": c_phone, "address": c_addr, "showroom": c_showroom or ""}
            st.success(f"Order Saved! Order ID: {oid}")
            st.rerun()
        except Exception as ex:
            st.error(f"Failed to save order: {ex}")

# PDF export (single download button)
st.header("Generate PDF")
if st.session_state["current_customer"] and st.session_state["entries"]:
    try:
        pdf_bytes = generate_pdf_bytes(st.session_state["current_customer"], st.session_state["entries"])
        st.download_button(
            "Download PDF",
            data=pdf_bytes,
            file_name=f"Order_{st.session_state['current_customer']['name'].replace(' ','_')}.pdf",
            mime="application/pdf",
        )
    except Exception as ex:
        st.error(f"Failed to create PDF: {ex}")
else:
    st.info("Save an order first before generating a PDF.")
