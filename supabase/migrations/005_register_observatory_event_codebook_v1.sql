-- AI Empowerment Observatory
-- Stage 7B.1B: register the first agreed event-classification codebook.
-- Run AFTER 004_event_level_methodology_schema.sql.

update public.codebook_versions
set is_active = false
where version_name = 'news_empowerment_v1.0';

insert into public.codebook_versions (
  version_name,
  source_title,
  hierarchy,
  prompt_text,
  is_active
)
values (
  'observatory_event_v1.0',
  'AI Empowerment Observatory event-level methodology: narrative frame + human empowerment direction + distribution breadth',
  '{
    "unit_of_analysis": "unique_real_world_event",
    "headline_public_signals": [
      "narrative_frame",
      "empowerment_status",
      "distribution_breadth"
    ],
    "empowerment_dimensions_parallel": [
      "operational",
      "creative",
      "agentic",
      "normative"
    ],
    "dimension_hierarchy": false,
    "non_empowerment_is_residual": true,
    "article_volume_does_not_equal_event_weight": true,
    "ai_authority_separate_from_human_empowerment": true
  }'::jsonb,
  $CODEBOOK$
# AI Empowerment Observatory — Event Classification Codebook v1.0

## Purpose

The Observatory helps citizens and managers make sense of an overwhelming AI
news environment. Classify the DIRECTION OF THE REPORTED AI DEVELOPMENT and the
DIRECTION OF THE NEWS NARRATIVE. Do not classify people as "pro-AI" or "anti-AI".

The unit of analysis is one UNIQUE REAL-WORLD EVENT. Multiple articles about
the same event must not receive multiple weights in the country index.

Use only the evidence supplied for the event. Do not infer unsupported facts.

## 1. AI relevance

Set `ai_relevant = true` only when AI is central to the reported development:
artificial intelligence, generative AI, machine learning, foundation models,
automated decision systems, or closely related AI governance/infrastructure.

Set `ai_relevant = false` when AI is incidental, an unrelated abbreviation, or
not substantively part of the development.

If `ai_relevant = false`, the event does not enter the AI narrative indices.

## 2. Human empowerment status — the core outcome

Classify the event into exactly one:

- `expanding`
- `contracting`
- `mixed`
- `non_empowerment`
- `unclear`

Human empowerment concerns whether the reported development materially expands
or contracts people's capability, creativity, autonomy/control, rights,
protection, participation, or influence.

EXPANDING:
The development materially increases human capability, creativity, autonomy,
control, protection, participation, rights, or meaningful influence.

CONTRACTING:
The development materially reduces or constrains those things.

MIXED:
The same development contains meaningful expansion and contraction, including
cases where important gains and losses coexist across groups or dimensions.

NON_EMPOWERMENT:
The development is substantive AI news but does not establish a material human
empowerment implication. This is the residual category.

Typical examples:
- an AI company raises capital;
- a model benchmark improves;
- a product/model launches with no demonstrated human capability/control effect;
- a valuation changes;
- a data-centre or compute announcement reports capacity without a supported
  human empowerment consequence.

Do NOT force such stories into operational empowerment.

UNCLEAR:
The supplied evidence is insufficient to make a reliable empowerment judgment.

## 3. Four parallel empowerment dimensions

The four dimensions are PARALLEL explanatory tags. There is NO hierarchy.
An event may contain zero, one, or several dimensions.

If `empowerment_status = non_empowerment`, all four dimensions should normally
be absent.

OPERATIONAL:
Changes people's ability to understand, access, interact with, or use AI
effectively, or changes competence/productivity in carrying out familiar tasks.
Examples include AI literacy, accessibility, effective use, productivity,
analysis, and task support.

CREATIVE:
Changes people's ability to innovate, co-create, generate ideas, invent, design,
or produce genuinely new expressive or intellectual output.

AGENTIC:
Changes autonomy, control, ownership, meaningful choice, identity,
responsibility, or decision authority. Examples include human override,
automated consequential decisions, worker monitoring, displacement, control over
personal data, or control over creative work.

NORMATIVE:
Changes the ability to shape, enforce, contest, or benefit from ethical, legal,
professional, or societal rules and values: fairness, safety, privacy,
transparency, labour rights, intellectual-property rights, consultation,
oversight, appeal, regulation, standards, inclusion, or accountability.

For every dimension that is present, classify its direction:
`expanding`, `contracting`, `mixed`, or `unclear`.

You may identify one `dominant_dimension` only as a concise explanatory label
for the public interface. It does not override or suppress secondary dimensions.

## 4. AI narrative frame — communication, not human impact

Classify the dominant way the event is communicated:

OPPORTUNITY:
AI is predominantly framed as enabling growth, innovation, competitiveness,
capability, productivity, investment opportunity, access, or improvement.

THREAT:
AI is predominantly framed as creating risk, harm, displacement, loss,
insecurity, abuse, danger, or societal/organisational vulnerability.

CONTESTED:
Meaningful opportunity and threat frames coexist.

DESCRIPTIVE_NEUTRAL:
Primarily factual or technical reporting without a meaningful opportunity or
threat frame.

UNCLEAR:
The evidence does not support a reliable frame classification.

Narrative frame and empowerment status are independent.

Example:
"AI startup raises $5B" may be:
- narrative_frame = opportunity
- empowerment_status = non_empowerment
- distribution_breadth = concentrated

## 5. Distribution breadth

Classify how widely the DIRECT gains, protections, losses, or power changes
described by the event are distributed.

BROAD:
Direct consequences plausibly reach a large population or broadly available
rights, services, protections, infrastructure, or capabilities.

TARGETED:
Direct consequences concern a clearly defined group, profession, industry,
community, user segment, patient group, or organisational population.

CONCENTRATED:
The direct gain or increase in power primarily accrues to a small set of firms,
owners, founders, investors, executives, state institutions, or already-capable
actors.

UNCLEAR:
The available evidence does not establish the breadth of distribution.

Do not exclude investors, founders, shareholders, or executives because they are
human. Record their direct benefits, but classify the distribution accurately.

## 6. AI authority shift — separate signal

Classify whether AI itself receives more decision authority:

- `increasing`
- `decreasing`
- `unchanged`
- `unclear`

This is NOT the inverse of human empowerment. AI authority and human empowerment
may move in the same direction, opposite directions, or independently.

## 7. Practical topic

Choose the single best navigation topic:

- work_employment
- business_productivity
- consumer_services
- creativity_ip
- education_research
- healthcare
- government_regulation
- privacy_security
- infrastructure_investment
- other

Topic is for browsing and summaries. It is not part of the empowerment score.

## 8. Event country

Identify where the reported policy, deployment, incident, organisation, or
directly affected population is located.

Do NOT automatically equate Google News search market with event country.

## 9. Confidence and review

Return a confidence score from 0.00 to 1.00.

Set `requires_review = true` when:
- evidence is headline-only;
- event clustering is uncertain;
- AI relevance is ambiguous;
- empowerment_status is unclear;
- distribution breadth is unclear in a consequential event;
- important opportunity and threat cues conflict;
- event country is uncertain;
- confidence < 0.85;
- the reasoning depends on information not present in the supplied evidence.

## 10. Required structured output

Return one valid JSON object:

{
  "ai_relevant": true,
  "empowerment_status":
    "expanding | contracting | mixed | non_empowerment | unclear",
  "narrative_frame":
    "opportunity | threat | contested | descriptive_neutral | unclear",
  "distribution_breadth":
    "broad | targeted | concentrated | unclear",
  "dominant_dimension":
    "operational | creative | agentic | normative | null",
  "dimensions": {
    "operational": {
      "present": true,
      "direction": "expanding | contracting | mixed | unclear | null",
      "confidence": 0.00,
      "reasoning": ""
    },
    "creative": {
      "present": false,
      "direction": null,
      "confidence": 0.00,
      "reasoning": ""
    },
    "agentic": {
      "present": false,
      "direction": null,
      "confidence": 0.00,
      "reasoning": ""
    },
    "normative": {
      "present": false,
      "direction": null,
      "confidence": 0.00,
      "reasoning": ""
    }
  },
  "ai_authority_shift":
    "increasing | decreasing | unchanged | unclear",
  "topic":
    "work_employment | business_productivity | consumer_services | creativity_ip | education_research | healthcare | government_regulation | privacy_security | infrastructure_investment | other",
  "event_country_iso3": null,
  "content_basis":
    "headline_only | headline_and_snippet | article_summary | multiple_sources | full_text",
  "confidence": 0.00,
  "reasoning": "",
  "requires_review": true,
  "review_reason": ""
}

## 11. Interpretive discipline

- Do not infer public anxiety from news coverage. Classify the NEWS NARRATIVE.
- Do not treat investment, valuation, or technical progress as human empowerment
  without a supported human capability/control consequence.
- Do not treat productivity as automatically socially broad.
- Do not assume "human" means only workers or citizens; investors and founders
  are humans too. Distribution breadth handles concentration.
- Do not convert article volume into event importance.
- Do not infer that opportunity framing means empowerment expansion.
- Do not infer that threat framing means empowerment contraction.
$CODEBOOK$,
  true
)
on conflict (version_name)
do update set
  source_title = excluded.source_title,
  hierarchy = excluded.hierarchy,
  prompt_text = excluded.prompt_text,
  is_active = excluded.is_active;

update public.codebook_versions
set is_active = false
where version_name <> 'observatory_event_v1.0';

select
  version_name,
  is_active,
  created_at
from public.codebook_versions
order by created_at desc;
