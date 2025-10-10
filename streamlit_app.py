import os
import io
import re
import html
import json
import time
import difflib
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st
import streamlit_confetti as stc
from pdl_client import PDLClient

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
    # Requires openpyxl for .xlsx
    return pd.read_excel(bio)


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
    """Infer mapping: category -> (best matching user column, confidence).

    - Lexical similarity against synonyms
    - Value-based hints (emails, phones, ZIPs, URLs)
    - Only return matches at/above threshold
    """
    norm_to_orig = {_norm(c): c for c in user_cols}
    results: Dict[str, Tuple[str, float]] = {}

    for cat, synonyms in CATEGORY_SYNONYMS.items():
        best_col, best_score = None, 0.0
        for norm_col, orig_col in norm_to_orig.items():
            for syn in synonyms:
                score = difflib.SequenceMatcher(None, norm_col, _norm(syn)).ratio()
                if score > best_score:
                    best_score, best_col = score, orig_col

        # Value-based boosts using a small sample
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
# UI <-> API field maps (verify against your PDL SDK)
# ---------------------------
# NOTE: These keys are COMMON patterns but may not match your exact PDL parameters.
# Verify in your environment and adjust as needed. If a key is wrong, PDL may ignore it
# or return an error. Using a map lets you change the API param names in one place.
UI_TO_API_INPUTS: Dict[str, str] = {
    "First and last name together": "name",
    "First Name": "first_name",
    "Last Name": "last_name",
    "Middle Name": "middle_name",
    "Location": "location",
    "Street Address": "street_address",
    "City or Town": "city",
    "State": "state",
    "ZIP Code": "zip",
    "Company or Employer": "company",
    "Job Title": "job_title",
    "Email": "email",
    "Phone Number": "phone",
    "Social Media Account 1": "profile1",
    "Social Media Account 2": "profile2",
    "Social Media Account 3": "profile3",
}

# If you want to limit the response to specific fields, map UI labels -> PDL field names here.
# Leave a label unmapped to exclude it from the `fields` parameter.
UI_TO_API_OUTPUTS: Dict[str, str] = {
    # EXAMPLES — ADJUST/VERIFY in your environment before enabling:
    # "Full Name": "full_name",
    # "Birth Year": "birth_year",
    # "Emails": "emails",
    # "Phone Numbers": "phone_numbers",
    # "LinkedIn URL": "linkedin_url",
    # "Facebook URL": "facebook_url",
    # "GitHub URL": "github_url",
    # "Other Social Media": "profiles",
    # "Street Address": "street_address",
    # "City or Town": "city",
    # "State": "state",
    # "ZIP Code": "zip",
    # "Job Title": "job_title",
    # "Company or Employer": "company",
}


def build_params(row: Dict[str, str], input_mapping: Dict[str, str], selected_outputs: List[str]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for ui_cat, user_col in input_mapping.items():
        if user_col not in row:
            continue
        val = row[user_col]
        if pd.notna(val) and str(val).strip():
            api_key = UI_TO_API_INPUTS.get(ui_cat)
            if api_key:
                params[api_key] = str(val).strip()

    # Only send 'fields' if you've verified UI_TO_API_OUTPUTS mapping
    api_fields = [UI_TO_API_OUTPUTS.get(x) for x in selected_outputs if UI_TO_API_OUTPUTS.get(x)]
    if api_fields:
        params["fields"] = api_fields
    return params


def _params_key(d: Dict) -> Tuple:
    return tuple(sorted((k, str(v)) for k, v in d.items()))


def call_with_backoff(fn, *args, retries: int = 3, base: float = 0.6, **kwargs):
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                raise
            time.sleep(base * (2 ** attempt))


# ---------------------------
# Secrets / API init
# ---------------------------
api_key = st.secrets.get("PDL_API_KEY") or os.getenv("PDL_API_KEY")
if not api_key:
    st.error("No PDL API key found. Add `PDL_API_KEY` to Streamlit Secrets or environment.")
    st.stop()

client = PDLClient(api_key)

# ---------------------------
# Styles
# ---------------------------
st.markdown(
    """
<style>
/* Reduce top margin above app title */
h1 { margin-top: 12px !important; }
/* Remove blue background from step boxes */
.upload-box, .middle-box, .right-box { background: none; border-radius: 0; padding: 0; margin-bottom: 0; }
/* Stylish pill-shaped background for step titles */
.step-pill { display: inline-block; background: linear-gradient(90deg, #4F8BF9 0%, #6FC3F7 100%); color: #fff; font-size: 1.38rem; font-weight: 700; padding: 16px 40px; border-radius: 32px; margin-bottom: 24px; box-shadow: 0 2px 8px rgba(79,139,249,0.10); }

div[data-testid="stFileUploader"] button { color: #333 !important; font-weight: normal !important; border-radius: 8px !important; border: 1px solid #ddd !important; background: transparent !important; box-shadow: none !important; }
/* Make Run enrichment button gray to match Browse Files */
div.right-box button { background-color: #f5f5f5 !important; color: #333 !important; font-weight: normal !important; border-radius: 8px !important; border: 1px solid #ddd !important; box-shadow: none !important; }
/* Hide Streamlit file uploader help text and drag-and-drop area */
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
    random_sample = st.checkbox("Randomize which 100 rows are used", value=False, help="If checked, a random sample of up to 100 rows is used instead of the first 100.")

    if uploaded_file is not None:
        try:
            file_bytes = uploaded_file.getvalue()
            df_full = load_df_from_bytes(file_bytes, uploaded_file.name)
            if random_sample and len(df_full) > 100:
                df = df_full.sample(n=100, random_state=42).reset_index(drop=True)
            else:
                df = df_full.head(100).copy()

            st.session_state.df = df
            st.session_state.row_count = len(df)
            st.session_state.input_cols = list(df.columns)

            # Safe HTML for column list
            col_list = '<br>'.join(f"- {html.escape(str(c))}" for c in st.session_state.input_cols)
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
        except Exception as e:  # noqa: BLE001
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

        # Optional: show a dry-run preview of first 3 payloads
        show_preview = st.checkbox("Preview first 3 request payloads", value=False)
        if show_preview and input_mapping:
            preview_payloads = []
            for _, row in df_preview.head(3).iterrows():
                payload = build_params(row.to_dict(), input_mapping, selected_outputs=[])
                preview_payloads.append(payload)
            st.code(json.dumps(preview_payloads, indent=2), language="json")
    else:
        st.info("Please upload a file first.")

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Step 3 — Select Outputs (optional fields filter)
# ---------------------------
with col3:
    st.markdown("<div class='middle-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 3: Select Outputs</div>", unsafe_allow_html=True)
    st.markdown("<small style='color: #000;'>Choose output fields you want. These must map to PDL field names in the code to be sent via the 'fields' parameter. If no map is provided, all defaults may be returned by the API.</small>", unsafe_allow_html=True)

    OUTPUT_FIELDS = [
        "Full Name",
        "Birth Year",
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
        "Job Title",
        "Company or Employer",
    ]

    selected_outputs = []
    for field in OUTPUT_FIELDS:
        if st.checkbox(field, key=f"out_{field}"):
            selected_outputs.append(field)

    with st.expander("Developer note: Output mapping"):
        st.write(
            """
            The app only includes a `fields` parameter if the UI labels you selected are mapped in `UI_TO_API_OUTPUTS`.
            Review and edit that dictionary in the source to match your PDL account's schema. Unmapped labels are ignored for the
            `fields` filter (the request is sent without `fields`).
            """
        )

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Step 4 — Enrich
# ---------------------------
with col4:
    st.markdown("<div class='right-box'>", unsafe_allow_html=True)
    st.markdown("<div class='step-pill'>Step 4: Enrich</div>", unsafe_allow_html=True)
    st.markdown("<small style='color: #000;'>Click to run enrichment and download results.</small>", unsafe_allow_html=True)

    max_workers = st.slider("Max parallel requests", min_value=1, max_value=8, value=4, help="Tune based on your PDL rate limits.")
    run_enrich = st.button("✨ Run enrichment", key="enrich_btn")
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------
# Enrichment execution
# ---------------------------
if run_enrich:
    if st.session_state.df is None:
        st.error("Please upload a file first.")
    elif not input_mapping:
        st.error("Please map at least one input field before running.")
    else:
        df_to_enrich = st.session_state.df
        progress = st.progress(0.0)
        memo: Dict[Tuple, Dict] = {}

        def one(row_dict: Dict) -> Dict:
            params = build_params(row_dict, input_mapping, selected_outputs)
            if not params:
                return {"error": "No valid parameters built for this row."}
            key = _params_key(params)
            if key in memo:
                return memo[key]
            try:
                # Match the original usage pattern
                out = call_with_backoff(client.person_enrich, params)
                # People Data Labs clients often return {"data": {...}}, but not guaranteed; be safe:
                data = (out or {}).get("data", out or {})
            except Exception as e:  # noqa: BLE001
                data = {"error": str(e)}
            memo[key] = data
            return data

        results_map: Dict[int, Dict] = {}
        total = len(df_to_enrich)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [(idx, ex.submit(one, row.to_dict())) for idx, row in df_to_enrich.iterrows()]
            for i, (idx, fut) in enumerate(futures, 1):
                # consume in submission order to keep progress smooth
                try:
                    results_map[idx] = fut.result()
                except Exception as e:  # noqa: BLE001
                    results_map[idx] = {"error": str(e)}
                progress.progress(i / total)

        results_df = pd.DataFrame.from_dict(results_map, orient="index").reset_index(drop=True)
        st.success(f"Enrichment completed for {len(results_df)} rows!")
        stc.confetti()
        st.dataframe(results_df, use_container_width=True)

        # Downloads
        csv_bytes = results_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download Enriched CSV",
            data=csv_bytes,
            file_name="enriched_results.csv",
            mime="text/csv",
        )

        try:
            excel_buffer = io.BytesIO()
            results_df.to_excel(excel_buffer, index=False, engine="xlsxwriter")
            excel_bytes = excel_buffer.getvalue()
            st.download_button(
                "Download Enriched Excel",
                data=excel_bytes,
                file_name="enriched_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as e:  # noqa: BLE001
            st.warning(f"Excel export unavailable: {e}")

# ---------------------------
# Footer note
# ---------------------------
st.caption(
    "Tip: If you see many duplicate rows hitting the API, the app deduplicates identical request payloads in-memory during a run.\n"
    "Adjust UI_TO_API_INPUTS/UI_TO_API_OUTPUTS to your PDL schema before relying on 'fields' filtering."
)
