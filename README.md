# PII-Bulk-Enrichment
Bulk data enrichment based on People Data Labs API

## Configuration

The app needs a People Data Labs API key. Set it as an environment variable:

```bash
export PDL_API_KEY="your-pdl-api-key"
streamlit run streamlit_app.py
```

Alternatively, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (gitignored) and fill it in. **Never commit a real key.**
