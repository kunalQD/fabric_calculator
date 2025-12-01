import streamlit as st
import pandas as pd
import math
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import io

st.set_page_config(layout="wide")
st.title("Curtain Quantity Calculator – PDF Order Form (Final)")

# ---------------------------
# SESSION STATE DEFAULTS
# ---------------------------
if "entries" not in st.session_state:
    st.session_state["entries"] = []
if "confirm_save" not in st.session_state:
    st.session_state["confirm_save"] = False

# default widget values (must be set BEFORE creating widgets)
if "window_name" not in st.session_state:
    st.session_state["window_name"] = ""
if "stitch_type" not in st.session_state:
    st.session_state["stitch_type"] = "Pleated"
if "width" not in st.session_state:
    st.session_state["width"] = 0.0
if "height" not in st.session_state:
    st.session_state["height"] = 0.0

# ---------------------------
# HELPER / CALCULATION FUNCTIONS
# ---------------------------
def is_number(x):
    return isinstance(x, (int, float))

def calculate_height_factor(height):
    """(height + 14) / 39 rounded to 2 decimals"""
    return round((height + 14) / 39, 2)

def calculate_quantity(stitch, width, height):
    """
    Returns quantity according to stitch rules:
    - Pleated: round(width/18,0) * round((height+14)/39,2)
    - Ripple: round(width/20,0) * round((height+14)/39,2)
    - Eyelet: round(width/24,0) * round((height+14)/39,2)
    - Roman Blinds 48": panels = ceil(width/44); qty = round(panels * h)
    - Roman Blinds 54": panels = ceil(width/50); qty = round(panels * h)
    - Blinds (Regular): do not calculate fabric qty (return 0)
    """
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
        panels = math.ceil(width / 44.0)
        return round(panels * h)

    if stitch == 'Roman Blinds 54"':
        panels = math.ceil(width / 50.0)
        return round(panels * h)

    if stitch == "Blinds (Regular)":
        # do not calculate fabric quantity for regular blinds
        return 0

    return 0

def calculate_track_ft(width, stitch):
    """
    Track in feet = width / 12,
    then round up to nearest 0.5 ft (ceiling to 0.5 increments).
    For Roman Blinds and Blinds (Regular) -> no track (return None)
    """
    if stitch in ['Roman Blinds 48"', 'Roman Blinds 54"', 'Blinds (Regular)']:
        return None
    ft = width / 12.0
    ft_rounded = math.ceil(ft * 2) / 2.0
    return ft_rounded

def calculate_sqft_for_roman_or_regular(width, height, stitch):
    """
    For Roman Blinds and Blinds (Regular) compute SQFT:
    round(width/12) * round(height/12)
    Otherwise return None.
    """
    if stitch in ['Roman Blinds 48"', 'Roman Blinds 54"', 'Blinds (Regular)']:
        w_blocks = round(width / 12.0)
        h_blocks = round(height / 12.0)
        return w_blocks * h_blocks
    return None

# ---------------------------
# CALLBACKS
# ---------------------------
def add_window_callback():
    stitch = st.session_state["stitch_type"]
    w = st.session_state["width"]
    h = st.session_state["height"]
    name = st.session_state["window_name"]

    qty = calculate_quantity(stitch, w, h)
    track_ft = calculate_track_ft(w, stitch)
    sqft = calculate_sqft_for_roman_or_regular(w, h, stitch)

    # For consistency in storage, store numeric 0 as 0 and None as None
    entry = {
        "Window": name,
        "Stitch Type": stitch,
        "Width (inches)": w,
        "Height (inches)": h,
        "Quantity": qty if is_number(qty) and qty != 0 else (0 if stitch in ["Pleated","Ripple","Eyelet","Roman Blinds 48\"","Roman Blinds 54\""] else 0),
        "Track (ft)": track_ft,
        "SQFT": sqft
    }

    st.session_state["entries"].append(entry)

    # Reset input widgets by updating session_state values (safe inside callback)
    st.session_state["window_name"] = ""
    st.session_state["stitch_type"] = "Pleated"
    st.session_state["width"] = 0.0
    st.session_state["height"] = 0.0

    # ensure confirm_save is reset
    st.session_state["confirm_save"] = False

def reset_everything_callback():
    st.session_state["entries"] = []
    st.session_state["confirm_save"] = False
    st.session_state["window_name"] = ""
    st.session_state["stitch_type"] = "Pleated"
    st.session_state["width"] = 0.0
    st.session_state["height"] = 0.0

def enable_confirm_callback():
    st.session_state["confirm_save"] = True

# ---------------------------
# INPUT SECTION (use keys)
# ---------------------------
st.header("Add Window Details")
col1, col2 = st.columns([3,1])

with col1:
    window_name = st.text_input("Window Name / Description", key="window_name")
    stitch_type = st.selectbox(
        "Stitch Type",
        [
            "Pleated",
            "Ripple",
            "Eyelet",
            'Roman Blinds 48"',
            'Roman Blinds 54"',
            "Blinds (Regular)"
        ],
        key="stitch_type"
    )
    width = st.number_input("Width (in inches)", min_value=0.0, step=0.1, key="width")
    height = st.number_input("Height (in inches)", min_value=0.0, step=0.1, key="height")

with col2:
    st.write("")  # spacer
    st.write("")
    st.button("Add Window", on_click=add_window_callback)

# ---------------------------
# DISPLAY TABLE + TOTALS
# ---------------------------
st.header("Windows Added (Not Saved Yet)")
if st.session_state["entries"]:
    df = pd.DataFrame(st.session_state["entries"])

    # Compute totals
    total_qty = 0.0
    total_track = 0.0
    total_sqft = 0.0

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

    # Display table
    st.dataframe(df, use_container_width=True)

    # Totals display
    t1, t2, t3 = st.columns(3)
    t1.metric("Total Fabric Quantity", f"{total_qty:.2f}" if not float(total_qty).is_integer() else f"{int(total_qty)}")
    t2.metric("Total Track (ft)", f"{total_track:.1f} ft")
    t3.metric("Total SQFT (Roman & Regular Blinds)", f"{total_sqft:.1f} sq.ft")

    # RESET BUTTON
    st.button("Reset Everything", on_click=reset_everything_callback)
else:
    st.info("No entries added yet.")

# ---------------------------
# SAVE PDF WORKFLOW
# ---------------------------
st.header("Save as PDF Order Form")

if st.session_state["entries"]:
    st.button("Generate PDF", on_click=enable_confirm_callback)

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
            title = Paragraph("<b><font size=18>Order Form</font></b>", styles["Title"])
            story.append(title)
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
            story.append(Spacer(1, 20))

            # Loop through window entries
            total_qty_pdf = 0.0
            total_track_pdf = 0.0
            total_sqft_pdf = 0.0

            for entry in st.session_state["entries"]:
                story.append(Paragraph(f"<b><font size=14>{entry['Window']}</font></b>", styles["Heading2"]))
                story.append(Spacer(1, 6))

                # Prepare display values
                qty_display = entry.get("Quantity", 0)
                qty_str = ""
                if is_number(qty_display):
                    total_qty_pdf += float(qty_display)
                    qty_str = f"{qty_display:.2f}" if (isinstance(qty_display, float) and not float(qty_display).is_integer()) else f"{int(qty_display)}"
                else:
                    qty_str = "-"

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

                table_data = [
                    ["Stitch Type", entry["Stitch Type"]],
                    ["Width (inches)", entry["Width (inches)"]],
                    ["Height (inches)", entry["Height (inches)"]],
                    ["Quantity", qty_str],
                    ["Track (ft)", track_str],
                    ["SQFT", sqft_str]
                ]
                table = Table(table_data, colWidths=[140, 320])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (1, 0), colors.lightgrey),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                    ('FONT', (0,0), (-1,-1), 'Helvetica', 11),
                ]))
                story.append(table)
                story.append(Spacer(1, 12))

            # Totals section in PDF
            story.append(Spacer(1, 12))
            totals_table = [
                ["Total Fabric Quantity", f"{total_qty_pdf:.2f}" if not float(total_qty_pdf).is_integer() else f"{int(total_qty_pdf)}"],
                ["Total Track (ft)", f"{total_track_pdf:.1f} ft"],
                ["Total SQFT (Roman & Regular Blinds)", f"{total_sqft_pdf:.1f} sq.ft"]
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
