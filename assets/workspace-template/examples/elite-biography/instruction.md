# Inputs

## Subject

Provided via prompt at launch time.

## Research Goal

Build a structured biography and timeline with evidence-linked fields.

## Required Information

- Basic biographical details: birth year, place of birth (province/state, city/county), gender
- Party affiliation history with year ranges, if applicable
  - Record party roles as separate entries by position and date range; do not collapse multiple within-party positions into one long affiliation span
  - For each party experience: year range, party name, position title
  - Use a separate party experience entry for each distinct within-party role such as member, youth leader, secretary, whip, deputy leader, or party leader
- Education history (primary, secondary, tertiary, and post-secondary) and highest education attainment
  - For each education entry: year range, organization name, education level, major/field
- Occupation/career timeline with organizations, positions, and year ranges
  - For each role: year range, organization name, position title, employed/unemployed
- Family/relatives (if available): relation and name only
  - Trace relatives across at least two generations where evidence exists
  - Search broadly for upward and downward relations, including grandparents, parents, spouse, siblings, children, and grandchildren
  - Include as many supported relatives as can be identified; do not stop at only spouse or children if broader family information is available
- Death status and year range, if applicable
  - If no definitive information on death, assume the individual is still alive

## Search Requirements

- Confirm all information is about the target politician
- Summarize in English; prioritize official government sources, news media, organization and personal websites
- Use strategic keyword variations; capture precise year ranges to build a detailed chronological position list
- Wikipedia pages are acceptable but should be cross-verified with other sources

## Quality Requirements

- Ensure objectivity, completeness, and accuracy
- Politicians may have multiple roles in different careers/fields/positions, which should be filled as `Concurrent`
- Present a clear, chronological timeline that integrates both education and full career history
- Diligently identify and fill gaps, especially throughout the typical workforce age (18–65), while preserving genuinely unknown periods
- Career together with education history should be as complete as supported evidence permits; use `Unemployed` only when supported or required by the project rules

## Critical Fields

- full_name
- birth_date
- education_experiences
- occupation_experiences

## Project Context

### Source priority

1. Official government pages and office biographies
2. Parliament/congress pages
3. Credible institutions and major media
4. Personal websites and secondary databases

### Coding expectations

- Prefer precise dates (`YYYY.MM` or `YYYY.MM.DD`) when available.
- Keep timeline ordering chronological.
- Keep unknown values explicit (`NA`).
- Every populated critical field should map to provenance evidence.

## Codebook

The following fenced JSON object is the machine-extracted output contract.

```json
{
  "full_name": "...",
  "birth_date": "YYYY.MM.DD",
  "gender": "Male/Female/Other",
  "place_of_birth": "Province/State, City/County",
  "education_attainment": "High school / Bachelor / Master / Doctorate / Other",
  "party_experiences": [
    {
      "time_range": "YYYY.MM-YYYY.MM or YYYY.MM-Present",
      "party_name": "...",
      "position_title": "...",
      "notes": "Use a separate entry for each distinct role and date range."
    }
  ],
  "occupation_experiences": [
    {
      "time_range": "YYYY.MM-YYYY.MM",
      "organization_name": "...",
      "position_title": "...",
      "concurrent": "Yes/No",
      "organization_type": "Political / State / Private Sector / Liberal Professions / Third Sector / Unemployed / Other",
      "notes": "..."
    }
  ],
  "education_experiences": [
    {
      "time_range": "YYYY.MM-YYYY.MM",
      "organization_name": "...",
      "education_level": "High school / Bachelor / Master / Doctorate / Diploma / Certificate",
      "enrollment_status": "Full-time / Part-time",
      "major_or_field": "...",
      "concurrent": "Yes/No",
      "notes": "..."
    }
  ],
  "relatives": [
    {
      "relation": "supported family relation",
      "full_name": "...",
      "notes": "..."
    }
  ],
  "retired": "Yes/No",
  "retirement_date": "YYYY.MM / Not applicable",
  "deceased": "Yes/No",
  "death_date": "YYYY.MM / Not deceased"
}

```

