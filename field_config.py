# field_config.py

# Map common CSV headers to PDL Person Enrichment input params (left = PDL param)
# You can expand this list as needed (see PDL docs for available params).
PDL_INPUT_PARAMS = [
    "name",
    "first_name",
    "last_name",
    "middle_name",
    "location",
    "street_address",
    "locality",
    "region",
    "country",
    "postal_code",
    "company",
    "job_title",
    "email",
    "phone",
    "profile",        # single social url
    "profiles",       # list of social urls
]

# Helpful preset output fields users often want back.
# (You can free-type field names in the UI too; these are just shortcuts.)
COMMON_OUTPUT_FIELDS = [
    "full_name",
    "gender",
    "birth_year",
    "emails",
    "phone_numbers",
    "linkedin_url",
    "twitter_url",
    "github_url",
    "facebook_url",
    "profiles",       # entire profiles array
    "location_name",
    "industry",
    "job_title",
    "job_company_name",
    "job_company_website",
    "job_start_date",
    "job_last_updated",
]

