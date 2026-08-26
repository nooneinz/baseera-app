from django import forms
from django.core.exceptions import ValidationError

from .models import CompanyStrategicProfile

# The 5 allowed strategic priority keys, in the fixed set the model validator enforces.
STRATEGIC_PRIORITY_CHOICES = [
    ("cash_preservation", "الحفاظ على السيولة النقدية / Cash Preservation"),
    ("growth", "النمو والتوسع / Growth"),
    ("profitability", "الربحية / Profitability"),
    ("cost_reduction", "خفض التكاليف / Cost Reduction"),
    ("long_term_stability", "الاستقرار طويل الأمد / Long-Term Stability"),
]


class CompanyStrategicProfileForm(forms.ModelForm):
    """
    Renders the strategic_priorities_ranking JSON field as 5 explicit rank
    selects (1 = highest priority ... 5 = lowest), which the browser and the
    server both validate as a permutation of the 5 fixed priority keys before
    they are assembled back into the ordered list the model expects.
    """
    priority_rank_1 = forms.ChoiceField(choices=STRATEGIC_PRIORITY_CHOICES, label="الأولوية رقم 1 (الأهم)")
    priority_rank_2 = forms.ChoiceField(choices=STRATEGIC_PRIORITY_CHOICES, label="الأولوية رقم 2")
    priority_rank_3 = forms.ChoiceField(choices=STRATEGIC_PRIORITY_CHOICES, label="الأولوية رقم 3")
    priority_rank_4 = forms.ChoiceField(choices=STRATEGIC_PRIORITY_CHOICES, label="الأولوية رقم 4")
    priority_rank_5 = forms.ChoiceField(choices=STRATEGIC_PRIORITY_CHOICES, label="الأولوية رقم 5 (الأقل)")

    class Meta:
        model = CompanyStrategicProfile
        fields = [
            "company_name", "sector", "size", "growth_stage",
            "risk_tolerance", "max_investment_limit", "cash_reserve_floor",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        if instance and instance.pk and instance.strategic_priorities_ranking:
            ranking = instance.strategic_priorities_ranking
            for i in range(1, 6):
                field_name = f"priority_rank_{i}"
                if len(ranking) >= i:
                    self.fields[field_name].initial = ranking[i - 1]

    def clean(self):
        cleaned_data = super().clean()
        ranking = [
            cleaned_data.get("priority_rank_1"),
            cleaned_data.get("priority_rank_2"),
            cleaned_data.get("priority_rank_3"),
            cleaned_data.get("priority_rank_4"),
            cleaned_data.get("priority_rank_5"),
        ]
        if None in ranking or "" in ranking:
            raise ValidationError("يجب تحديد ترتيب الأولويات الخمسة كاملة. / All 5 priority ranks are required.")
        if len(set(ranking)) != 5:
            raise ValidationError("لا يمكن تكرار نفس الأولوية أكثر من مرة في الترتيب. / Each priority may only be ranked once.")
        cleaned_data["strategic_priorities_ranking"] = ranking
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.strategic_priorities_ranking = self.cleaned_data["strategic_priorities_ranking"]
        if commit:
            instance.save()
        return instance
