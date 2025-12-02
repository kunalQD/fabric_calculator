# Updated Curtain Fabric Calculator — integrated row actions, single Save, cleaned PDF flow
import streamlit as st
import pandas as pd
import math
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
import io
import sqlite3
import json
import uuid
from pathlib import Path

# -------------------------------------
# STORAGE PATHS
# -------------------------------------
BASE_DIR = Path.cwd() / "saved_data"
DB_PATH = BASE_DIR / "app_data.db"
IMAGES_DIR = BASE_DIR / "images"
BASE_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------
# DB INITIALIZATION
# -------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            address TEXT,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY,
            customer_id INTEGER,
            created_at TEXT,
            entries_json TEXT
        )
    """)

    conn.commit()
    conn.close()

# -------------------------------------
# DB HELPERS
# -------------------------------------
def save_customer_if_new(name, phone, address):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Try match by phone
    if phone:
        c.execute("SELECT id FROM customers WHERE phone = ?", (phone.strip(),))
        row = c.fetchone()
        if row:
            cid = row[0]
            conn.close()
            return cid

    # Try match by name
    if name:
        c.execute("SELECT id FROM customers WHERE name = ?", (name.strip(),))
        row = c.fetchone()
        if row:
            cid = row[0]
            conn.close()
            return cid

    # Create new
    now = datetime.now().isoformat()
    c.execute("INSERT INTO customers(name, phone, address, created_at) VALUES (?,?,?,?)",
              (name.strip(), phone.strip(), address.strip(), now))
    cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid

def update_customer_db(cid, name, phone, address):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE customers SET name = ?, phone = ?, address = ? WHERE id = ?
    """, (name.strip(), phone.strip(), address.strip(), cid))
    conn.commit()
    conn.close()

def search_customers(term):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    like = f"%{term.strip()}%"
    c.execute("""
        SELECT id, name, phone, address, created_at 
        FROM customers 
        WHERE name LIKE ? OR phone LIKE ?
        ORDER BY created_at DESC
    """, (like, like))
    rows = c.fetchall()
    conn.close()
    return rows

def save_order(customer_id, entries):
    order_id = str(uuid.uuid4())

    # Create order image folder
    order_dir = IMAGES_DIR / order_id
    order_dir.mkdir(parents=True, exist_ok=True)

    serializable_entries = []

    for i, e in enumerate(entries):
        e_copy = e.copy()
        images = e_copy.get("Images", [])
        image_paths = []

        for idx, b in enumerate(images):
            # images are raw bytes
            try:
                fpath = order_dir / f"{i+1}_{idx+1}.jpg"
                with open(fpath, "wb") as f:
                    f.write(b)
                image_paths.append(str(fpath.relative_to(BASE_DIR)))
            except Exception:
                # skip if image save fails
                pass

        e_copy["Images"] = image_paths
        serializable_entries.append(e_copy)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()

    c.execute("""
        INSERT INTO orders(id, customer_id, created_at, entries_json) 
        VALUES (?,?,?,?)
    """, (order_id, customer_id, now, json.dumps(serializable_entries)))

    conn.commit()
    conn.close()
    return order_id

def get_orders_for_customer(cid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, created_at, entries_json
        FROM orders
        WHERE customer_id = ?
        ORDER BY created_at DESC
    """, (cid,))
    rows = c.fetchall()
    conn.close()

    orders = []
    for oid, created, entries_json in rows:
        try:
            entries = json.loads(entries_json)
        except:
            entries = []
        orders.append({
            "id": oid,
            "created_at": created,
            "entries": entries,
        })
    return orders

# -------------------------------------
# CALCULATION HELPERS
# -------------------------------------
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

# -------------------------------------
# ORDER LOADER
# -------------------------------------
def load_order_into_session(order):
    loaded = []

    for e in order["entries"]:
        stitch = e.get("Stitch Type")
        width = float(e.get("Width (inches)", 0))
        height = float(e.get("Height (inches)", 0))

        # load image bytes
        images_bytes = []
        for p in e.get("Images", []):
            abs_path = BASE_DIR / p
            if abs_path.exists():
                with open(abs_path, "rb") as f:
                    images_bytes.append(f.read())

        loaded.append({
            "Window": e.get("Window"),
            "Stitch Type": stitch,
            "Width (inches)": width,
            "Height (inches)": height,
            "Quantity": calculate_quantity(stitch, width, height),
            "Track (ft)": calculate_track_ft(width, stitch),
            "SQFT": calculate_sqft_for_roman_or_regular(width, height, stitch),
            "Panels": calculate_panels(stitch, width),
            "Images": images_bytes,
        })

    st.session_state["entries"] = loaded

# -------------------------------------
# PDF GENERATOR (returns bytes)
# -------------------------------------
def generate_pdf_bytes(customer, entries):
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b><font size=18>Order Form</font></b>", styles["Title"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph(f"""
        <b>Name:</b> {customer['name']}<br/>
        <b>Phone:</b> {customer['phone']}<br/>
        <b>Address:</b> {customer['address']}<br/>
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

        for img in e["Images"]:
            try:
                img_buff = io.BytesIO(img)
                story.append(RLImage(img_buff, width=2.5*inch, height=2.5*inch))
                story.append(Spacer(1, 6))
            except:
                pass

    pdf.build(story)
    buffer.seek(0)
    return buffer.read()

# -------------------------------------
# STREAMLIT INITIAL SETUP
# -------------------------------------
init_db()
st.set_page_config(layout="wide", page_title="Curtain Fabric Calculator")

if "entries" not in st.session_state:
    st.session_state["entries"] = []

if "current_customer" not in st.session_state:
    st.session_state["current_customer"] = None

if "edit_index" not in st.session_state:
    st.session_state["edit_index"] = None

st.title("Curtain Fabric Calculator")

# -------------------------------------
# LOAD EXISTING CUSTOMER SCREEN
# -------------------------------------
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
        results = search_customers(term)

        if not results:
            st.warning("No results found.")
        else:
            formatted = [f"{r[0]} — {r[1]} — {r[2]}" for r in results]
            sel = st.selectbox("Select customer", list(range(len(formatted))), format_func=lambda i: formatted[i])
            cid, name, phone, addr, _ = results[sel]

            # Immediately set current customer AND auto-load latest order (if exists)
            st.session_state["current_customer"] = {
                "id": cid, "name": name, "phone": phone, "address": addr
            }

            orders = get_orders_for_customer(cid)

            if orders:
                # load latest automatically
                load_order_into_session(orders[0])
                st.success(f"Customer '{name}' loaded and latest order loaded.")
            else:
                st.info(f"Customer '{name}' loaded — no saved orders. Start adding windows.")

            # clear any edit index
            st.session_state["edit_index"] = None
            # rerun so UI updates and Windows Added shows
            st.rerun()

# -------------------------------------
# ADD / EDIT WINDOW FORM
# -------------------------------------
st.header("Add / Edit Window Details")

# If editing a row, prefill form values
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
    # if user uploaded new images, use them; otherwise keep existing images when updating
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
        "Images": imgs,
    }

    if e_prefill:
        # update existing
        st.session_state["entries"][edit_idx] = entry
        st.session_state["edit_index"] = None
        st.success("Window updated.")
    else:
        st.session_state["entries"].append(entry)
        st.success("Window added.")

    # clear uploader: increment uploader key to reset (hack)
    if "uploader" in st.session_state:
        try:
            del st.session_state["uploader"]
        except Exception:
            pass

    # clear form prefill and rerun so the empty form is shown
    st.rerun()

# -------------------------------------
# DISPLAY WINDOWS (table + integrated actions)
# -------------------------------------
st.header("Windows Added")

if st.session_state["entries"]:
    # Table header
    cols = st.columns([1.2,1.6,1,1,1,1,1,0.9,0.9])  # tune widths
    headers = ["#", "Window", "Stitch Type", "Width", "Height", "Quantity", "Track (ft)", "Panels", "Images"]
    for c, h in zip(cols, headers):
        c.markdown(f"**{h}**")

    # Rows with actions inline
    for i, e in enumerate(st.session_state["entries"]):
        row_cols = st.columns([1.2,1.6,1,1,1,1,1,0.9,0.9,1.4])  # last col for actions
        row_cols[0].write(i)
        row_cols[1].write(e["Window"])
        row_cols[2].write(e["Stitch Type"])
        row_cols[3].write(e["Width (inches)"])
        row_cols[4].write(e["Height (inches)"])
        row_cols[5].write(round(e["Quantity"],2) if is_number(e["Quantity"]) else e["Quantity"])
        row_cols[6].write(e["Track (ft)"] if e["Track (ft)"] is not None else "None")
        row_cols[7].write(e["Panels"] if e["Panels"] is not None else "None")
        row_cols[8].write(len(e["Images"]))

        # Inline actions in last column (Edit / Delete)
        if row_cols[9].button("Edit", key=f"edit_{i}"):
            st.session_state["edit_index"] = i
            st.rerun()
        if row_cols[9].button("Delete", key=f"del_{i}"):
            st.session_state["entries"].pop(i)
            # reset edit index if necessary
            if st.session_state.get("edit_index") == i:
                st.session_state["edit_index"] = None
            st.success("Window deleted.")
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

# -------------------------------------
# CUSTOMER DETAILS (editable) + Save Order (single button)
# -------------------------------------
st.header("Customer Details & Save Order")

if not st.session_state["current_customer"]:
    c_name = st.text_input("Customer Name")
    c_phone = st.text_input("Customer Phone")
    c_addr = st.text_area("Customer Address")
else:
    cust = st.session_state["current_customer"]
    c_name = st.text_input("Customer Name", value=cust.get("name",""))
    c_phone = st.text_input("Customer Phone", value=cust.get("phone",""))
    c_addr = st.text_area("Customer Address", value=cust.get("address",""))

# Single Save Order button: creates/updates customer and saves order
if st.button("Save Order"):
    # validate
    if not c_name and not c_phone:
        st.warning("Enter at least a name or phone to save order.")
    elif not st.session_state["entries"]:
        st.warning("Add at least one window before saving.")
    else:
        # create or find customer
        cid = save_customer_if_new(c_name or "", c_phone or "", c_addr or "")
        # if there was an existing customer, update details
        if st.session_state.get("current_customer"):
            if st.session_state["current_customer"]["id"] == cid:
                update_customer_db(cid, c_name or "", c_phone or "", c_addr or "")
        # save order
        oid = save_order(cid, st.session_state["entries"])
        st.session_state["current_customer"] = {"id": cid, "name": c_name, "phone": c_phone, "address": c_addr}
        st.success(f"Order Saved! Order ID: {oid}")
        st.rerun()

# -------------------------------------
# PDF EXPORT (single download button)
# -------------------------------------
st.header("Generate PDF")

# Only show download when there's a customer loaded and entries exist
if st.session_state["current_customer"] and st.session_state["entries"]:
    # Prepare bytes now (fast enough for small orders). If large, you can generate on save and store buffer on disk.
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
