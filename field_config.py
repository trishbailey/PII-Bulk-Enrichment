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
    "social_media_3", # Social Media Account 3
]

# Helpful preset output fields users often want back.
# (You can free-type field names in the UI too; these are just shortcuts.)
COMMON_OUTPUT_FIELDS = [
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

