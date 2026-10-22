import html
import sqlite3
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PDFImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "duperman_jobs.db"
STATUSES = [
    "Estimate",
    "Approved",
    "In progress",
    "Completed",
    "Invoiced - unpaid",
    "Paid",
]
UPLOAD_CATEGORIES = ["Before photos", "Completion photos", "Receipts", "Other documents"]
JOB_FIELDS = [
    "customer", "phone", "email", "address", "job_title", "description",
    "labor_hours", "labor_rate", "materials", "materials_markup", "gas",
    "miles", "mileage_rate", "other_cost", "equipment_cost", "overhead",
    "profit", "tax", "notes",
]
NUMERIC_FIELDS = {
    "labor_hours", "labor_rate", "materials", "materials_markup", "gas",
    "miles", "mileage_rate", "other_cost", "equipment_cost", "overhead",
    "profit", "tax", "actual_labor_hours", "actual_materials", "actual_gas",
    "actual_miles", "actual_other", "actual_equipment",
}
COLUMN_DEFINITIONS = {
    "estimate_no": "TEXT",
    "created": "TEXT",
    "updated": "TEXT",
    "customer": "TEXT DEFAULT ''",
    "phone": "TEXT DEFAULT ''",
    "email": "TEXT DEFAULT ''",
    "address": "TEXT DEFAULT ''",
    "job_title": "TEXT DEFAULT ''",
    "description": "TEXT DEFAULT ''",
    "labor_hours": "REAL DEFAULT 0",
    "labor_rate": "REAL DEFAULT 0",
    "materials": "REAL DEFAULT 0",
    "materials_markup": "REAL DEFAULT 0",
    "gas": "REAL DEFAULT 0",
    "miles": "REAL DEFAULT 0",
    "mileage_rate": "REAL DEFAULT 0",
    "other_cost": "REAL DEFAULT 0",
    "equipment_cost": "REAL DEFAULT 0",
    "overhead": "REAL DEFAULT 0",
    "profit": "REAL DEFAULT 0",
    "tax": "REAL DEFAULT 0",
    "notes": "TEXT DEFAULT ''",
    "total": "REAL DEFAULT 0",
    "status": "TEXT DEFAULT 'Estimate'",
    "actual_labor_hours": "REAL DEFAULT 0",
    "actual_materials": "REAL DEFAULT 0",
    "actual_gas": "REAL DEFAULT 0",
    "actual_miles": "REAL DEFAULT 0",
    "actual_other": "REAL DEFAULT 0",
    "actual_equipment": "REAL DEFAULT 0",
    "actual_total": "REAL DEFAULT 0",
}


def connect_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def setup_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        )
        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
        }
        for column, definition in COLUMN_DEFINITIONS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                original_name TEXT NOT NULL,
                storage_name TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_job ON attachments(job_id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_estimate_no "
            "ON jobs(estimate_no) WHERE estimate_no IS NOT NULL"
        )
        legacy_jobs = connection.execute(
            "SELECT id, estimate_no, created FROM jobs"
        ).fetchall()
        for job in legacy_jobs:
            if not job["estimate_no"]:
                date_part = (job["created"] or "").replace("-", "")[:8]
                if len(date_part) != 8 or not date_part.isdigit():
                    date_part = "LEGACY"
                connection.execute(
                    "UPDATE jobs SET estimate_no = ? WHERE id = ?",
                    (f"DS-{date_part}-{job['id']:05d}", job["id"]),
                )
            if not job["created"]:
                connection.execute(
                    "UPDATE jobs SET created = ? WHERE id = ?",
                    (datetime.now().strftime("%Y-%m-%d"), job["id"]),
                )


def calculate(values):
    values = dict(values)
    labor = float(values.get("labor_hours", 0)) * float(values.get("labor_rate", 0))
    raw_materials = float(values.get("materials", 0))
    markup = raw_materials * float(values.get("materials_markup", 0)) / 100
    materials_total = raw_materials + markup
    fuel = float(values.get("gas", 0))
    mileage = float(values.get("miles", 0)) * float(values.get("mileage_rate", 0))
    other = float(values.get("other_cost", 0))
    equipment = float(values.get("equipment_cost", 0))
    direct = labor + materials_total + fuel + mileage + other + equipment
    overhead = direct * float(values.get("overhead", 0)) / 100
    cost_with_overhead = direct + overhead
    profit = cost_with_overhead * float(values.get("profit", 0)) / 100
    subtotal = cost_with_overhead + profit
    tax = subtotal * float(values.get("tax", 0)) / 100
    total = subtotal + tax
    return {
        "Labor": labor,
        "Materials": raw_materials,
        "Materials markup": markup,
        "Gas and fuel": fuel,
        "Mileage": mileage,
        "Other expenses": other,
        "Equipment": equipment,
        "Overhead": overhead,
        "Profit": profit,
        "Subtotal before tax": subtotal,
        "Tax": tax,
        "Estimated total": total,
    }


def actual_costs(job):
    labor = float(job["actual_labor_hours"] or 0) * float(job["labor_rate"] or 0)
    mileage = float(job["actual_miles"] or 0) * float(job["mileage_rate"] or 0)
    return {
        "Labor": labor,
        "Materials": float(job["actual_materials"] or 0),
        "Gas and fuel": float(job["actual_gas"] or 0),
        "Mileage": mileage,
        "Other expenses": float(job["actual_other"] or 0),
        "Equipment": float(job["actual_equipment"] or 0),
    }


def money(amount):
    return f"${float(amount or 0):,.2f}"


def safe_paragraph(text):
    return html.escape(str(text or "")).replace("\n", "<br/>")


def pdf_table(rows, widths):
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173c35")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d5ded9")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
    ]))
    return table


def make_pdf(job, report=False, attachments=None):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"Duperman Services - {job['estimate_no']}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="RightSmall", parent=styles["Normal"], alignment=TA_RIGHT,
        textColor=colors.HexColor("#49625b"),
    ))
    title = "FINAL JOB REPORT" if report else "JOB ESTIMATE"
    story = [
        Paragraph("Duperman Services", styles["Title"]),
        Paragraph(title, styles["Heading2"]),
        Spacer(1, 8),
        Paragraph(
            f"Estimate: {safe_paragraph(job['estimate_no'])}"
            f"<br/>Date: {safe_paragraph(job['created'])}"
            f"<br/>Status: {safe_paragraph(job['status'])}",
            styles["Normal"],
        ),
        Spacer(1, 12),
        Paragraph("Customer", styles["Heading2"]),
        Paragraph(
            f"{safe_paragraph(job['customer'])}<br/>"
            f"{safe_paragraph(job['phone'])}"
            f"{'<br/>' + safe_paragraph(job['email']) if job['email'] else ''}<br/>"
            f"{safe_paragraph(job['address'])}",
            styles["Normal"],
        ),
        Spacer(1, 10),
        Paragraph("Job Details", styles["Heading2"]),
        Paragraph(f"<b>{safe_paragraph(job['job_title'])}</b>", styles["Normal"]),
        Paragraph(safe_paragraph(job["description"]), styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Cost Breakdown", styles["Heading2"]),
    ]

    estimated = calculate(job)
    estimate_rows = [["Item", "Amount"]]
    for label, amount in estimated.items():
        estimate_rows.append([label, money(amount)])
    story.append(pdf_table(estimate_rows, [4.9 * inch, 1.5 * inch]))

    if report:
        actual = actual_costs(job)
        actual_total = sum(actual.values())
        story.extend([
            Spacer(1, 12),
            Paragraph("Actual Expenses", styles["Heading2"]),
        ])
        actual_rows = [["Item", "Actual", "Estimate", "Difference"]]
        estimated_by_actual = {
            "Labor": estimated["Labor"],
            "Materials": estimated["Materials"] + estimated["Materials markup"],
            "Gas and fuel": estimated["Gas and fuel"],
            "Mileage": estimated["Mileage"],
            "Other expenses": estimated["Other expenses"],
            "Equipment": estimated["Equipment"],
        }
        for label, amount in actual.items():
            estimate_amount = estimated_by_actual[label]
            actual_rows.append([
                label, money(amount), money(estimate_amount),
                money(amount - estimate_amount),
            ])
        actual_rows.append(["Actual direct cost total", money(actual_total), "", ""])
        story.append(pdf_table(actual_rows, [2.4 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch]))

    if job["notes"]:
        story.extend([
            Spacer(1, 12),
            Paragraph("Notes and Payment Terms", styles["Heading2"]),
            Paragraph(safe_paragraph(job["notes"]), styles["Normal"]),
        ])

    if report:
        grouped = {category: [] for category in UPLOAD_CATEGORIES}
        for attachment in attachments or []:
            grouped.setdefault(attachment["category"], []).append(attachment)
        story.extend([Spacer(1, 12), Paragraph("Uploaded Files", styles["Heading2"])])
        for category, files in grouped.items():
            if not files:
                continue
            story.append(Paragraph(safe_paragraph(category), styles["Heading3"]))
            for attachment in files:
                story.append(Paragraph(
                    f"{safe_paragraph(attachment['original_name'])}"
                    f" - uploaded {safe_paragraph(attachment['uploaded_at'])}",
                    styles["Normal"],
                ))
                if category in ("Completion photos", "Receipts"):
                    stored_file = UPLOAD_DIR / attachment["storage_name"]
                    if stored_file.is_file():
                        try:
                            with PILImage.open(stored_file) as source:
                                image_buffer = BytesIO()
                                source.convert("RGB").save(image_buffer, format="JPEG")
                            image_buffer.seek(0)
                            image = PDFImage(image_buffer, width=3.0 * inch, height=2.0 * inch)
                            image._restrictSize(3.0 * inch, 2.0 * inch)
                            story.extend([image, Spacer(1, 6)])
                        except (OSError, ValueError):
                            pass

    document.build(story)
    return buffer.getvalue()


def get_jobs():
    with connect_db() as connection:
        return connection.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()


def get_job(job_id):
    with connect_db() as connection:
        return connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_attachments(job_id):
    with connect_db() as connection:
        return connection.execute(
            "SELECT * FROM attachments WHERE job_id = ? ORDER BY id DESC", (job_id,)
        ).fetchall()


def save_job(values, job_id=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = calculate(values)["Estimated total"]
    with connect_db() as connection:
        if job_id is None:
            created = datetime.now().strftime("%Y-%m-%d")
            columns = JOB_FIELDS + ["created", "updated", "total", "status"]
            data = [values[field] for field in JOB_FIELDS] + [created, now, total, "Estimate"]
            placeholders = ", ".join("?" for _ in columns)
            cursor = connection.execute(
                f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders})", data
            )
            new_id = cursor.lastrowid
            estimate_no = f"DS-{datetime.now():%Y%m%d}-{new_id:05d}"
            connection.execute(
                "UPDATE jobs SET estimate_no = ? WHERE id = ?", (estimate_no, new_id)
            )
            return new_id
        assignments = [f"{field} = ?" for field in JOB_FIELDS]
        assignments.extend(["updated = ?", "total = ?"])
        data = [values[field] for field in JOB_FIELDS] + [now, total, job_id]
        connection.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", data
        )
        return job_id


def save_actuals(job_id, status, values):
    with connect_db() as connection:
        connection.execute(
            """UPDATE jobs SET status = ?, actual_labor_hours = ?,
                actual_materials = ?, actual_gas = ?, actual_miles = ?,
                actual_other = ?, actual_equipment = ?, actual_total = ?, updated = ?
                WHERE id = ?""",
            (
                status, values["actual_labor_hours"], values["actual_materials"],
                values["actual_gas"], values["actual_miles"], values["actual_other"],
                values["actual_equipment"], sum(actual_costs({**values, "labor_rate": values["labor_rate"], "mileage_rate": values["mileage_rate"]}).values()),
                datetime.now().strftime("%Y-%m-%d %H:%M"), job_id,
            ),
        )


def upload_files(job_id, category, files):
    saved = 0
    for uploaded in files:
        extension = Path(uploaded.name).suffix.lower()
        storage_name = f"{uuid.uuid4().hex}{extension}"
        destination = UPLOAD_DIR / storage_name
        destination.write_bytes(uploaded.getvalue())
        original_name = Path(uploaded.name).name[:255]
        try:
            with connect_db() as connection:
                connection.execute(
                    """INSERT INTO attachments
                        (job_id, category, original_name, storage_name, uploaded_at)
                        VALUES (?, ?, ?, ?, ?)""",
                    (
                        job_id, category, original_name, storage_name,
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    ),
                )
            saved += 1
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return saved


def render_cost_breakdown(costs):
    for label, amount in costs.items():
        if label == "Estimated total":
            st.success(f"Estimated total: {money(amount)}")
        elif label == "Subtotal before tax":
            st.markdown(f"**{label}: {money(amount)}**")
        else:
            st.write(f"{label}: {money(amount)}")


def numeric_field(label, key, value, step, prefix, help_text=None):
    return st.number_input(
        label,
        min_value=0.0,
        value=float(value or 0),
        step=step,
        key=f"{prefix}_{key}",
        help=help_text,
    )


def text_field(label, key, value, prefix, multiline=False, height=None):
    if multiline:
        return st.text_area(
            label, value=value or "", height=height,
            key=f"{prefix}_{key}",
        )
    return st.text_input(label, value=value or "", key=f"{prefix}_{key}")


def render_editor(jobs):
    st.header("Create or edit an estimate")
    choices = {"New estimate": None}
    for job in jobs:
        choices[
            f"{job['estimate_no']} | {job['customer'] or 'New customer'} | {job['job_title'] or 'Untitled job'}"
        ] = job["id"]
    selection = st.selectbox("Estimate", list(choices.keys()))
    job_id = choices[selection]
    job = get_job(job_id) if job_id else None
    defaults = dict(job) if job else {}
    prefix = f"estimate_{job_id or 'new'}"

    st.subheader("Customer information")
    col_a, col_b = st.columns(2)
    with col_a:
        customer = text_field("Customer name", "customer", defaults.get("customer"), prefix)
        phone = text_field("Phone number", "phone", defaults.get("phone"), prefix)
        email = text_field("Email", "email", defaults.get("email"), prefix)
    with col_b:
        address = text_field(
            "Job address", "address", defaults.get("address"), prefix,
            multiline=True, height=105,
        )

    st.subheader("Job details")
    job_title = text_field("Job title", "job_title", defaults.get("job_title"), prefix)
    description = text_field(
        "Problem and work required", "description", defaults.get("description"),
        prefix, multiline=True, height=120,
    )

    st.subheader("Estimated costs")
    left, right = st.columns(2)
    with left:
        labor_hours = numeric_field("Labor hours", "labor_hours", defaults.get("labor_hours"), 0.5, prefix)
        labor_rate = numeric_field("Hourly labor rate ($)", "labor_rate", defaults.get("labor_rate"), 5.0, prefix)
        materials = numeric_field("Materials cost ($)", "materials", defaults.get("materials"), 10.0, prefix)
        materials_markup = numeric_field("Materials markup (%)", "materials_markup", defaults.get("materials_markup"), 5.0, prefix)
        gas = numeric_field("Gas and fuel ($)", "gas", defaults.get("gas"), 5.0, prefix)
        miles = numeric_field("Miles traveled", "miles", defaults.get("miles"), 1.0, prefix)
    with right:
        mileage_rate = numeric_field("Mileage rate ($ per mile)", "mileage_rate", defaults.get("mileage_rate"), 0.05, prefix)
        other_cost = numeric_field("Other expenses ($)", "other_cost", defaults.get("other_cost"), 5.0, prefix)
        equipment_cost = numeric_field("Equipment costs ($)", "equipment_cost", defaults.get("equipment_cost"), 5.0, prefix)
        overhead = numeric_field("Overhead (%)", "overhead", defaults.get("overhead"), 1.0, prefix)
        profit = numeric_field("Profit margin (%)", "profit", defaults.get("profit"), 1.0, prefix)
        tax = numeric_field("Tax (%)", "tax", defaults.get("tax"), 0.1, prefix)

    notes = text_field(
        "Notes, exclusions, and payment terms", "notes", defaults.get("notes"),
        prefix, multiline=True, height=100,
    )
    values = {
        "customer": customer, "phone": phone, "email": email, "address": address,
        "job_title": job_title, "description": description,
        "labor_hours": labor_hours, "labor_rate": labor_rate,
        "materials": materials, "materials_markup": materials_markup,
        "gas": gas, "miles": miles, "mileage_rate": mileage_rate,
        "other_cost": other_cost, "equipment_cost": equipment_cost,
        "overhead": overhead, "profit": profit, "tax": tax, "notes": notes,
    }
    st.subheader("Live estimate breakdown")
    render_cost_breakdown(calculate(values))
    if st.button("Save changes" if job_id else "Save estimate", type="primary", use_container_width=True):
        saved_id = save_job(values, job_id)
        st.success(f"Estimate {get_job(saved_id)['estimate_no']} saved.")


def render_actuals(job):
    st.subheader("Job status and actual costs")
    prefix = f"actual_{job['id']}"
    current_status = job["status"] if job["status"] in STATUSES else "Estimate"
    with st.form(f"actuals_form_{job['id']}"):
        status = st.selectbox(
            "Status", STATUSES, index=STATUSES.index(current_status), key=f"{prefix}_status"
        )
        col_a, col_b = st.columns(2)
        with col_a:
            hours = st.number_input("Actual labor hours", min_value=0.0, value=float(job["actual_labor_hours"] or 0), step=0.5, key=f"{prefix}_hours")
            materials = st.number_input("Actual materials ($)", min_value=0.0, value=float(job["actual_materials"] or 0), step=5.0, key=f"{prefix}_materials")
            gas = st.number_input("Actual gas and fuel ($)", min_value=0.0, value=float(job["actual_gas"] or 0), step=5.0, key=f"{prefix}_gas")
        with col_b:
            miles = st.number_input("Actual miles", min_value=0.0, value=float(job["actual_miles"] or 0), step=1.0, key=f"{prefix}_miles")
            other = st.number_input("Actual other expenses ($)", min_value=0.0, value=float(job["actual_other"] or 0), step=5.0, key=f"{prefix}_other")
            equipment = st.number_input("Actual equipment costs ($)", min_value=0.0, value=float(job["actual_equipment"] or 0), step=5.0, key=f"{prefix}_equipment")
        submitted = st.form_submit_button("Save status and actual costs", type="primary", use_container_width=True)
    values = {
        "actual_labor_hours": hours, "actual_materials": materials,
        "actual_gas": gas, "actual_miles": miles, "actual_other": other,
        "actual_equipment": equipment, "labor_rate": job["labor_rate"],
        "mileage_rate": job["mileage_rate"],
    }
    if submitted:
        save_actuals(job["id"], status, values)
        st.success("Job tracking details saved.")
        st.rerun()
    return values


def render_attachments(job):
    st.subheader("Photos, receipts, and files")
    categories = st.tabs(UPLOAD_CATEGORIES)
    for tab, category in zip(categories, UPLOAD_CATEGORIES):
        with tab:
            nonce_key = f"upload_nonce_{job['id']}_{category}"
            nonce = st.session_state.get(nonce_key, 0)
            uploader_key = f"upload_{job['id']}_{category}_{nonce}"
            files = st.file_uploader(
                f"Add {category.lower()}",
                accept_multiple_files=True,
                key=uploader_key,
                help="Files are stored in this Codespace workspace and linked to this job.",
            )
            if st.button(
                f"Save {category.lower()}",
                key=f"save_{job['id']}_{category}_{nonce}",
                disabled=not files,
                use_container_width=True,
            ):
                try:
                    count = upload_files(job["id"], category, files)
                    st.session_state[nonce_key] = nonce + 1
                    st.success(f"Saved {count} file(s) to this job.")
                    st.rerun()
                except (OSError, sqlite3.Error) as error:
                    st.error(f"Could not save the upload: {error}")

    attachments = get_attachments(job["id"])
    if not attachments:
        st.info("No files have been uploaded for this job yet.")
        return attachments
    for category in UPLOAD_CATEGORIES:
        category_files = [item for item in attachments if item["category"] == category]
        if not category_files:
            continue
        with st.expander(f"{category} ({len(category_files)})", expanded=True):
            for attachment in category_files:
                stored_file = UPLOAD_DIR / attachment["storage_name"]
                col_file, col_action = st.columns([3, 1])
                with col_file:
                    st.write(f"**{attachment['original_name']}**")
                    st.caption(f"Uploaded {attachment['uploaded_at']}")
                    if stored_file.is_file() and stored_file.suffix.lower() in {
                        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
                    }:
                        st.image(str(stored_file), use_container_width=True)
                with col_action:
                    if stored_file.is_file():
                        mime = "application/pdf" if stored_file.suffix.lower() == ".pdf" else None
                        st.download_button(
                            "Download",
                            data=stored_file.read_bytes(),
                            file_name=attachment["original_name"],
                            mime=mime,
                            key=f"download_attachment_{attachment['id']}",
                            use_container_width=True,
                        )
                    else:
                        st.warning("File is missing")
    return attachments


def render_job_detail(job):
    st.header(f"{job['estimate_no']} · {job['job_title'] or 'Untitled job'}")
    st.caption(f"{job['customer'] or 'No customer name'} · {job['created']} · {job['status']}")
    st.write(job["description"] or "No job description entered.")
    estimated = calculate(job)
    estimate_total = estimated["Estimated total"]
    actual = actual_costs(job)
    actual_total = sum(actual.values())
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Estimated total", money(estimate_total))
    col_b.metric("Actual direct costs", money(actual_total))
    col_c.metric("Actual vs. estimate", money(actual_total - estimate_total))
    with st.expander("Customer and estimate details"):
        st.write(f"**Phone:** {job['phone'] or 'Not entered'}")
        st.write(f"**Email:** {job['email'] or 'Not entered'}")
        st.write(f"**Address:** {job['address'] or 'Not entered'}")
        st.write(f"**Notes and terms:** {job['notes'] or 'None'}")
    st.subheader("Estimated cost breakdown")
    st.dataframe(
        [{"Cost item": label, "Amount": money(amount)} for label, amount in estimated.items()],
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Actual vs. estimated direct costs")
    estimated_by_actual = {
        "Labor": estimated["Labor"],
        "Materials": estimated["Materials"] + estimated["Materials markup"],
        "Gas and fuel": estimated["Gas and fuel"],
        "Mileage": estimated["Mileage"],
        "Other expenses": estimated["Other expenses"],
        "Equipment": estimated["Equipment"],
    }
    st.dataframe(
        [
            {"Cost item": label, "Estimated": money(estimated_by_actual[label]), "Actual": money(actual[label]), "Difference": money(actual[label] - estimated_by_actual[label])}
            for label in actual
        ],
        hide_index=True,
        use_container_width=True,
    )
    render_actuals(job)
    attachments = render_attachments(job)
    left, right = st.columns(2)
    with left:
        st.download_button(
            "Download estimate PDF",
            data=make_pdf(job),
            file_name=f"{job['estimate_no']}_estimate.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with right:
        st.download_button(
            "Download final job report PDF",
            data=make_pdf(job, report=True, attachments=attachments),
            file_name=f"{job['estimate_no']}_final_report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def render_dashboard(jobs):
    st.header("Duperman Services")
    st.caption("Job estimates, field costs, and customer documents")
    counts = {status: sum(job["status"] == status for job in jobs) for status in STATUSES}
    columns = st.columns(3)
    columns[0].metric("Open estimates", counts["Estimate"])
    columns[1].metric("Approved / in progress", counts["Approved"] + counts["In progress"])
    columns[2].metric("Unpaid invoices", counts["Invoiced - unpaid"])
    st.subheader("Recent jobs")
    if not jobs:
        st.info("No estimates saved yet. Create your first estimate from the navigation menu.")
        return
    st.dataframe(
        [
            {
                "Estimate": job["estimate_no"],
                "Date": job["created"],
                "Customer": job["customer"],
                "Job": job["job_title"],
                "Status": job["status"],
                "Estimated total": money(job["total"]),
            }
            for job in jobs[:10]
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("All status counts")
    st.write(" · ".join(f"{status}: {counts[status]}" for status in STATUSES))


def main():
    st.set_page_config(
        page_title="Duperman Services Job Estimator",
        page_icon="DS",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """<style>
        :root { --ds-green: #173c35; --ds-mint: #e9f1ed; --ds-ink: #202b28; }
        .stApp { color: var(--ds-ink); }
        [data-testid="stSidebar"] { background: #f2f6f3; }
        div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] {
            background: var(--ds-green); border-color: var(--ds-green); min-height: 2.8rem;
        }
        [data-testid="stMetric"] { background: var(--ds-mint); padding: .8rem; border-radius: 6px; }
        @media (max-width: 700px) { .block-container { padding: 1rem .8rem 2rem; } }
        </style>""",
        unsafe_allow_html=True,
    )
    setup_database()
    jobs = get_jobs()
    st.sidebar.title("Duperman Services")
    page = st.sidebar.radio("Navigation", ["Overview", "New / Edit Estimate", "Jobs & Files"])
    st.sidebar.caption("Your jobs are saved in the workspace database.")

    if page == "Overview":
        render_dashboard(jobs)
    elif page == "New / Edit Estimate":
        render_editor(jobs)
    else:
        st.header("Saved jobs and files")
        if not jobs:
            st.info("Create an estimate first to add photos, receipts, and job updates.")
            return
        job_by_label = {
            f"{job['estimate_no']} | {job['customer'] or 'New customer'} | {job['job_title'] or 'Untitled job'}": job["id"]
            for job in jobs
        }
        selected = st.selectbox("Choose a job", list(job_by_label.keys()))
        render_job_detail(get_job(job_by_label[selected]))


setup_database()
main()
