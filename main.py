import streamlit as st
import pandas as pd
import math
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
import io

st.set_page_config(layout="wide")
st.title("Curtain Fabric Calculator")

# ---------------------------
# SESSION STATE DEFAULTS
# ---------------------------
if "entries" not in st.session_state:
    st.session_state["entries"] = []
if "confirm_save" not in st.session_state:
    st.session_state["confirm_save"] = False

# widget-backed defaults (safe BEFORE creating widgets)
if "window_name" not in st.session_state:
    st.session_state["window_name"] = ""
if "stitch_type" not in st.session_state:
    st.session_state["stitch_type"] = "Pleated"
if "width" not in st.session_state:
    st.session_state["width"] = 0.0
if "height" not in st.session_state:
    st.session_state["height"] = 0.0

# NOTE: We DO NOT create or assign a session_state key for the file_uploader.
# The uploader lives inside the form and we read its return value directly.

# ---------------------------
# HELPER / CALCULATION FUNCTIONS
# ---------------------------
def is_number(x):
    return isinstance(x, (int, float))

def calculate_height_factor(height):
    """(height + 14) / 39 rounded to 2 decimals"""
    return round((height + 14) / 39, 2)

def calculate_quantity(stitch, width, height):
    """Quantity rules per stitch type."""
    h = calculate_height_factor(height)

    if stitch == "Pleated":
        w = round(width / 18, 0)
        return w * h

    if stitch == "Ripple":
        w = round(width / 20, 0)
        return w * h

    if stitch == "Eyelet":
        w = round(width / 24, 0)
        return w * h

    if stitch == 'Roman Blinds 48"':
        panels = math.ceil(width / 44.0)  # round up
        return round(panels * h)

    if stitch == 'Roman Blinds 54"':
        panels = math.ceil(width / 50.0)  # round up
        return round(panels * h)

    if stitch == "Blinds (Regular)":
        return 0

    return 0

def calculate_track_ft(width, stitch):
    """
    Track in feet = width / 12,
    then round up to nearest 0.5 ft (ceiling to 0.5 increments).
    No track for Roman/Regular blinds.
    """
    if stitch in ['Roman Blinds 48"', 'Roman Blinds 54"', 'Blinds (Regular)']:
        return None
    ft = width / 12.0
    return math.ceil(ft * 2.0) / 2.0

def calculate_sqft_for_roman_or_regular(width, height, stitch):
    """
    For Roman & Regular blinds compute SQFT:
    round(width/12) * round(height/12)
    """
    if stitch in ['Roman Blinds 48"', 'Roman Blinds 54"', 'Blinds (Regular)']:
        w_blocks = round(width / 12.0)
        h_blocks = round(height / 12.0)
        return w_blocks * h_blocks
    return None

def calculate_panels(stitch, width):
    """
    Panels only for Pleated, Ripple, Eyelet:
    Pleated -> round(width / 18)
    Ripple  -> round(width / 20)
    Eyelet  -> round(width / 24)
    """
    if stitch == "Pleated":
        return int(round(width / 18, 0))
    if stitch == "Ripple":
        return int(round(width / 20, 0))
    if stitch == "Eyelet":
        return int(round(width / 24, 0))
    return None

# ---------------------------
# Reset callback
# ---------------------------
def reset_everything_callback():
    st.session_state["entries"] = []
    st.session_state["confirm_save"] = False
    # Reset widget defaults (if desired)
    st.session_state["window_name"] = ""
    st.session_state["stitch_type"] = "Pleated"
    st.session_state["width"] = 0.0
    st.session_state["height"] = 0.0
    # Force rerun to clear UI
    st.rerun()

# ---------------------------
# INPUT SECTION (form-based: uploader has no key)
# ---------------------------
st.header("Add Window Details")

with st.form("add_window_form", clear_on_submit=False):
    window_name_in = st.text_input("Window Name / Description")
    stitch_type_in = st.selectbox(
        "Stitch Type",
        [
            "Pleated",
            "Ripple",
            "Eyelet",
            'Roman Blinds 48"',
            'Roman Blinds 54"',
            "Blinds (Regular)",
        ],
        index=0,
    )
    width_in = st.number_input("Width (in inches)", min_value=0.0, step=0.1, value=0.0)
    height_in = st.number_input("Height (in inches)", min_value=0.0, step=0.1, value=0.0)

    # IMPORTANT: No 'key' here. We read the returned list directly on submit.
    uploaded_files = st.file_uploader(
        "Upload window images (optional) — multiple allowed",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
    )

    submitted = st.form_submit_button("Add Window")

if submitted:
    # prepare values
    stitch = stitch_type_in
    w = float(width_in)
    h = float(height_in)
    name = window_name_in.strip() or "Window"

    qty = calculate_quantity(stitch, w, h)
    track_ft = calculate_track_ft(w, stitch)
    sqft = calculate_sqft_for_roman_or_regular(w, h, stitch)
    panels = calculate_panels(stitch, w)

    # read images into bytes
    images_bytes = []
    if uploaded_files:
        for f in uploaded_files:
            try:
                images_bytes.append(f.read())
            except Exception:
                continue

    entry = {
        "Window": name,
        "Stitch Type": stitch,
        "Width (inches)": w,
        "Height (inches)": h,
        "Quantity": qty if is_number(qty) else 0,
        "Track (ft)": track_ft,
        "SQFT": sqft,
        "Panels": panels,
        "Images": images_bytes,
    }

    st.session_state["entries"].append(entry)

    st.success(f"Added: {name} — Qty: {qty} — Images: {len(images_bytes)}")
    # rerun to clear the form (including uploader)
    st.rerun()

# ---------------------------
# DISPLAY TABLE + TOTALS
# ---------------------------
st.header("Windows Added (Not Saved Yet)")

if st.session_state["entries"]:
    # Prepare DataFrame for display (exclude raw image bytes)
    rows_for_df = []
    for e in st.session_state["entries"]:
        row = e.copy()
        row["Image Count"] = len(row.get("Images", []))
        row.pop("Images", None)
        rows_for_df.append(row)

    df = pd.DataFrame(rows_for_df)

    # Compute totals
    total_qty = 0.0
    total_track = 0.0
    total_sqft = 0.0
    total_panels = 0

    for e in st.session_state["entries"]:
        q = e.get("Quantity")
        if is_number(q):
            total_qty += float(q)

        t = e.get("Track (ft)")
        if is_number(t):
            total_track += float(t)

        s = e.get("SQFT")
        if is_number(s):
            total_sqft += float(s)

        p = e.get("Panels")
        if is_number(p):
            total_panels += int(p)

    # Display formatted DataFrame
    display_df = df.copy()
    display_df["Track (ft)"] = display_df["Track (ft)"].apply(lambda x: f"{x:.1f} ft" if is_number(x) else "-")
    display_df["SQFT"] = display_df["SQFT"].apply(lambda x: int(x) if is_number(x) else "-")
    display_df["Panels"] = display_df["Panels"].apply(lambda x: int(x) if is_number(x) else "-")

    def fmt_qty(x):
        if is_number(x):
            if isinstance(x, float) and not float(x).is_integer():
                return f"{x:.2f}"
            return f"{int(x)}"
        return "-"

    display_df["Quantity"] = display_df["Quantity"].apply(fmt_qty)

    st.dataframe(display_df, use_container_width=True)

    # Totals display
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Fabric Quantity", f"{total_qty:.2f}" if not float(total_qty).is_integer() else f"{int(total_qty)}")
    c2.metric("Total Track (ft)", f"{total_track:.1f} ft")
    c3.metric("Total SQFT (Roman & Regular Blinds)", f"{total_sqft:.1f} sq.ft")
    c4.metric("Total Panels (Pleated/Ripple/Eyelet)", f"{total_panels}")

    # Reset button (clears and reruns)
    if st.button("Reset Everything"):
        reset_everything_callback()
else:
    st.info("No entries added yet.")

# ---------------------------
# SAVE PDF WORKFLOW
# ---------------------------
st.header("Save as PDF Order Form")

if st.session_state["entries"]:
    if st.button("Generate PDF"):
        st.session_state["confirm_save"] = True

    if st.session_state["confirm_save"]:
        st.subheader("Customer Details")
        cust_name = st.text_input("Customer Name")
        cust_phone = st.text_input("Customer Phone Number")
        cust_address = st.text_area("Customer Address")

        if st.button("Confirm & Download PDF"):
            # Build PDF in memory
            buffer = io.BytesIO()
            pdf = SimpleDocTemplate(buffer, pagesize=A4)
            styles = getSampleStyleSheet()
            story = []

            # PDF Title
            story.append(Paragraph("<b><font size=18>Order Form</font></b>", styles["Title"]))
            story.append(Spacer(1, 12))

            # Customer Info
            customer_info = f"""
                <font size=12>
                <b>Customer Name:</b> {cust_name}<br/>
                <b>Phone:</b> {cust_phone}<br/>
                <b>Address:</b> {cust_address}<br/>
                <b>Date:</b> {datetime.now().strftime("%d-%m-%Y %H:%M")}
                </font>
            """
            story.append(Paragraph(customer_info, styles["Normal"]))
            story.append(Spacer(1, 12))

            # Loop through window entries and add tables + images
            total_qty_pdf = 0.0
            total_track_pdf = 0.0
            total_sqft_pdf = 0.0
            total_panels_pdf = 0

            for entry in st.session_state["entries"]:
                story.append(Paragraph(f"<b><font size=14>{entry['Window']}</font></b>", styles["Heading2"]))
                story.append(Spacer(1, 6))

                # Prepare display values
                qty_display = entry.get("Quantity", 0)
                qty_str = "-"
                if is_number(qty_display):
                    total_qty_pdf += float(qty_display)
                    qty_str = f"{qty_display:.2f}" if (isinstance(qty_display, float) and not float(qty_display).is_integer()) else f"{int(qty_display)}"

                track_val = entry.get("Track (ft)")
                track_str = "-"
                if is_number(track_val):
                    total_track_pdf += float(track_val)
                    track_str = f"{track_val:.1f} ft"

                sqft_val = entry.get("SQFT")
                sqft_str = "-"
                if is_number(sqft_val):
                    total_sqft_pdf += float(sqft_val)
                    sqft_str = f"{int(sqft_val)} sq.ft"

                panels_val = entry.get("Panels")
                panels_str = "-"
                if is_number(panels_val):
                    total_panels_pdf += int(panels_val)
                    panels_str = f"{int(panels_val)}"

                table_data = [
                    ["Stitch Type", entry["Stitch Type"]],
                    ["Width (inches)", entry["Width (inches)"]],
                    ["Height (inches)", entry["Height (inches)"]],
                    ["Quantity", qty_str],
                    ["Track (ft)", track_str],
                    ["SQFT", sqft_str],
                    ["Panels", panels_str],
                ]
                table = Table(table_data, colWidths=[120, 340])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (1, 0), colors.lightgrey),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                    ('FONT', (0,0), (-1,-1), 'Helvetica', 10),
                ]))
                story.append(table)
                story.append(Spacer(1, 8))

                # Add images (if any)
                images = entry.get("Images", [])
                if images:
                    for img_bytes in images:
                        try:
                            img_buffer = io.BytesIO(img_bytes)
                            img = RLImage(img_buffer, width=2.5 * inch, height=2.5 * inch)
                            story.append(img)
                            story.append(Spacer(1, 6))
                        except Exception:
                            # skip images that can't be processed
                            continue

                story.append(Spacer(1, 12))

            # Totals section in PDF
            story.append(Spacer(1, 12))
            totals_table = [
                ["Total Fabric Quantity", f"{total_qty_pdf:.2f}" if not float(total_qty_pdf).is_integer() else f"{int(total_qty_pdf)}"],
                ["Total Track (ft)", f"{total_track_pdf:.1f} ft"],
                ["Total SQFT (Roman & Regular Blinds)", f"{total_sqft_pdf:.1f} sq.ft"],
                ["Total Panels (Pleated/Ripple/Eyelet)", f"{total_panels_pdf}"]
            ]
            totals_tbl = Table(totals_table, colWidths=[260, 200])
            totals_tbl.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONT', (0,0), (-1,-1), 'Helvetica-Bold', 11),
            ]))
            story.append(totals_tbl)
            story.append(Spacer(1, 12))

            pdf.build(story)
            buffer.seek(0)

            st.download_button(
                label="Download PDF Order Form",
                data=buffer,
                file_name=f"Order_Form_{cust_name.replace(' ','_')}.pdf",
                mime="application/pdf"
            )

            st.success("PDF Generated!")

else:
    st.info("Add at least one window to enable PDF creation.")

