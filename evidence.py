"""The evidence contract.

Every number the audit can print lives in an evidence file and carries its own
source. The rule the renderer enforces: a null value omits its section. Nothing
is estimated, interpolated, or invented, because these figures get quoted out
loud on a sales call.
"""

import json

REQUIRED = ("domain", "business_name", "generated_at")

# A section renders only if at least one of its dotted paths has a real value.
SECTION_REQUIREMENTS = {
    "traffic": ("traffic.monthly_organic_visits", "traffic.ranking_keyword_count",
                "traffic.traffic_value_usd"),
    "brand_split": ("brand_split.brand_pct", "brand_split.nonbrand_pct"),
    "position_buckets": ("position_buckets.1-3", "position_buckets.4-10",
                         "position_buckets.11-20", "position_buckets.21-50",
                         "position_buckets.51-100"),
    "money_keywords": ("money_keywords",),
    "backlinks": ("backlinks.referring_domains", "backlinks.total_backlinks",
                  "backlinks.authority", "backlinks.trust"),
    "paid": ("paid.estimated_monthly_spend_usd", "paid.paid_keywords",
             "paid.landing_pages"),
    "competitors": ("competitors",),
    "ai_visibility": ("ai_visibility.platforms",),
    "scorecard": ("scorecard.content_quality", "scorecard.authority",
                  "scorecard.user_experience", "scorecard.ai_visibility"),
    "technical": ("technical.ai_crawler_access", "technical.structured_data",
                  "technical.core_web_vitals"),
}


class EvidenceError(Exception):
    pass


class Evidence:
    def __init__(self, data):
        self.data = data

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def get(self, dotted):
        """Value at a dotted path. Unwraps {'value': x} metrics. None if absent."""
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        if isinstance(node, dict):
            if "value" not in node:
                return None
            node = node["value"]
        if isinstance(node, (list, tuple)) and not node:
            return None
        if isinstance(node, dict) and not node:
            return None
        return node

    def has(self, dotted):
        return self.get(dotted) is not None

    def present_sections(self):
        return {s for s, paths in SECTION_REQUIREMENTS.items()
                if any(self.has(p) for p in paths)}

    def validate(self):
        for key in REQUIRED:
            if not self.data.get(key):
                raise EvidenceError("required field missing or empty: %s" % key)
