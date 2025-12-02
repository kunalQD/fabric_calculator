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
import json
import uuid
import math
from datetime import datetime
from pathlib import Path

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
MONGO_URI = os.getenv("MONGO_URI")
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

    for e in entries:
        story.append(Paragraph(f"<b>{e['Window']}</b>", styles["Heading2"]))

        tbl = Table([
            ["Stitch Type", e["Stitch Type"]],
            ["Width", e["Width (inches)"]],
            ["Height", e["Height (inches)"]],
            ["Quantity", e["Quantity"]],
            ["Track (ft)", e["Track (ft)"]],
            ["SQFT", e["SQFT"]],
            ["Panels", e["Panels"]],
        ], colWidths=[150, 300])

        tbl.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 10))

        for img in e.get("Images", []):
            try:
                img_buff = io.BytesIO(img)
                story.append(RLImage(img_buff, width=2.5*inch, height=2.5*inch))
                story.append(Spacer(1, 6))
            except Exception:
                pass

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

edit_idx = st.session_state.get("edit_index")
if edit_idx is not None and 0 <= edit_idx < len(st.session_state["entries"]):
    e_prefill = st.session_state["entries"][edit_idx]
else:
    e_prefill = None

with st.form("win_form", clear_on_submit=False):
    win_name = st.text_input("Window Name", value=e_prefill["Window"] if e_prefill else "")
    stitch = st.selectbox("Stitch Type", [
        "Pleated", "Ripple", "Eyelet",
        'Roman Blinds 48"', 'Roman Blinds 54"',
        "Blinds (Regular)"
    ], index=["Pleated","Ripple","Eyelet",'Roman Blinds 48"','Roman Blinds 54"',"Blinds (Regular)"].index(e_prefill["Stitch Type"]) if e_prefill else 0)
    width = st.number_input("Width (inches)", min_value=0.0, value=e_prefill["Width (inches)"] if e_prefill else 0.0)
    height = st.number_input("Height (inches)", min_value=0.0, value=e_prefill["Height (inches)"] if e_prefill else 0.0)
    files = st.file_uploader("Upload Images", accept_multiple_files=True, key="uploader")

    submit_label = "Update Window" if e_prefill else "Add Window"
    add = st.form_submit_button(submit_label)

if add:
    imgs = []
    if files:
        imgs = [f.read() for f in files]
    elif e_prefill:
        imgs = e_prefill.get("Images", [])

    entry = {
        "Window": win_name or "Window",
        "Stitch Type": stitch,
        "Width (inches)": width,
        "Height (inches)": height,
        "Quantity": calculate_quantity(stitch, width, height),
        "Track (ft)": calculate_track_ft(width, stitch),
        "SQFT": calculate_sqft_for_roman_or_regular(width, height, stitch),
        "Panels": calculate_panels(stitch, width),
        "Images": imgs
    }

    if e_prefill:
        st.session_state["entries"][edit_idx] = entry
        st.session_state["edit_index"] = None
        st.success("Window updated.")
    else:
        st.session_state["entries"].append(entry)
        st.success("Window added.")

    # reset uploader
    if "uploader" in st.session_state:
        try:
            del st.session_state["uploader"]
        except Exception:
            pass

    # after add/update show cleared form (or unprefilled)
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
            "Window": e["Window"],
            "Stitch Type": e["Stitch Type"],
            "Width (inches)": e["Width (inches)"],
            "Height (inches)": e["Height (inches)"],
            "Quantity": round(e["Quantity"], 2) if is_number(e["Quantity"]) else e["Quantity"],
            "Track (ft)": e["Track (ft)"] if e["Track (ft)"] is not None else "None",
            "SQFT": e["SQFT"] if e["SQFT"] is not None else "None",
            "Panels": e["Panels"] if e["Panels"] is not None else "None",
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
        if st.session_state.get("edit_index") == selected_row:
            st.session_state["edit_index"] = None
        st.success(f"Deleted window: {removed.get('Window','(unknown)')}")
        st.rerun()

    # Totals
    total_qty = total_track = total_sqft = 0
    total_panels = 0
    for e in st.session_state["entries"]:
        if is_number(e["Quantity"]): total_qty += e["Quantity"]
        if is_number(e["Track (ft)"]): total_track += e["Track (ft)"]
        if is_number(e["SQFT"]): total_sqft += e["SQFT"]
        if is_number(e["Panels"]): total_panels += e["Panels"]

    colA, colB, colC, colD = st.columns(4)
    colA.metric("Total Quantity", round(total_qty, 2))
    colB.metric("Total Track (ft)", round(total_track, 2))
    colC.metric("Total SQFT", round(total_sqft, 2))
    colD.metric("Total Panels", total_panels)

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