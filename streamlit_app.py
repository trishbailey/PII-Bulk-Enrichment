import os
import io
import re
import html
import time
import difflib
from typing import Dict, List, Tuple, Any
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
from peopledatalabs import PDLPY

# ---------------------------
# App config (must be first Streamlit call)
# ---------------------------
st.set_page_config(page_title="Get Contact Info", layout="wide")

# ---------------------------
# Helpers & caching
# ---------------------------
@st.cache_data(show_spinner=False)
def load_df_from_bytes(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Load a CSV/XLSX from in-memory bytes exactly once per content hash."""
    bio = io.BytesIO(file_bytes)
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(bio)
    # Explicitly use openpyxl for .xlsx
    return pd.read_excel(bio, engine="openpyxl")


def _norm(s: str) -> str:
    """Normalize strings for fuzzy matching (lowercase, strip punctuation)."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


# ---------------------------
# Input categories & synonyms (for auto-inference)
# ---------------------------
INPUT_CATEGORIES: List[str] = [
    "First and last name together",
    "First Name",
    "Last Name",
    "Middle Name",
    "Location",
    "Street Address",
    "City or Town",
    "State",
    "ZIP Code",
    "Country",
    "Company or Employer",
    "Job Title",
    "Email",
    "Phone Number",
    "Social Media Account 1",
    "Social Media Account 2",
    "Social Media Account 3",
]

CATEGORY_SYNONYMS: Dict[str, set] = {
    "First and last name together": {"name", "full name", "fullname", "first last", "contact name"},
    "First Name": {"first name", "firstname", "fname", "given name", "forename"},
    "Last Name": {"last name", "lastname", "lname", "surname", "family name"},
    "Middle Name": {"middle name", "middlename", "mname", "middle initial", "mi"},
    "Location": {"location", "loc"},
    "Street Address": {"street address", "address", "address line 1", "address1", "street", "line1"},
    "City or Town": {"city", "town", "locality", "municipality"},
    "State": {"state", "province", "region"},
    "ZIP Code": {"zip", "zip code", "zipcode", "postal code", "postcode"},
    "Country": {"country", "nation"},
    "Company or Employer": {"company", "employer", "organization", "org", "workplace", "current company"},
    "Job Title": {"job title", "title", "position", "role"},
    "Email": {"email", "email address", "email_address", "e-mail", "work email", "personal email"},
    "Phone Number": {"phone", "phone number", "phone_number", "mobile", "cell", "telephone", "tel"},
    "Social Media Account 1": {"linkedin", "linkedin url", "linkedin profile", "profile url"},
    "Social Media Account 2": {"facebook", "fb", "facebook url"},
    "Social Media Account 3": {"github", "twitter", "x", "instagram", "website", "url", "profile"},
}

EMAIL_HINT = re.compile(r"@")
PHONE_HINT = re.compile(r"\d{7,}")
ZIP_HINT = re.compile(r"^\d{5}(-\d{4})?$")


def infer_mapping(user_cols: List[str], df_sample: pd.DataFrame, threshold: float = 0.86) -> Dict[str, Tuple[str, float]]:
    """Infer mapping: category -> (best matching user column, confidence)."""
    norm_to_orig = {_norm(c): c for c in user_cols}
    results: Dict[str, Tuple[str, float]] = {}

    for cat, synonyms in CATEGORY_SYNONYMS.items():
        best_col, best_score = None, 0.0
        for norm_col, orig_col in norm_to_orig.items():
            for syn in synonyms:
                score = difflib.SequenceMatcher(None, norm_col, _norm(syn)).ratio()
                if score > best_score:
                    best_score, best_col = score, orig_col

        # Value-based boosts
        if best_col is not None and best_score >= 0.70 and df_sample is not None and best_col in df_sample.columns:
            series = df_sample[best_col].dropna().astype(str).head(50)
            if cat == "Email" and len(series) and series.str.contains(EMAIL_HINT).mean() > 0.6:
                best_score = max(best_score, 0.95)
            elif cat == "Phone Number" and len(series) and series.str.contains(PHONE_HINT).mean() > 0.6:
                best_score = max(best_score, 0.92)
            elif cat == "ZIP Code" and len(series) and series.str.match(ZIP_HINT).mean() > 0.6:
                best_score = max(best_score, 0.92)
            elif cat in {"Social Media Account 1", "Social Media Account 2", "Social Media Account 3"}:
                if len(series) and series.str.contains(r"https?://", case=False).mean() > 0.5:
                    best_score = max(best_score, 0.88)

        if best_col is not None and best_score >= threshold:
            results[cat] = (best_col, round(float(best_score), 2))

    return results


# ---------------------------
# UI <-> API field maps (PDL inputs / outputs)
# ---------------------------
UI_TO_API_INPUTS: Dict[str, str] = {
    "First and last name together": "name",
    "First Name": "first_name",
    "Last Name": "last_name",
    "Middle Name": "middle_name",
    "Location": "location",
    "Street Address": "street_address",
    "City or Town": "locality",
    "State": "region",
    "ZIP Code": "postal_code",
    "Country": "country",
    "Company or Employer": "company",
    "Job Title": "job_title",
    "Email": "email",
    "Phone Number": "phone",
    "Social Media Account 1": "profile",
    "Social Media Account 2": "profile",
    "Social Media Account 3": "profile",
}

UI_TO_DATA_INCLUDE: Dict[str, str] = {
    "Full Name": "full_name",
    "Birth Date": "birth_date",
    "Emails": "emails.address",
    "Phone Numbers": "phone_numbers",
    "LinkedIn URL": "linkedin_url",
    "Facebook URL": "facebook_url",
    "GitHub URL": "github_url",
    "Other Social Media": "profiles.url",
    "Street Address": "location_street_address",
    "City or Town": "location_locality",
    "State": "location_region",
    "ZIP Code": "location_postal_code",
    "Country": "location_country",
    "Job Title": "job_title",
    "Company or Employer": "job_company_name",
}


def build_params(row: Dict[str, str], input_mapping: Dict[str, str], selected_outputs: List[str]) -> Dict[str, Any]:
    """Build API parameters with proper types and data_include support."""
    params: Dict[str, Any] = {}
    list_fields = {"profile", "email", "phone", "name"}  # multi-valued inputs supported as lists
    list_acc: Dict[str, List[str]] = {k: [] for k in list_fields}

    for ui_cat, user_col in input_mapping.items():
        if user_col not in row:
            continue
        val = str(row[user_col]).strip()
        if not val or val.lower() == "nan":
            continue
        api_key = UI_TO_API_INPUTS.get(ui_cat)
        if not api_key:
            continue

        # Normalize phone: require +country code; default +1 for 10/11-digit US-like numbers
        if api_key == "phone" and not val.startswith("+"):
            if val.startswith("1") and len(val) == 11 and val.isdigit():
                val = "+" + val
            elif len(val) == 10 and val.isdigit():
                val = "+1" + val
            else:
                # skip invalid international numbers lacking a leading +
                continue

        if api_key in list_fields:
            list_acc[api_key].append(val)
        else:
            params[api_key] = val

    for k, vals in list_acc.items():
        if vals:
            params[k] = vals

    inc = [UI_TO_DATA_INCLUDE[x] for x in selected_outputs if x in UI_TO_DATA_INCLUDE]
    if inc:
        params["data_include"] = ",".join(inc)

    params["pretty"] = True
    return params


def _params_key(d: Dict) -> Tuple:
    """Hashable key from params dict for in-run de-dup."""
    items = []
    for k, v in sorted(d.items()):
        if isinstance(v, list):
            items.append((k, tuple(sorted(v))))
        else:
            items.append((k, str(v)))
    return tuple(items)


def call_with_backoff(fn, *args, retries: int = 3, base: float = 0.6, **kwargs):
    """Call function with exponential backoff."""
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(base * (2 ** attempt))


# ---------------------------
# Secrets / API init (PRODUCTION)
# ---------------------------
api_key = st.secrets.get("PDL_API_KEY") or os.getenv("PDL_API_KEY")
if not api_key:
    st.error("No PDL API key found. Add PDL_API_KEY to Streamlit Secrets or environment.")
    st.stop()

client = PDLPY(api_key=api_key, sandbox=False)  # Production mode — charges apply

# ---------------------------
# Styles
# ---------------------------
st.markdown(
    """
<style>
h1 { margin-top: 12px !important; }
.upload-box, .middle-box, .right-box { background: none; border-radius: 0; padding: 0; margin-bottom: 0; }
.step-pill { display: inline-block; background: linear-gradient(90deg, #4F8BF9 0%, #6FC3F7 100%);
             color: #fff; font-size: 1.38rem; font-weight: 700; padding: 16px 40px; border-radius: 32px;
             margin-bottom: 24px; box-shadow: 0 2px 8px rgba(79,139,249,0.10); }
div[data-testid="stFileUploader"] button { color: #333 !important; font-weight: normal !important; border-radius: 8px !important;
             border: 1px solid #ddd !important; background: transparent !important; box-shadow: none !important; }
div.right-box button { background-color: #f5f5f5 !important; color: #333 !important; font-weight: normal !important; border-radius: 8px !important;
             border: 1px solid #ddd !important; box-shadow: none !important; }
div[data-testid="stFileUploader"] div:nth-child(2),
div[data-testid="stFileUploader"] .uploadedFileInfo,
div[data-testid="stFileUploader"] .drag-and-drop-area { display: none !important; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("<h1 style='text-align: center; color: #4F8BF9;'>Get Contact Info</h1>", unsafe_allow_html=True)

# ---------------------------
# Layout & state
# ---------------------------
col1, col2, col3, col4 = st.columns([1.7, 2.1, 2.1, 1.7])

if "df" not in st.session_state:
    st.session_state.df = None
if "input_cols" not in st.session_state:
    st.session_state.input_cols = []
if "row_count" not in st.session_state:
    st.session_state.row_count = 0

selected_outputs: List[str] = []
input_mapping: Dict[str, str] = {}

# ---------------------------
# Step 1 — Upload
# ---------------------------
with col1:
    st.markdown("<div class='upload-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 1: Upload</div>", unsafe_allow_html=True)

    uploaded_file = st.file_uploader("Upload your CSV or Excel file (max 100 rows)", type=["csv", "xlsx"])

    if uploaded_file is not None:
        try:
            file_bytes = uploaded_file.getvalue()
            df_full = load_df_from_bytes(file_bytes, uploaded_file.name)
            df = df_full.head(100).copy()

            st.session_state.df = df
            st.session_state.row_count = len(df)
            st.session_state.input_cols = list(df.columns)

            col_list = "<br>".join(f"- {html.escape(str(c))}" for c in st.session_state.input_cols)
            st.markdown(
                f"""
                <div style='background-color:#e6f4ea;padding:16px;border-radius:8px;border:1px solid #b7e4c7;'>
                    <span style='font-size:1.1rem;font-weight:600;color:#2e7d32;'>File Upload Successful 🎉</span><br>
                    <span style='color:#222;'>{st.session_state.row_count} rows (capped at 100)</span><br>
                    <span style='color:#222;'>Columns:</span><br>
                    <span style='color:#222;'>{col_list}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"File upload failed: {str(e)}")
            st.session_state.df = None
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Step 2 — Map Inputs (auto-infer + human confirmation)
# ---------------------------
with col2:
    st.markdown("<div class='middle-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 2: Select Inputs</div>", unsafe_allow_html=True)
    st.markdown("<small style='color: #000;'>Map your column names to the input fields below. Suggestions include a confidence score; review before running.</small>", unsafe_allow_html=True)

    if st.session_state.df is not None:
        user_cols = st.session_state.input_cols
        df_preview = st.session_state.df
        suggested = infer_mapping(user_cols, df_preview)

        column_options = ["(Do not include)"] + user_cols
        for cat in INPUT_CATEGORIES:
            default_col = suggested.get(cat, (None, None))[0] or "(Do not include)"
            idx = column_options.index(default_col) if default_col in column_options else 0
            label = cat
            if default_col != "(Do not include)" and cat in suggested:
                label += f" — suggested: {default_col} (conf {suggested[cat][1]})"
            sel = st.selectbox(label, options=column_options, index=idx, key=f"map_{cat}")
            if sel != "(Do not include)":
                input_mapping[cat] = sel

        with st.expander("ℹ️ Valid input combinations"):
            st.markdown(
                """
                **PDL requires one of these parameter combinations for a match:**
                - Email address
                - Phone number (with country code)
                - Social media profile URL
                - Name + (Company OR Location OR Address)

                Without a valid combination, the API won't return results.
                """
            )
    else:
        st.info("Please upload a file first.")

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Step 3 — Select Outputs
# ---------------------------
with col3:
    st.markdown("<div class='middle-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 3: Select Outputs</div>", unsafe_allow_html=True)
    st.markdown("<small style='color: #000;'>Choose output fields you want. Uses PDL's 'data_include' parameter to limit response size.</small>", unsafe_allow_html=True)

    OUTPUT_FIELDS = [
        "Full Name",
        "Birth Date",
        "Emails",
        "Phone Numbers",
        "LinkedIn URL",
        "Facebook URL",
        "GitHub URL",
        "Other Social Media",
        "Street Address",
        "City or Town",
        "State",
        "ZIP Code",
        "Country",
        "Job Title",
        "Company or Employer",
    ]

    selected_outputs = []
    for field in OUTPUT_FIELDS:
        if st.checkbox(field, key=f"out_{field}"):
            selected_outputs.append(field)

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Step 4 — Enrich (controls)
# ---------------------------
with col4:
    st.markdown("<div class='right-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 4: Enrich</div>", unsafe_allow_html=True)
    st.markdown("<small style='color: #000;'>Click to run enrichment.</small>", unsafe_allow_html=True)

    max_workers = st.slider(
        "Max parallel requests",
        min_value=1,
        max_value=20,
        value=4,  # reasonable default for production; tune to your rate limits
        help="Adjust based on your PDL plan's rate limits."
    )
    run_enrich = st.button("✨ Run enrichment", key="enrich_btn")

    # Container for success/error messages
    result_container = st.container()

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Enrichment execution
# ---------------------------
if run_enrich:
    if st.session_state.df is None:
        with result_container:
            st.error("Please upload a file first.")
    elif not input_mapping:
        with result_container:
            st.error("Please map at least one input field before running.")
    else:
        df_to_enrich = st.session_state.df
        total = len(df_to_enrich)
        if total == 0:
            with result_container:
                st.warning("No rows to enrich.")
        else:
            progress = st.progress(0.0)
            memo: Dict[Tuple, Dict] = {}

            def one(row_dict: Dict) -> Dict:
                """Call PDL API for one row."""
                params = build_params(row_dict, input_mapping, selected_outputs)
                if not params:
                    return {"error": "No valid parameters built for this row."}
                key = _params_key(params)
                if key in memo:
                    return memo[key]

                try:
                    resp = call_with_backoff(client.person.enrichment, **params)
                    try:
                        j = resp.json()
                    except Exception:
                        j = None

                    if getattr(resp, "status_code", 0) != 200:
                        data = {"error": f"HTTP {resp.status_code}", "body": j or getattr(resp, "text", "")}
                    else:
                        # Some PDL responses include a top-level "status"
                        if isinstance(j, dict) and j.get("status") not in (None, 200):
                            data = {"error": j.get("error", j)}
                        else:
                            data = (j or {}).get("data", j or {})
                except Exception as e:
                    data = {"error": str(e)}

                memo[key] = data
                return data

            results_map: Dict[int, Dict] = {}

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = [(idx, ex.submit(one, row.to_dict())) for idx, row in df_to_enrich.iterrows()]
                for i, (idx, fut) in enumerate(futures, 1):
                    try:
                        results_map[idx] = fut.result()
                    except Exception as e:
                        results_map[idx] = {"error": str(e)}
                    progress.progress(i / total)

            # Build cleaned output rows efficiently
            cleaned_rows: List[Dict[str, Any]] = []
            for idx, row in df_to_enrich.iterrows():
                cleaned_row: Dict[str, Any] = {}

                # Original inputs
                for col in df_to_enrich.columns:
                    cleaned_row[col] = row[col]

                enrichment = results_map.get(idx, {})

                if "error" in enrichment:
                    cleaned_row["Error"] = enrichment.get("error", "Unknown error")
                    cleaned_rows.append(cleaned_row)
                    continue

                # Name fields
                if enrichment.get("full_name"):
                    cleaned_row["Full_Name"] = enrichment.get("full_name", "")
                if enrichment.get("first_name"):
                    cleaned_row["PDL_First_Name"] = enrichment.get("first_name", "")
                if enrichment.get("last_name"):
                    cleaned_row["PDL_Last_Name"] = enrichment.get("last_name", "")

                # Emails
                emails = enrichment.get("emails", [])
                if isinstance(emails, list) and emails:
                    email_list = []
                    for email in emails:
                        if isinstance(email, dict) and "address" in email:
                            email_list.append(email["address"])
                        elif isinstance(email, str):
                            email_list.append(email)
                    if email_list:
                        cleaned_row["Emails"] = "; ".join(email_list)

                # Phones
                phones = enrichment.get("phone_numbers", [])
                if isinstance(phones, list) and phones:
                    phone_list = []
                    for ph in phones:
                        if isinstance(ph, dict) and "number" in ph:
                            phone_list.append(ph["number"])
                        elif isinstance(ph, str):
                            phone_list.append(ph)
                    if phone_list:
                        cleaned_row["Phone_Numbers"] = "; ".join(phone_list)

                # Social profiles
                if enrichment.get("linkedin_url"):
                    cleaned_row["LinkedIn_URL"] = enrichment.get("linkedin_url", "")
                if enrichment.get("facebook_url"):
                    cleaned_row["Facebook_URL"] = enrichment.get("facebook_url", "")
                if enrichment.get("github_url"):
                    cleaned_row["GitHub_URL"] = enrichment.get("github_url", "")

                # Location
                if enrichment.get("location_street_address"):
                    cleaned_row["Street_Address"] = enrichment.get("location_street_address", "")
                if enrichment.get("location_locality"):
                    cleaned_row["City"] = enrichment.get("location_locality", "")
                if enrichment.get("location_region"):
                    cleaned_row["State"] = enrichment.get("location_region", "")
                if enrichment.get("location_postal_code"):
                    cleaned_row["Postal_Code"] = enrichment.get("location_postal_code", "")
                if enrichment.get("location_country"):
                    cleaned_row["Country"] = enrichment.get("location_country", "")

                # Work
                if enrichment.get("job_title"):
                    cleaned_row["Job_Title"] = enrichment.get("job_title", "")
                if enrichment.get("job_company_name"):
                    cleaned_row["Company"] = enrichment.get("job_company_name", "")

                # Birth date
                if enrichment.get("birth_date"):
                    cleaned_row["Birth_Date"] = enrichment.get("birth_date", "")

                # Likelihood
                if enrichment.get("likelihood"):
                    cleaned_row["Match_Score"] = enrichment.get("likelihood", "")

                cleaned_rows.append(cleaned_row)

            cleaned_df = pd.DataFrame(cleaned_rows)
            st.session_state["results_df"] = cleaned_df

            with result_container:
                st.success(f"✅ Enrichment successful! Processed {len(results_map)} rows.")

            # Prepare downloadable artifacts and persist in session
            st.session_state["csv_data"] = cleaned_df.to_csv(index=False).encode("utf-8")

            try:
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    cleaned_df.to_excel(writer, index=False, sheet_name="Enriched_Data")
                    worksheet = writer.sheets["Enriched_Data"]
                    for i, col in enumerate(cleaned_df.columns):
                        max_len = max(cleaned_df[col].astype(str).str.len().max(), len(col)) + 2
                        worksheet.set_column(i, i, min(max_len, 50))
                st.session_state["excel_data"] = excel_buffer.getvalue()
            except Exception as e:
                st.warning(f"Excel export unavailable: {e}")
                st.session_state["excel_data"] = None

# ---------------------------
# Always-on Download buttons (persist across reruns)
# ---------------------------
with col4:
    if "csv_data" in st.session_state or st.session_state.get("excel_data"):
        st.markdown("### Downloads")
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "📄 Download CSV",
                data=st.session_state.get("csv_data", b""),
                file_name="enriched_results.csv",
                mime="text/csv",
                key="csv_download",
            )
        with dl2:
            if st.session_state.get("excel_data"):
                st.download_button(
                    "📊 Download Excel",
                    data=st.session_state["excel_data"],
                    file_name="enriched_results.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="excel_download",
                )
