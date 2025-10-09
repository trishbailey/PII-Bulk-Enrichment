import io
import json
import math
import os
from typing import List, Dict
from ast import literal_eval # ADDED: Required for parsing string representations of lists

import pandas as pd
import streamlit as st

# Assuming these files are in your project structure
from field_config import PDL_INPUT_PARAMS, COMMON_OUTPUT_FIELDS
from pdl_client import PDLClient

# -----------------------------------------------------------------------------
# Utility Functions for Data Cleaning
# -----------------------------------------------------------------------------

def join_all_emails(emails: List[Dict]) -> str | None:
    """Flattens a list of email dictionaries (from PDL) into a single, semicolon-separated string."""
    if not isinstance(emails, list):
        return None
    
    # Extract 'address' from each dictionary, filtering out non-dict items
    addrs = [e.get('address') for e in emails if isinstance(e, dict) and e.get('address')]
    
    # Dedupe while preserving order
    seen = set()
    out = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
            
    return "; ".join(out) if out else None


def join_all_phones(phones: List[str]) -> str | None:
    """Flattens a list of phone numbers (from PDL) into a single, semicolon-separated string."""
    if not isinstance(phones, list):
        return None
    
    # Dedupe strings while preserving order
    seen = set()
    out = []
    for p in phones:
        if isinstance(p, str) and p not in seen:
            seen.add(p)
            out.append(p)
            
    return "; ".join(out) if out else None

# -----------------------------------------------------------------------------
# Page config
# -----------------------------------------------------------------------------
st.set_page_config(page_title="PDL Bulk Enrichment", page_icon="🧭", layout="wide")

st.title("People Data Labs — Bulk Person Enrichment")
st.caption("Upload a CSV/XLSX, map your columns to PDL inputs, select output fields, and enrich in batches.")

# ---- API key
api_key = st.secrets.get("PDL_API_KEY") or os.getenv("PDL_API_KEY")
if not api_key:
    st.error("No PDL API key found. Add `PDL_API_KEY` to Streamlit Secrets or environment.")
    st.stop()

client = PDLClient(api_key)

# ---- File upload
uploaded = st.file_uploader("Upload .csv or .xlsx", type=["csv", "xlsx"])

if uploaded:
    # Read file safely
    if uploaded.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded)
    else:
        df = pd.read_excel(uploaded)

    st.subheader("Preview")
    st.dataframe(df.head(20), use_container_width=True)

    if df.empty:
        st.warning("Your file appears to be empty.")
        st.stop()

    # ---- Column mapping UI
    st.subheader("Map your columns → PDL input params")
    st.write("Only the parameters you map will be sent for each record. (Unmapped are ignored.)")

    # Build selectors for each PDL param; users can leave unmapped.
    colmap: Dict[str, str] = {}
    with st.expander("Mapping", expanded=True):
        for p in PDL_INPUT_PARAMS:
            options = ["(none)"] + list(df.columns)
            sel = st.selectbox(f"{p}", options, index=0, key=f"map_{p}")
            if sel != "(none)":
                colmap[p] = sel

    # ---- Output field selection
    st.subheader("Select output fields to include")
    # Added 'emails' and 'phone_numbers' to default list to ensure they are available for flattening
    default_fields = ["full_name","emails","phone_numbers","linkedin_url"]
    presets = st.multiselect("Quick-pick common fields", COMMON_OUTPUT_FIELDS, default=default_fields)
    freeform = st.text_input("Add additional field names (comma-separated)", value="")
    requested_fields = list(dict.fromkeys([*presets, *[f.strip() for f in freeform.split(",") if f.strip()]]))

    st.caption("Tip: You can return full objects like `profiles` or specific fields like `linkedin_url`.")

    # ---- Batching controls
    st.subheader("Batching & execution")
    batch_size = st.number_input("Batch size (max 100)", min_value=1, max_value=100, value=100, step=1)
    max_rows = st.number_input("Limit number of rows to process (0 = all)", min_value=0, value=0, step=1)

    # ---- Start enrichment
    if st.button("Run enrichment"):
        work_df = df if max_rows == 0 else df.head(int(max_rows))
        total = len(work_df)
        if total == 0:
            st.warning("Nothing to process.")
            st.stop()

        progress = st.progress(0)
        results: List[dict] = []
        errors: List[dict] = []

        # Build requests payloads in batches
        def row_to_params(row) -> dict:
            params = {}
            for pdl_param, colname in colmap.items():
                val = row.get(colname)
                # Handle list fields (e.g., profiles) if comma-separated in the file
                if pdl_param == "profiles" and isinstance(val, str):
                    params[pdl_param] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    if pd.notna(val):
                        params[pdl_param] = val
            # restrict fields to only what user asked (if any)
            if requested_fields:
                params["fields"] = requested_fields
            return {"params": params}

        # Build all requests
        request_objs = [row_to_params(row) for _, row in work_df.iterrows()]

        # Process in batches
        num_batches = math.ceil(len(request_objs) / batch_size)
        out_rows = []
        
        for i in range(num_batches):
            start = i * batch_size
            end = min(start + batch_size, len(request_objs))
            batch = request_objs[start:end]

            try:
                payload = client.bulk_enrich(batch)
            except Exception as e:
                errors.append({"batch_index": i, "error": str(e)})
                progress.progress(min(1.0, (i + 1) / num_batches))
                continue

            # Handle both response formats:
            if isinstance(payload, list):
                responses = payload
            elif isinstance(payload, dict) and "responses" in payload:
                responses = payload["responses"]
            else:
                st.warning(f"Unexpected response format for batch {i}: {type(payload)}")
                responses = []

            for item in responses:
                status = item.get("status")
                # Ensure data is a dictionary before trying to access keys
                data = item.get("data", {}) if isinstance(item.get("data", {}), dict) else {}
                out = {"status": status}

                # Flatten selected fields from the returned profile data
                for f in requested_fields:
                    out[f] = data.get(f)

                # Optionally extract job info if requested
                job = data.get("job", {}) or {}
                if "job_title" in requested_fields:
                    out["job_title"] = job.get("title")
                if "job_company_name" in requested_fields:
                    company = job.get("company", {}) or {}
                    out["job_company_name"] = company.get("name")
                if "job_company_website" in requested_fields:
                    company = job.get("company", {}) or {}
                    out["job_company_website"] = company.get("website")

                # Keep raw data for JSON download
                out["_raw"] = data
                out_rows.append(out)

            progress.progress(min(1.0, (i + 1) / num_batches))
        
        # ---------------------------------------------------------------------
        # Output results logic
        # ---------------------------------------------------------------------
        
        if out_rows:
            out_df = pd.DataFrame(out_rows)

            # -----------------------------------------------------------------
            # START: CRITICAL CORRECTION - FLATTEN EMAILS AND PHONES
            # -----------------------------------------------------------------
            # Only apply if the columns were requested and exist in the output
            if 'emails' in out_df.columns:
                # 1. Check if 'emails' is a string representation (from a prior export/reload). 
                # This step is often not needed in in-memory enrichment, but good for robustness.
                def _maybe_parse(val):
                    if isinstance(val, str) and val.strip().startswith('[') and val.strip().endswith(']'):
                        try:
                            # Use literal_eval to safely convert string list-of-dicts to actual list-of-dicts
                            return literal_eval(val)
                        except Exception:
                            return None
                    return val

                out_df['emails'] = out_df['emails'].apply(_maybe_parse)
                
                # 2. Flatten the list-of-dicts into the desired clean string
                out_df['emails_all'] = out_df['emails'].apply(join_all_emails).astype('string')
                out_df = out_df.drop(columns=['emails'])

            if 'phone_numbers' in out_df.columns:
                # Phone numbers often come back as a list of strings, so simple join works best.
                out_df['phone_numbers_all'] = out_df['phone_numbers'].apply(join_all_phones).astype('string')
                out_df = out_df.drop(columns=['phone_numbers'])
            
            # Identify columns to drop from the final clean CSV/Excel output (keep only the raw JSON)
            clean_df = out_df.drop(columns=["_raw"], errors="ignore")
            # -----------------------------------------------------------------
            # END: CRITICAL CORRECTION
            # -----------------------------------------------------------------


            st.success(f"Completed. Rows processed: {total}. Output rows: {len(out_rows)}.")
            st.dataframe(clean_df.head(50), use_container_width=True)

            # Download CSV (using the clean DataFrame)
            csv_bytes = clean_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV (Flattened)", data=csv_bytes, file_name="pdl_enriched_flattened.csv", mime="text/csv")
            
            # Download Excel (using the clean DataFrame)
            excel_buffer = io.BytesIO()
            clean_df.to_excel(excel_buffer, index=False, engine='xlsxwriter')
            st.download_button(
                label="Download Excel (Flattened)", 
                data=excel_buffer.getvalue(), 
                file_name="pdl_enriched_flattened.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # Optional: provide full JSON for advanced work (uses the original out_rows with raw data)
            raw_json = json.dumps(out_rows, indent=2, ensure_ascii=False)
            st.download_button("Download JSON (includes _raw)", data=raw_json.encode("utf-8"),
                               file_name="pdl_enriched_raw.json", mime="application/json")
        else:
            st.warning("No results returned.")

        if errors:
            with st.expander("Errors", expanded=False):
                st.write(pd.DataFrame(errors))
