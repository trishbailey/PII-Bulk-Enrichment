import streamlit as st
import streamlit_confetti as stc
st.set_page_config(layout="wide")
import pandas as pd
from pdl_client import PDLClient
import os
import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
from pdl_client import PDLClient
import os

api_key = st.secrets.get("PDL_API_KEY") or os.getenv("PDL_API_KEY")
if not api_key:
	st.error("No PDL API key found. Add `PDL_API_KEY` to Streamlit Secrets or environment.")
	st.stop()
st.markdown("""
<style>
/* Reduce top margin above app title */
h1 {
	margin-top: 12px !important;
}
/* Darker blue boxes with white header text */
/* Remove blue background from step boxes */
.upload-box, .middle-box, .right-box {
	background: none;
	border-radius: 0;
	padding: 0;
	margin-bottom: 0;
}
/* Stylish pill-shaped background for step titles */
.step-pill {
	display: inline-block;
	background: linear-gradient(90deg, #4F8BF9 0%, #6FC3F7 100%);
	color: #fff;
				font-size: 1.06rem;
	font-weight: 700;
	padding: 16px 40px;
	border-radius: 32px;
	margin-bottom: 24px;
	box-shadow: 0 2px 8px rgba(79,139,249,0.10);
}
div[data-testid="stFileUploader"] button {
color: #333 !important;
font-weight: normal !important;
border-radius: 8px !important;
border: 1px solid #ddd !important;
background: transparent !important;
box-shadow: none !important;
}
/* Make Run enrichment button gray to match Browse Files */
div.right-box button {
	background-color: #f5f5f5 !important;
	color: #333 !important;
	font-weight: normal !important;
	border-radius: 8px !important;
	border: 1px solid #ddd !important;
	box-shadow: none !important;
}
/* Hide Streamlit file uploader help text and drag-and-drop area */
div[data-testid="stFileUploader"] div:nth-child(2),
div[data-testid="stFileUploader"] .uploadedFileInfo,
div[data-testid="stFileUploader"] .drag-and-drop-area {
display: none !important;
}

""", unsafe_allow_html=True)

st.markdown("<h1 style='text-align: center; color: #4F8BF9;'>PII Bulk Enrichment Tool</h1>", unsafe_allow_html=True)

client = PDLClient(api_key)


col1, col2, col3, col4 = st.columns([1.7, 2.1, 2.1, 1.7])

selected_inputs = []
selected_outputs = []

with col1:
	st.markdown("<div class='upload-box'>", unsafe_allow_html=True)
	st.markdown("<div class='step-pill'>Step 1: Upload</div>", unsafe_allow_html=True)
	uploaded_file = st.file_uploader("Upload your CSV or Excel file (max 100 rows)", type=["csv", "xlsx"])
	if uploaded_file:
		try:
			if uploaded_file.name.endswith(".csv"):
				df_preview = pd.read_csv(uploaded_file)
			else:
				df_preview = pd.read_excel(uploaded_file)
			row_count = len(df_preview)
			input_cols = list(df_preview.columns)
			col_list = '\n'.join([f"- {col}" for col in input_cols])
			st.success(f"File Upload Successful 🎉\n{row_count} rows detected\nColumns:\n{col_list}")
		except Exception as e:
			st.error(f"File upload failed: {str(e)}")
	st.markdown("</div>", unsafe_allow_html=True)

with col2:
	st.markdown("<div class='middle-box'>", unsafe_allow_html=True)
	st.markdown("<div class='step-pill'>Step 2: Select Inputs</div>", unsafe_allow_html=True)
	st.markdown("<small style='color: #000;'>Check all columns present in your uploaded file.</small>", unsafe_allow_html=True)
	input_categories = [
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
		"Social Media Account 3"
	]
	cols_in = st.columns(1)
	for cat in input_categories:
		if st.checkbox(cat, key=f"in_{cat}"):
			selected_inputs.append(cat)
	st.markdown("</div>", unsafe_allow_html=True)

with col3:
	st.markdown("<div class='middle-box'>", unsafe_allow_html=True)
	st.markdown("<div class='step-pill'>Step 3: Select Outputs</div>", unsafe_allow_html=True)
	st.markdown("<small style='color: #000;'>Check all output fields you want to receive from enrichment.</small>", unsafe_allow_html=True)
	output_fields = [
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
		"Company or Employer"
	]
	cols_out = st.columns(1)
	for field in output_fields:
		if st.checkbox(field, key=f"out_{field}"):
			selected_outputs.append(field)
	st.markdown("</div>", unsafe_allow_html=True)

with col4:
	st.markdown("<div class='right-box'>", unsafe_allow_html=True)
	st.markdown("<div class='step-pill'>Step 4: Enrich</div>", unsafe_allow_html=True)
	st.markdown("<small style='color: #000;'>Click to run enrichment and download results.</small>", unsafe_allow_html=True)
	run_enrich = st.button("✨ Run enrichment", key="enrich_btn")
	st.markdown("</div>", unsafe_allow_html=True)



if uploaded_file:
	if uploaded_file.name.endswith(".csv"):
		df = pd.read_csv(uploaded_file)
	else:
		df = pd.read_excel(uploaded_file)
	df = df.head(100)  # Limit to 100 rows

	if run_enrich:
		with st.spinner("Enriching data. Please wait..."):
			results = []
			for _, row in df.iterrows():
				params = {}
				for field in selected_inputs:
					if field in df.columns:
						val = row[field]
						if pd.notna(val):
							params[field] = val
				if selected_outputs:
					params["fields"] = selected_outputs
				try:
					response = client.person_enrich(params)
					results.append(response.get("data", {}))
				except Exception as e:
					results.append({"error": str(e)})
			results_df = pd.DataFrame(results)
			st.success(f"Enrichment completed for {len(results_df)} rows!")
			stc.confetti()
			st.dataframe(results_df)
			csv_bytes = results_df.to_csv(index=False).encode("utf-8")
			st.download_button("Download Enriched CSV", data=csv_bytes, file_name="enriched_results.csv", mime="text/csv")

			import io
			import pandas as pd
			excel_buffer = io.BytesIO()
			results_df.to_excel(excel_buffer, index=False, engine='xlsxwriter')
			excel_bytes = excel_buffer.getvalue()
			st.download_button("Download Enriched Excel", data=excel_bytes, file_name="enriched_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")



